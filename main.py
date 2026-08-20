"""
Cockpit API — Read-model projections over canonical planning data.

Data source: PLANNING_CHECKOUT_PATH (git-synced drake-governance checkout).
Reads registry, namespace trees, operator_questions.json.
Never writes to Git directly — dispatch confirm enqueues runner jobs.

Run:
  PLANNING_CHECKOUT_PATH=/data/planning/drake-governance uvicorn main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import jsonschema
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

app = FastAPI(title="Cockpit API", version="0.1.0")

_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "COCKPIT_CORS_ORIGINS",
        "http://localhost:8081,http://127.0.0.1:8081",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

PLANNING_PATH = Path(os.getenv("PLANNING_CHECKOUT_PATH", "/data/planning/drake-governance"))
QUEUE_PATH = Path(os.getenv("QUEUE_PATH", "/data/cockpit/queue.json"))
SOURCES_PATH = Path(os.getenv("SOURCES_PATH", "/data/cockpit/sources.json"))
RUNS_PATH = Path(os.getenv("RUNS_PATH", "/data/cockpit/runs"))

SAFE_RUN_ID = re.compile(r"^[a-zA-Z0-9._-]+$")
DEFAULT_EXPECTED_ENV_VARS = ("DEEPSEEK_API_KEY",)
SECRET_FIELD_NAMES = {
    "gh_token",
    "github_token",
    "api_key",
    "api_token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "private_key",
    "client_secret",
    "webhook_secret",
    "bearer_token",
}


# ── Helpers ──────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> dict:
    return _load_json(PLANNING_PATH / ".docs/projects-registry.json")


def _dispatch_webhook_token_resolvable(repo_id: str) -> str | None:
    """Return webhook token env var name when resolvable from env or broker."""
    import sys

    scripts_root = Path(__file__).resolve().parent / "scripts"
    if str(scripts_root) not in sys.path:
        sys.path.insert(0, str(scripts_root))
    from projects_registry import get_primary_worker  # noqa: E402
    from credential_broker.broker import get_broker  # noqa: E402

    project = next(
        (item for item in _registry().get("projects", []) if item.get("id") == repo_id),
        None,
    )
    if project is None:
        return None
    worker = get_primary_worker(project)
    if worker is None:
        return None
    token_name = (worker.get("webhook_env") or {}).get("token")
    if not isinstance(token_name, str) or not token_name:
        return None
    if os.environ.get(token_name):
        return token_name
    broker = get_broker()
    if broker is not None and broker.is_resolvable(token_name):
        return token_name
    return None


def _normalize_repo_id(repo_id: str) -> str:
    """Strip GitHub org prefix so 'sv-copilot/drake-governance' → 'drake-governance'."""
    return repo_id.split("/")[-1] if "/" in repo_id else repo_id


def _tree(repo_id: str) -> dict:
    repo_id = _normalize_repo_id(repo_id)
    tree_path = PLANNING_PATH / f".docs/planning/projects/{repo_id}/slice_dependency_tree.json"
    if not tree_path.exists():
        raise HTTPException(404, f"No namespace tree for {repo_id}")
    return _load_json(tree_path)


def _registry_project(repo_id: str) -> dict | None:
    """Return the registry entry for a repo_id (normalized), or None."""
    repo_id = _normalize_repo_id(repo_id)
    return next(
        (item for item in _registry().get("projects", []) if item.get("id") == repo_id),
        None,
    )


def _resolve_integration_branch(project: dict | None) -> str:
    """Resolve a project's integration branch from its registry entry."""
    return (project or {}).get("integration_branch") or "dev"


def _planning_branch() -> str:
    """Default planning checkout branch for the admin sync cron."""
    return os.getenv("PLANNING_BRANCH", "dev")


