"""Background runner that polls the cockpit API and dispatches CrewAI crews."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_COCKPIT_URL = "http://localhost:8080"
DEFAULT_POLL_INTERVAL_S = 60.0
DEFAULT_REPO_ID = "drake-governance"
MAX_CONCURRENT = 3
MAX_PER_REPO = 1

RUNNER_DIR = Path(__file__).resolve().parent
RUNS_LOG = RUNNER_DIR / "runs.log"
HEARTBEAT_FILE = RUNNER_DIR / ".runner_heartbeat.json"
PID_FILE = RUNNER_DIR / ".runner.pid"

logger = logging.getLogger("crewai.runner")


@dataclass
class RunnerConfig:
    cockpit_url: str = DEFAULT_COCKPIT_URL
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S
    default_repo_id: str = DEFAULT_REPO_ID
    max_concurrent: int = MAX_CONCURRENT
    max_per_repo: int = MAX_PER_REPO
    runs_log_path: Path = field(default_factory=lambda: RUNS_LOG)
    heartbeat_path: Path = field(default_factory=lambda: HEARTBEAT_FILE)
    pid_path: Path = field(default_factory=lambda: PID_FILE)


@dataclass
class PollResult:
    polled_at: str
    ready_count: int = 0
    sync_count: int = 0
    dispatched: int = 0
    skipped: int = 0
    deferred: int = 0
    errors: int = 0


def resolve_crew_type(slice_meta: dict[str, Any], *, prior_crew_run: bool) -> str | None:
    """Resolve crew_type from slice metadata and prior run history."""
    if not slice_meta.get("automation_eligible", False):
        return None

    state = str(slice_meta.get("state") or "")
    group = str(slice_meta.get("group") or "")
    branch_posture = slice_meta.get("branch_posture") or {}
    merged_branch = branch_posture.get("merged_branch")

    if state == "running" and merged_branch:
        return "sync"

    if group == "technical":
        if state in ("ready", "shaped") and not prior_crew_run:
            return "refinement"
        return "audit"

    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CockpitClient:
    """Minimal HTTP client for cockpit progress, slice detail, and crew dispatch."""

    def __init__(self, base_url: str, *, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers: dict[str, str] = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = resp.read().decode("utf-8")
                if not payload:
                    return {}
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(path) from exc
            raise

    def get_progress(self) -> dict[str, Any]:
        return self._request("GET", "/cockpit/progress")

    def get_slice(self, slice_id: str, repo_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"repo_id": repo_id})
        return self._request("GET", f"/cockpit/slices/{slice_id}?{query}")

    def get_project_slices(self, repo_id: str) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/cockpit/projects/{repo_id}/slices")
        slices = payload.get("slices")
        return slices if isinstance(slices, list) else []

    def list_crewai_runs(self, repo_id: str | None = None) -> list[dict[str, Any]]:
        query = ""
        if repo_id:
            query = "?" + urllib.parse.urlencode({"repo_id": repo_id, "limit": 100})
        payload = self._request("GET", f"/crewai/runs{query}")
        runs = payload.get("runs")
        return runs if isinstance(runs, list) else []

    def dispatch(self, *, crew_type: str, slice_id: str, repo_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/crewai/dispatch",
            body={"crew_type": crew_type, "slice_id": slice_id, "repo_id": repo_id},
        )

    def find_repo_for_slice(
        self,
        slice_id: str,
        progress: dict[str, Any],
        default_repo_id: str,
    ) -> str:
        projects = progress.get("projects") or []
        repo_ids = [str(p.get("repo_id")) for p in projects if p.get("repo_id")]
        if not repo_ids:
            repo_ids = [default_repo_id]

        for repo_id in repo_ids:
            try:
                meta = self.get_slice(slice_id, repo_id)
            except FileNotFoundError:
                continue
            if meta.get("slice_id") == slice_id:
                return repo_id
        return default_repo_id

    def collect_sync_candidates(
        self,
        progress: dict[str, Any],
        default_repo_id: str,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        projects = progress.get("projects") or []
        repo_ids = [str(p.get("repo_id")) for p in projects if p.get("repo_id")]
        if not repo_ids:
            repo_ids = [default_repo_id]

        candidates: list[tuple[str, str, dict[str, Any]]] = []
        for repo_id in repo_ids:
            for slice_meta in self.get_project_slices(repo_id):
                if not slice_meta.get("automation_eligible", False):
                    continue
                if str(slice_meta.get("state") or "") != "running":
                    continue
                branch_posture = slice_meta.get("branch_posture") or {}
                if not branch_posture.get("merged_branch"):
                    continue
                slice_id = str(slice_meta.get("slice_id") or "")
                if slice_id:
                    candidates.append((slice_id, repo_id, slice_meta))
        return candidates


class Runner:
    """Poll cockpit progress and dispatch ready slices to CrewAI crews."""

    def __init__(
        self,
        client: CockpitClient,
        config: RunnerConfig | None = None,
        *,
        dispatch_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.client = client
        self.config = config or RunnerConfig()
        self._dispatch_fn = dispatch_fn or client.dispatch
        self._active_lock = threading.Lock()
        self._active_repos: dict[str, str] = {}
        self._poll_repo_counts: dict[str, int] = defaultdict(int)
        self._poll_dispatch_count = 0

    def _project_repo_ids(self, progress: dict[str, Any]) -> list[str]:
        projects = progress.get("projects") or []
        repo_ids = [str(p.get("repo_id")) for p in projects if p.get("repo_id")]
        return repo_ids or [self.config.default_repo_id]

    def _has_prior_crew_run(self, slice_id: str, repo_id: str) -> bool:
        try:
            runs = self.client.list_crewai_runs(repo_id=repo_id)
        except Exception:  # noqa: BLE001 — treat lookup failures as no prior run
            runs = []
        return any(str(run.get("slice_id") or "") == slice_id for run in runs)

    def _can_dispatch(self, repo_id: str) -> bool:
        with self._active_lock:
            if len(self._active_repos) >= self.config.max_concurrent:
                return False
            repo_active = sum(1 for active_repo in self._active_repos.values() if active_repo == repo_id)
            if repo_active >= self.config.max_per_repo:
                return False
            if self._poll_dispatch_count >= self.config.max_concurrent:
                return False
            if self._poll_repo_counts[repo_id] >= self.config.max_per_repo:
                return False
            return True

    def _record_poll_dispatch(self, repo_id: str) -> None:
        with self._active_lock:
            self._poll_dispatch_count += 1
            self._poll_repo_counts[repo_id] += 1

    def _mark_active(self, slice_id: str, repo_id: str) -> None:
        with self._active_lock:
            self._active_repos[slice_id] = repo_id

    def _mark_inactive(self, slice_id: str) -> None:
        with self._active_lock:
            self._active_repos.pop(slice_id, None)

    def _append_log(self, record: dict[str, Any]) -> None:
        self.config.runs_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True)
        with self.config.runs_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _collect_candidates(
        self,
        progress: dict[str, Any],
    ) -> tuple[list[tuple[str, str, dict[str, Any]]], int, int]:
        ready_ids = progress.get("ready_to_trigger_slice_ids") or []
        ready_candidates: list[tuple[str, str, dict[str, Any]]] = []

        for slice_id in ready_ids:
            repo_id = self.client.find_repo_for_slice(
                slice_id,
                progress,
                self.config.default_repo_id,
            )
            try:
                slice_meta = self.client.get_slice(slice_id, repo_id)
            except FileNotFoundError:
                logger.warning("slice %s not found in repo %s", slice_id, repo_id)
                continue
            ready_candidates.append((slice_id, repo_id, slice_meta))

        sync_candidates = self.client.collect_sync_candidates(progress, self.config.default_repo_id)

        seen: set[str] = set()
        merged: list[tuple[str, str, dict[str, Any]]] = []
        for slice_id, repo_id, meta in ready_candidates + sync_candidates:
            if slice_id in seen:
                continue
            seen.add(slice_id)
            merged.append((slice_id, repo_id, meta))

        return merged, len(ready_ids), len(sync_candidates)

    def poll_once(self) -> PollResult:
        polled_at = _utc_now_iso()
        result = PollResult(polled_at=polled_at)
        self._poll_repo_counts = defaultdict(int)
        self._poll_dispatch_count = 0

        try:
            progress = self.client.get_progress()
        except Exception as exc:  # noqa: BLE001
            result.errors += 1
            self._append_log(
                {
                    "timestamp": polled_at,
                    "event": "poll_error",
                    "status": "error",
                    "error": str(exc),
                }
            )
            return result

        candidates, ready_count, sync_count = self._collect_candidates(progress)
        result.ready_count = ready_count
        result.sync_count = sync_count

        self._append_log(
            {
                "timestamp": polled_at,
                "event": "poll",
                "status": "ok",
                "ready_to_trigger_count": ready_count,
                "sync_candidate_count": sync_count,
                "candidate_count": len(candidates),
            }
        )

        for slice_id, repo_id, slice_meta in candidates:
            if not slice_meta.get("automation_eligible", False):
                result.skipped += 1
                self._append_log(
                    {
                        "timestamp": _utc_now_iso(),
                        "event": "skip",
                        "status": "skipped",
                        "slice_id": slice_id,
                        "repo_id": repo_id,
                        "reason": "automation_eligible_false",
                    }
                )
                continue

            prior_run = self._has_prior_crew_run(slice_id, repo_id)
            crew_type = resolve_crew_type(slice_meta, prior_crew_run=prior_run)
            if crew_type is None:
                result.skipped += 1
                self._append_log(
                    {
                        "timestamp": _utc_now_iso(),
                        "event": "skip",
                        "status": "skipped",
                        "slice_id": slice_id,
                        "repo_id": repo_id,
                        "reason": "unresolved_crew_type",
                    }
                )
                continue

            if not self._can_dispatch(repo_id):
                result.deferred += 1
                self._append_log(
                    {
                        "timestamp": _utc_now_iso(),
                        "event": "defer",
                        "status": "deferred",
                        "slice_id": slice_id,
                        "repo_id": repo_id,
                        "crew_type": crew_type,
                        "reason": "fan_out_limit",
                    }
                )
                continue

            self._mark_active(slice_id, repo_id)
            self._record_poll_dispatch(repo_id)
            dispatch_started = _utc_now_iso()
            try:
                dispatch_result = self._dispatch_fn(
                    crew_type=crew_type,
                    slice_id=slice_id,
                    repo_id=repo_id,
                )
                result.dispatched += 1
                self._append_log(
                    {
                        "timestamp": _utc_now_iso(),
                        "event": "dispatch",
                        "status": str(dispatch_result.get("status") or "success"),
                        "slice_id": slice_id,
                        "repo_id": repo_id,
                        "crew_type": crew_type,
                        "run_id": dispatch_result.get("run_id"),
                        "started_at": dispatch_started,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                self._append_log(
                    {
                        "timestamp": _utc_now_iso(),
                        "event": "dispatch_error",
                        "status": "error",
                        "slice_id": slice_id,
                        "repo_id": repo_id,
                        "crew_type": crew_type,
                        "error": str(exc),
                        "started_at": dispatch_started,
                    }
                )
            finally:
                self._mark_inactive(slice_id)

        return result

    def write_heartbeat(self) -> None:
        payload = {
            "pid": os.getpid(),
            "last_poll_at": _utc_now_iso(),
            "status": "ok",
        }
        self.config.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.heartbeat_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def write_pid_file(self) -> None:
        self.config.pid_path.write_text(str(os.getpid()) + "\n", encoding="utf-8")

    def remove_pid_file(self) -> None:
        try:
            self.config.pid_path.unlink(missing_ok=True)
        except OSError:
            pass

    def run_forever(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self.write_pid_file()
        logger.info(
            "crewai runner starting (cockpit=%s interval=%ss)",
            self.config.cockpit_url,
            self.config.poll_interval_s,
        )
        try:
            while True:
                result = self.poll_once()
                self.write_heartbeat()
                logger.info(
                    "poll complete dispatched=%s skipped=%s deferred=%s errors=%s",
                    result.dispatched,
                    result.skipped,
                    result.deferred,
                    result.errors,
                )
                time.sleep(self.config.poll_interval_s)
        finally:
            self.remove_pid_file()


def _config_from_env() -> RunnerConfig:
    return RunnerConfig(
        cockpit_url=os.getenv("COCKPIT_API_URL", DEFAULT_COCKPIT_URL),
        poll_interval_s=float(os.getenv("CREWAI_RUNNER_POLL_INTERVAL_S", str(DEFAULT_POLL_INTERVAL_S))),
        default_repo_id=os.getenv("CREWAI_RUNNER_DEFAULT_REPO_ID", DEFAULT_REPO_ID),
        max_concurrent=int(os.getenv("CREWAI_RUNNER_MAX_CONCURRENT", str(MAX_CONCURRENT))),
        max_per_repo=int(os.getenv("CREWAI_RUNNER_MAX_PER_REPO", str(MAX_PER_REPO))),
        runs_log_path=Path(os.getenv("CREWAI_RUNNER_LOG_PATH", str(RUNS_LOG))),
        heartbeat_path=Path(os.getenv("CREWAI_RUNNER_HEARTBEAT_PATH", str(HEARTBEAT_FILE))),
        pid_path=Path(os.getenv("CREWAI_RUNNER_PID_PATH", str(PID_FILE))),
    )


def main() -> int:
    config = _config_from_env()
    client = CockpitClient(config.cockpit_url)
    runner = Runner(client, config)
    try:
        runner.run_forever()
    except KeyboardInterrupt:
        logger.info("crewai runner stopped")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