def _resolve_worker_routing(project: dict | None) -> dict:
    """Resolve dispatch worker routing from the project's primary worker.

    Falls back to the legacy hardcoded values when no registry entry/worker
    exists, so the dry-run preview never crashes on an unregistered repo.
    """
    worker: dict | None = None
    if project is not None:
        try:
            import sys

            scripts_root = Path(__file__).resolve().parent / "scripts"
            if str(scripts_root) not in sys.path:
                sys.path.insert(0, str(scripts_root))
            from projects_registry import get_primary_worker  # noqa: E402

            worker = get_primary_worker(project)
        except Exception:
            worker = None
    webhook_env = (worker or {}).get("webhook_env") or {}
    return {
        "adapter_type": (worker or {}).get("adapter_type") or "cline",
        "worker_id": (worker or {}).get("worker_id") or "self-hosted-cline-runner",
        "webhook_env_name": webhook_env.get("url") or "COCKPIT_API_URL",
    }

def _expected_env_report() -> dict[str, str]:
    """Presence-only map of expected runtime env vars — names only, never values.

    The expected list is config-driven via the comma-separated
    ``EXPECTED_ENV_VARS`` env var, defaulting to ``DEEPSEEK_API_KEY``. Values are
    reported as ``present``/``absent``; the secret values themselves are never
    included in the payload.
    """
    raw = os.getenv("EXPECTED_ENV_VARS")
    names = (
        [name.strip() for name in raw.split(",") if name.strip()]
        if raw
        else list(DEFAULT_EXPECTED_ENV_VARS)
    )
    return {name: "present" if name in os.environ else "absent" for name in names}


def _sync_age() -> int:
    """Seconds since last git-sync pull."""
    head_path = PLANNING_PATH / ".git" / "FETCH_HEAD"
    if not head_path.exists():
        return 99999
    return int(time.time() - head_path.stat().st_mtime)


def _slice_counts(slices: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in slices:
        state = s.get("state", "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _slice_counts_by_tier(slices: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in slices:
        tier = s.get("tier", "unknown")
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def _promotion_gaps(slices: list[dict]) -> list[dict]:
    gaps = []
    for s in slices:
        bp = s.get("branch_posture", {})
        if s.get("state") == "validated" and bp.get("merged_branch") and not bp.get("promoted_branch"):
            gaps.append({
                "slice_id": s["slice_id"],
                "slice_number": s["slice_number"],
                "title": s.get("title", ""),
                "implementation_repo_id": bp.get("implementation_repo_id", "unknown"),
                "merged_branch": bp.get("merged_branch"),
                "promoted_branch": bp.get("promoted_branch"),
                "last_known_pr_url": f"https://github.com/sv-copilot/{bp.get('implementation_repo_id', '')}/pull/{s['last_known_pr']}" if s.get("last_known_pr") else None,
            })
    return gaps


def _branch_posture_summary(slices: list[dict]) -> dict[str, int]:
    """Roll up merged_branch/promoted_branch counts per branch for active slices."""
    summary: dict[str, int] = {}
    for s in slices:
        bp = s.get("branch_posture") or {}
        for key in ("merged_branch", "promoted_branch"):
            branch = bp.get(key)
            if branch:
                summary[str(branch)] = summary.get(str(branch), 0) + 1
    return summary


def _load_queue() -> dict:
    if not QUEUE_PATH.exists():
        return {"jobs": []}
    return _load_json(QUEUE_PATH)


def _save_queue(queue: dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")


def _load_sources() -> list[dict]:
    if not SOURCES_PATH.exists():
        return []
    data = _load_json(SOURCES_PATH)
    return data.get("sources", []) if isinstance(data, dict) else []


def _save_sources(sources: list[dict]) -> None:
    SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_PATH.write_text(json.dumps({"sources": sources}, indent=2) + "\n", encoding="utf-8")


def _ensure_runs_dir() -> None:
    try:
        RUNS_PATH.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # Non-fatal on local dev where /data may not exist


_ensure_runs_dir()


def _evidence_schema_path() -> Path:
    return PLANNING_PATH / "adapters" / "evidence-contract.schema.json"


def _evidence_validator() -> jsonschema.Draft7Validator:
    adapters_dir = PLANNING_PATH / "adapters"
    schema = _load_json(_evidence_schema_path())
    validation_results = _load_json(adapters_dir / "validation-results.schema.json")
    resolver = jsonschema.RefResolver(
        base_uri=schema["$id"],
        referrer=schema,
        store={
            "validation-results.schema.json": validation_results,
            validation_results.get("$id", ""): validation_results,
        },
    )
    return jsonschema.Draft7Validator(schema, resolver=resolver)


def _format_validation_errors(error: jsonschema.ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for sub in sorted(error.context, key=lambda e: list(e.path)):
        errors.append({
            "field": ".".join(str(p) for p in sub.path) or "root",
            "message": sub.message,
        })
    if not errors:
        errors.append({
            "field": ".".join(str(p) for p in error.path) or "root",
            "message": error.message,
        })
    return errors


def _validate_evidence_payload(payload: dict[str, Any]) -> None:
    validator = _evidence_validator()
    try:
        validator.validate(payload)
    except jsonschema.ValidationError as exc:
        raise HTTPException(status_code=422, detail=_format_validation_errors(exc)) from exc


def _contains_secret_values(payload: dict[str, Any]) -> Optional[str]:
    for key, value in payload.items():
        normalized = key.lower().replace("-", "_")
        if normalized in SECRET_FIELD_NAMES and isinstance(value, str) and value.strip():
            return key
        if isinstance(value, dict):
            nested = _contains_secret_values(value)
            if nested:
                return f"{key}.{nested}"
    return None


def _run_id_from_payload(payload: dict[str, Any]) -> str:
    run_id = payload.get("run_id") or payload.get("task_id")
    if not run_id or not isinstance(run_id, str):
        raise HTTPException(status_code=422, detail="run_id or task_id is required")
    if not SAFE_RUN_ID.match(run_id):
        raise HTTPException(status_code=422, detail="run_id contains unsafe characters")
    return run_id


def _run_sort_timestamp(payload: dict[str, Any]) -> str:
    timestamps = payload.get("timestamps") if isinstance(payload.get("timestamps"), dict) else {}
    for key in ("completed_at", "started_at"):
        value = payload.get(key) or timestamps.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _load_run_file(path: Path) -> dict[str, Any]:
    return _load_json(path)


def _list_run_records(
    *,
    repo_id: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not RUNS_PATH.exists():
        return []

    runs: list[dict[str, Any]] = []
    for path in RUNS_PATH.glob("*.json"):
        try:
            record = _load_run_file(path)
        except (json.JSONDecodeError, OSError):
            continue
        if repo_id and record.get("repo_id") != repo_id:
            continue
        runs.append(record)

    runs.sort(key=_run_sort_timestamp, reverse=True)
    return runs[:limit]


def _save_run_record(run_id: str, payload: dict[str, Any]) -> None:
    _ensure_runs_dir()
    path = RUNS_PATH / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# ── Preflight Refinement Gate (#200) ────────────────────────────────────

def _parse_slice_detail_doc(repo_id: str, slice_id: str) -> dict:
    """Parse a slice detail markdown doc to extract file paths, functions, URLs."""
    doc_path = PLANNING_PATH / f".docs/planning/projects/{repo_id}/slices/{slice_id}.md"
    if not doc_path.exists():
        return {"file_paths": [], "function_names": [], "urls": [], "error": "doc_not_found"}

    content = doc_path.read_text(encoding="utf-8")

    # Extract file paths: backtick-wrapped paths with extensions
    file_paths = list(set(re.findall(r'`([a-zA-Z0-9_/.\-]+\.[a-zA-Z]+)`', content)))

    # Extract function names: backtick-wrapped functions with ()
    function_names = list(set(re.findall(r'`([a-z_]+\(\))`', content)))

    # Extract URLs
    urls = list(set(re.findall(r'https?://[^\s\)]+', content)))

    return {
        "file_paths": sorted(file_paths),
        "function_names": sorted(function_names),
        "urls": sorted(urls),
    }


def _run_preflight_checks(repo_id: str, slice_id: str) -> dict:
    """Run pre-flight refinement checks for a slice before dispatch.

    Checks:
    1. Dependency state — are all deps validated?
    2. File staleness — git log on referenced files
    3. Endpoint liveness — HEAD request for referenced URLs
    """
    checks: dict[str, Any] = {}
    warnings: list[str] = []
    blocks: list[str] = []

    # Load the planning tree
    tree = _tree(repo_id)
    slices = tree.get("slices", [])
    slice_entry = None
    for s in slices:
        if s["slice_id"] == slice_id:
            slice_entry = s
            break

    if not slice_entry:
        return {"status": "block", "checks": {"error": "slice_not_found"}}

    # Check 1: Dependency state — verify all deps are validated
    deps = slice_entry.get("dependencies", [])
    dep_statuses: list[dict] = []
    all_valid = True
    for dep_num in deps:
        dep_slice = None
        for s in slices:
            if s.get("slice_number") == dep_num:
                dep_slice = s
                break
        dep_state = dep_slice.get("state", "unknown") if dep_slice else "unknown"
        dep_statuses.append({"slice_number": dep_num, "state": dep_state})
        if dep_state not in ("validated", "promoted", "released", "done"):
            all_valid = False

    dep_valid_count = sum(
        1 for d in dep_statuses
        if d["state"] in ("validated", "promoted", "released", "done")
    )
    checks["dependencies_valid"] = {
        "status": "pass" if all_valid else "block",
        "details": f"{dep_valid_count}/{len(dep_statuses)} dependencies validated"
        if dep_statuses
        else "No dependencies",
        "deps": dep_statuses,
    }
    if not all_valid:
        unmet = len(dep_statuses) - dep_valid_count
        blocks.append(f"{unmet} dependencies not yet validated")

    # Parse the detail doc
    parsed = _parse_slice_detail_doc(repo_id, slice_id)

    # Check 2: File staleness — git log on referenced files
    if parsed.get("error") == "doc_not_found":
        checks["file_staleness"] = {
            "status": "pass",
            "details": "No detail doc found — skipping staleness check",
        }
    elif parsed.get("file_paths"):
        changed_files: list[dict] = []
        for file_path in parsed["file_paths"]:
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "-3", "--", file_path],
                    capture_output=True, text=True, timeout=10,
                    cwd=str(PLANNING_PATH),
                )
                if result.returncode == 0 and result.stdout.strip():
                    changed_files.append({
                        "path": file_path,
                        "recent_commits": result.stdout.strip().split("\n"),
                    })
            except (subprocess.TimeoutExpired, OSError):
                pass

        staleness = "warn" if changed_files else "pass"
        checks["file_staleness"] = {
            "status": staleness,
            "details": (
                f"{len(changed_files)}/{len(parsed['file_paths'])} referenced files "
                f"have recent commits"
            )
            if changed_files
            else f"No recent changes in {len(parsed['file_paths'])} referenced files",
            "changed_files": changed_files,
        }
        if changed_files:
            warnings.append(
                f"{len(changed_files)} referenced files changed since slice was "
                f"shaped — may need re-refinement"
            )
    else:
        checks["file_staleness"] = {
            "status": "pass",
            "details": "No file paths referenced in detail doc",
        }

    # Check 3: Endpoint liveness — HEAD request on referenced URLs
    if parsed.get("urls"):
        endpoint_results: list[dict] = []
        for url in parsed["urls"]:
            try:
                req = Request(url, method="HEAD")
                resp = urlopen(req, timeout=5)
                endpoint_results.append({"url": url, "status": resp.status})
            except Exception as exc:
                endpoint_results.append({
                    "url": url,
                    "status": "error",
                    "error": str(exc)[:100],
                })

        all_live = all(r.get("status") == 200 for r in endpoint_results)
        live_count = sum(1 for r in endpoint_results if r.get("status") == 200)
        checks["endpoint_liveness"] = {
            "status": "pass" if all_live else "warn",
            "details": f"{live_count}/{len(endpoint_results)} endpoints reachable",
            "endpoints": endpoint_results,
        }
        if not all_live:
            dead = len(endpoint_results) - live_count
            warnings.append(f"{dead} endpoints unreachable")
    else:
        checks["endpoint_liveness"] = {
            "status": "pass",
            "details": "No URLs referenced in detail doc",
        }

    # Determine overall status
    if blocks:
        status = "block"
    elif warnings:
        status = "warn"
    else:
        status = "pass"

    recommendation = (
        "; ".join(blocks + warnings)
        if (blocks or warnings)
        else "All preflight checks passed. Safe to proceed."
    )

    return {
        "status": status,
        "checks": checks,
        "recommendation": recommendation,
    }


# ── Models (matching operator_cockpit_read_models.md) ────────────────────

class PromotionGap(BaseModel):
    slice_id: str
    slice_number: int
    title: str
    implementation_repo_id: str
    merged_branch: Optional[str] = None
    promoted_branch: Optional[str] = None
    last_known_pr_url: Optional[str] = None


class ProjectRollup(BaseModel):
    repo_id: str
    github_slug: str
    slice_counts_by_state: dict[str, int]
    branch_posture_summary: dict[str, int]
    promotion_gap_count: int
    open_gate_count: int
    last_run_at: Optional[str] = None
    last_pr_url: Optional[str] = None


class RunSummary(BaseModel):
    run_id: str
    slice_id: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    pr_url: Optional[str] = None
    exit_code: Optional[int] = None


class CockpitProgressSummary(BaseModel):
    generated_at: str
    sync_age_seconds: int
    portfolio: dict
    promotion_gaps: list[PromotionGap]
    projects: list[ProjectRollup]
    ready_to_trigger_slice_ids: list[str]
    recent_runs: list[RunSummary] = []


class TriggerPreview(BaseModel):
    slice_id: str
    slice_number: int
    title: str
    implementation_repo_id: str
    implementation_github_slug: str
    integration_branch: str
    dependency_tree_path: str
    worker_routing: dict
    dry_run_task_packet: dict
    estimated_effort: str
    risk: str
    gates: list[str]
    warnings: list[str]
    preflight_refinement: Optional[dict] = None


class DispatchRequest(BaseModel):
    repo_id: str
    slice_id: str


class DispatchResponse(BaseModel):
    status: str
    job_id: str
    slice_id: str


# ── Source Registry Models ──────────────────────────────────────────────

class SourceType(str, Enum):
    career_page = "career_page"
    job_board = "job_board"
    rss = "rss"
    custom = "custom"


def _validate_source_url(url: str) -> str:
    normalized = url.strip()
    if not normalized:
        raise ValueError("url is required")
    parsed = urlparse(normalized)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must use http or https scheme")
    if not parsed.netloc:
        raise ValueError("url must include a host")
    return normalized


class SourceCreate(BaseModel):
    url: str
    label: str
    source_type: SourceType = SourceType.career_page
    enabled: bool = True
    interval_minutes: int = 360  # default: poll every 6 hours
    notes: str = ""

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_source_url(value)


class SourcePatch(BaseModel):
    enabled: Optional[bool] = None
    label: Optional[str] = None
    url: Optional[str] = None
    interval_minutes: Optional[int] = None
    last_polled_at: Optional[str] = None
    last_status: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _validate_source_url(value)


class SourceRecord(SourceCreate):
    id: str
    created_at: str
    last_polled_at: Optional[str] = None
    last_status: Optional[str] = None


def _recent_run_summaries(limit: int = 5) -> list[RunSummary]:
    summaries: list[RunSummary] = []
    for record in _list_run_records(limit=limit):
        timestamps = record.get("timestamps") if isinstance(record.get("timestamps"), dict) else {}
        pr_info = record.get("pr_info") if isinstance(record.get("pr_info"), dict) else {}
        run_id = record.get("run_id") or record.get("task_id")
        if not isinstance(run_id, str):
            continue
        summaries.append(RunSummary(
            run_id=run_id,
            slice_id=str(record.get("slice_id") or record.get("task_id") or ""),
            status=str(record.get("status") or "unknown"),
            started_at=record.get("started_at") or timestamps.get("started_at"),
            completed_at=record.get("completed_at") or timestamps.get("completed_at"),
            pr_url=record.get("pr_url") or pr_info.get("pr_url"),
            exit_code=record.get("exit_code"),
        ))
    return summaries


# ── Endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "planning_path": str(PLANNING_PATH),
        "sync_age_seconds": _sync_age(),
        "expected_env": _expected_env_report(),
    }


@app.get("/cockpit/progress", response_model=CockpitProgressSummary)
def cockpit_progress():
    registry = _registry()
    projects_data = registry.get("projects", [])
    all_slices: list[dict] = []
    project_rollups: list[ProjectRollup] = []
    ready_to_trigger: list[str] = []

    for proj in projects_data:
        repo_id = proj["id"]
        try:
            tree = _tree(repo_id)
        except HTTPException:
            continue

        slices = tree.get("slices", [])
        all_slices.extend(slices)

        # Ready-to-trigger slices
        by_number = {s["slice_number"]: s for s in slices}
        for s in slices:
            state = s.get("state", "")
            if state == "ready" and s.get("automation_eligible", False) and not s.get("operator_gates"):
                deps_met = all(by_number.get(d, {}).get("state") in ("validated", "promoted", "released", "done") for d in s.get("dependencies", []))
                if deps_met:
                    ready_to_trigger.append(s["slice_id"])

        open_gates = sum(1 for s in slices if s.get("operator_gates"))

        project_rollups.append(ProjectRollup(
            repo_id=repo_id,
            github_slug=proj.get("github_slug", ""),
            slice_counts_by_state=_slice_counts(slices),
            branch_posture_summary=_branch_posture_summary(slices),
            promotion_gap_count=len(_promotion_gaps(slices)),
            open_gate_count=open_gates,
        ))

    return CockpitProgressSummary(
        generated_at=datetime.now(timezone.utc).isoformat(),
        sync_age_seconds=_sync_age(),
        portfolio={
            "repo_count": len(project_rollups),
            "slice_counts_by_state": _slice_counts(all_slices),
            "slice_counts_by_tier": _slice_counts_by_tier(all_slices),
            "automation_eligible_ready_count": len(ready_to_trigger),
            "open_question_count": 0,  # stub — read operator_questions.json
            "blocked_by_questions_count": 0,
        },
        promotion_gaps=[PromotionGap(**g) for g in _promotion_gaps(all_slices)],
        projects=project_rollups,
        ready_to_trigger_slice_ids=ready_to_trigger,
        recent_runs=_recent_run_summaries(),
    )


@app.get("/cockpit/projects/{repo_id}/slices")
def cockpit_project_slices_legacy(repo_id: str):
    """Legacy route — prefer GET /cockpit/projects/slices?repo_id=..."""
    return _cockpit_project_slices(repo_id)


@app.get("/cockpit/projects/slices")
def cockpit_project_slices(repo_id: str = Query(...)):
    """List all slices for a project. Accepts both short names and GitHub slugs."""
    return _cockpit_project_slices(repo_id)


def _cockpit_project_slices(repo_id: str) -> dict:
    repo_id = _normalize_repo_id(repo_id)
    tree = _tree(repo_id)
    slices = tree.get("slices", [])
    return {"repo_id": repo_id, "slices": slices}


@app.get("/cockpit/slices/{slice_id}")
def cockpit_slice_detail(slice_id: str, repo_id: str = Query(...)):
    repo_id = _normalize_repo_id(repo_id)
    tree = _tree(repo_id)
    for s in tree.get("slices", []):
        if s["slice_id"] == slice_id:
            return s
    raise HTTPException(404, f"Slice {slice_id} not found in {repo_id}")


# ── Source Registry Endpoints ───────────────────────────────────────────

@app.get("/api/v1/sources")
def list_sources():
    return {"sources": _load_sources()}


@app.post("/api/v1/sources", status_code=201)
def create_source(body: SourceCreate):
    sources = _load_sources()
    now = datetime.now(timezone.utc).isoformat()
    record = body.model_dump()
    record["id"] = str(uuid.uuid4())[:8]
    record["created_at"] = now
    record["last_polled_at"] = None
    record["last_status"] = None
    sources.append(record)
    _save_sources(sources)
    return record


@app.delete("/api/v1/sources/{source_id}")
def delete_source(source_id: str):
    sources = _load_sources()
    new_sources = [s for s in sources if s["id"] != source_id]
    if len(new_sources) == len(sources):
        raise HTTPException(404, f"Source {source_id} not found")
    _save_sources(new_sources)
    return {"status": "deleted", "id": source_id}


@app.patch("/api/v1/sources/{source_id}")
def patch_source(source_id: str, body: SourcePatch):
    sources = _load_sources()
    updates = body.model_dump(exclude_unset=True)
    for s in sources:
        if s["id"] == source_id:
            s.update(updates)
            _save_sources(sources)
            return s
    raise HTTPException(404, f"Source {source_id} not found")


@app.post("/dispatch/dry-run", response_model=TriggerPreview)
def dispatch_dry_run(req: DispatchRequest):
    repo_id = _normalize_repo_id(req.repo_id)
    project = _registry_project(repo_id)
    integration_branch = _resolve_integration_branch(project)
    worker_routing = _resolve_worker_routing(project)
    tree = _tree(repo_id)
    for s in tree.get("slices", []):
        if s["slice_id"] == req.slice_id:
            warnings = []
            if not s.get("automation_eligible"):
                warnings.append("Slice is not automation_eligible")
            if s.get("operator_gates"):
                warnings.append(f"Slice has {len(s['operator_gates'])} operator gate(s)")

            # Run preflight refinement gate (#200)
            preflight: Optional[dict] = None
            try:
                preflight = _run_preflight_checks(repo_id, req.slice_id)
                if preflight["status"] == "block":
                    return TriggerPreview(
                        slice_id=s["slice_id"],
                        slice_number=s["slice_number"],
                        title=s.get("title", ""),
                        implementation_repo_id=repo_id,
                        implementation_github_slug=f"sv-copilot/{repo_id}",
                        integration_branch=integration_branch,
                        dependency_tree_path=f".docs/planning/projects/{repo_id}/slice_dependency_tree.json",
                        worker_routing=worker_routing,
                        dry_run_task_packet={
                            "task_type": "implement_slice",
                            "adapter_type": "cline",
                            "slice_id": req.slice_id,
                            "planning_repo_id": "drake-governance",
                            "implementation_repo_id": repo_id,
                            "slice_detail_path": f".docs/planning/projects/{repo_id}/slices/{req.slice_id}.md",
                        },
                        estimated_effort=s.get("effort", "small"),
                        risk=s.get("risk", "low"),
                        gates=s.get("operator_gates", []),
                        warnings=warnings + [f"PREFLIGHT BLOCKED: {preflight['recommendation']}"],
                        preflight_refinement=preflight,
                    )
                if preflight["status"] == "warn":
                    warnings.append(f"PREFLIGHT WARN: {preflight['recommendation']}")
            except Exception:
                pass  # Graceful degradation — preflight is advisory

            return TriggerPreview(
                slice_id=s["slice_id"],
                slice_number=s["slice_number"],
                title=s.get("title", ""),
                implementation_repo_id=repo_id,
                implementation_github_slug=f"sv-copilot/{repo_id}",
                integration_branch=integration_branch,
                dependency_tree_path=f".docs/planning/projects/{repo_id}/slice_dependency_tree.json",
                worker_routing=worker_routing,
                dry_run_task_packet={
                    "task_type": "implement_slice",
                    "adapter_type": "cline",
                    "slice_id": req.slice_id,
                    "planning_repo_id": "drake-governance",
                    "implementation_repo_id": repo_id,
                    "slice_detail_path": f".docs/planning/projects/{repo_id}/slices/{req.slice_id}.md",
                },
                estimated_effort=s.get("effort", "small"),
                risk=s.get("risk", "low"),
                gates=s.get("operator_gates", []),
                warnings=warnings,
                preflight_refinement=preflight,
            )
    raise HTTPException(404, f"Slice {req.slice_id} not found")


@app.post("/dispatch/confirm", response_model=DispatchResponse)
def dispatch_confirm(req: DispatchRequest):
    import uuid
    repo_id = _normalize_repo_id(req.repo_id)
    tree = _tree(repo_id)
    found = None
    for s in tree.get("slices", []):
        if s["slice_id"] == req.slice_id:
            found = s
            break
    if not found:
        raise HTTPException(404, f"Slice {req.slice_id} not found")
    if found.get("operator_gates"):
        raise HTTPException(400, f"Slice has unresolved operator gates: {found['operator_gates']}")

    token_name = _dispatch_webhook_token_resolvable(repo_id)
    if not token_name:
        raise HTTPException(
            400,
            "Primary worker webhook token is not configured in env or credential broker",
        )

    job_id = str(uuid.uuid4())
    queue = _load_queue()
    queue.setdefault("jobs", []).append({
        "job_id": job_id,
        "slice_id": req.slice_id,
        "repo_id": repo_id,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "webhook_token_env": token_name,
    })
    _save_queue(queue)

    return DispatchResponse(status="queued", job_id=job_id, slice_id=req.slice_id)


@app.get("/runs")
def list_runs(
    repo_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    return {"runs": _list_run_records(repo_id=repo_id, limit=limit)}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    if not SAFE_RUN_ID.match(run_id):
        raise HTTPException(422, "run_id contains unsafe characters")
    path = RUNS_PATH / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Run {run_id} not found")
    return _load_run_file(path)


@app.post("/runs", status_code=201)
def receive_run(run: dict[str, Any]):
    """Ingest Evidence Contract JSON from cloud pipeline workers and local runners."""
    secret_field = _contains_secret_values(run)
    if secret_field:
        raise HTTPException(
            status_code=422,
            detail=f"Secret values are not allowed in run payloads (field: {secret_field})",
        )

    _validate_evidence_payload(run)
    run_id = _run_id_from_payload(run)
    _save_run_record(run_id, run)
    return {"status": "accepted", "run_id": run_id}


@app.get("/admin/sync")
def admin_sync():
    """Pull latest planning data from GitHub. Called by Railway cron job."""
    import subprocess

    planning_path = Path(os.getenv("PLANNING_CHECKOUT_PATH", "/data/planning/drake-governance"))
    slug = os.getenv("PLANNING_GITHUB_SLUG", "sv-copilot/drake-governance")
    branch = _planning_branch()
    gh_token = os.getenv("GH_TOKEN", "")

    if not planning_path.exists():
        planning_path.parent.mkdir(parents=True, exist_ok=True)

    if not (planning_path / ".git").exists():
        # First sync: clone
        clone_url = f"https://{gh_token}@github.com/{slug}.git"
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, clone_url, str(planning_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {"status": "clone_failed", "error": result.stderr.strip()}
        return {"status": "cloned", "commit": _git_head(planning_path), "sync_age_seconds": 0}
    else:
        # Subsequent syncs: fetch + reset
        result = subprocess.run(
            ["git", "fetch", "origin", branch, "--depth", "1"],
            capture_output=True, text=True, timeout=60, cwd=str(planning_path),
        )
        if result.returncode != 0:
            return {"status": "fetch_failed", "error": result.stderr.strip()}
        result = subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            capture_output=True, text=True, timeout=30, cwd=str(planning_path),
        )
        if result.returncode != 0:
            return {"status": "reset_failed", "error": result.stderr.strip()}
        return {"status": "synced", "commit": _git_head(planning_path), "sync_age_seconds": 0}


def _git_head(path: Path) -> Optional[str]:
    """Return short HEAD hash for a git checkout."""
    import subprocess
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=10, cwd=str(path),
    )
    return result.stdout.strip() if result.returncode == 0 else None


# Optional route modules — may not exist in local dev
try:
    from credentials_routes import router as credentials_router  # noqa: E402
    app.include_router(credentials_router)
except ImportError:
    pass

try:
    from crewai_routes import router as crewai_router  # noqa: E402
    app.include_router(crewai_router)
except ImportError:
    pass

try:
    from webhook_routes import router as webhook_router  # noqa: E402
    app.include_router(webhook_router)
except ImportError:
    pass
