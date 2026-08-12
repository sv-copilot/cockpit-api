"""CrewAI dispatch and run tracking routes for the Cockpit API."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parent
_ORCH_ROOT = _REPO_ROOT / "crewai_orchestrator"

PLANNING_PATH = Path(os.getenv("PLANNING_CHECKOUT_PATH", "/data/planning/drake-governance"))
RUNS_PATH = Path(os.getenv("RUNS_PATH", "/data/cockpit/runs"))
CREWAI_RUNS_PATH = Path(os.getenv("CREWAI_RUNS_PATH", str(RUNS_PATH / "crewai")))


def _get_crewai_runs_path() -> Path:
    """Resolve CREWAI_RUNS_PATH at call time (not module load) to support env var overrides."""
    env_val = os.getenv("CREWAI_RUNS_PATH")
    if env_val:
        return Path(env_val)
    return CREWAI_RUNS_PATH

SAFE_RUN_ID = re.compile(r"^[a-zA-Z0-9._-]+$")
VALID_CREW_TYPES = frozenset({"refinement", "audit", "sync"})

router = APIRouter(prefix="/crewai", tags=["crewai"])


class CrewaiDispatchRequest(BaseModel):
    crew_type: str
    slice_id: str
    repo_id: str


class CrewaiDispatchResponse(BaseModel):
    run_id: str
    status: str
    crew_type: str
    slice_id: str


def _load_orch_module(module_name: str, filename: str):
    path = _ORCH_ROOT / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {module_name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_context_mod = _load_orch_module("crewai_context", "context.py")
_evidence_mod = _load_orch_module("crewai_evidence", "evidence.py")
_crews_mod = _load_orch_module("crewai_crews", "crews/__init__.py")

resolve_planning_context = _context_mod.resolve_planning_context
TreeNotFoundError = _context_mod.TreeNotFoundError
SliceNotFoundError = _context_mod.SliceNotFoundError
build_crewai_evidence = _evidence_mod.build_crewai_evidence
extract_task_outputs = _evidence_mod.extract_task_outputs
run_summary_from_evidence = _evidence_mod.run_summary_from_evidence
create_crew = _crews_mod.create_crew


def _validate_evidence_payload(payload: dict[str, Any]) -> None:
    from main import _validate_evidence_payload as validate

    validate(payload)


def _ensure_crewai_runs_dir() -> None:
    try:
        _get_crewai_runs_path().mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # best-effort; will retry on save


def _save_crewai_run(run_id: str, payload: dict[str, Any]) -> None:
    _ensure_crewai_runs_dir()
    path = _get_crewai_runs_path() / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_crewai_run(run_id: str) -> dict[str, Any]:
    path = _get_crewai_runs_path() / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"CrewAI run {run_id} not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _list_crewai_runs(
    *,
    repo_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not _get_crewai_runs_path().exists():
        return []

    records: list[dict[str, Any]] = []
    for path in _get_crewai_runs_path().glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if repo_id and record.get("repo_id") != repo_id:
            continue
        records.append(record)

    def sort_key(record: dict[str, Any]) -> str:
        timestamps = record.get("timestamps") if isinstance(record.get("timestamps"), dict) else {}
        for key in ("completed_at", "started_at"):
            value = record.get(key) or timestamps.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    records.sort(key=sort_key, reverse=True)
    return records[:limit]


def run_crew_kickoff(
    crew_type: str,
    context: dict[str, Any],
    *,
    dry_run: bool = True,
) -> tuple[Any, list[dict[str, Any]]]:
    """Instantiate and kick off a crew; return kickoff result and task outputs."""
    crew = create_crew(
        crew_type,
        dry_run=dry_run,
        repo_id=context["repo_id"],
        operator_input=context.get("operator_input", ""),
        slice_id=context["slice_id"],
        slice_detail=context.get("slice_detail", ""),
    )
    kickoff_inputs = {
        "repo_id": context["repo_id"],
        "slice_id": context["slice_id"],
        "operator_input": context.get("operator_input", ""),
    }
    result = crew.kickoff(inputs=kickoff_inputs)
    task_outputs = extract_task_outputs(crew)
    return result, task_outputs


@router.post("/dispatch", response_model=CrewaiDispatchResponse)
def dispatch_crewai(req: CrewaiDispatchRequest) -> CrewaiDispatchResponse:
    if req.crew_type not in VALID_CREW_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid crew_type '{req.crew_type}'; expected one of: {', '.join(sorted(VALID_CREW_TYPES))}",
        )

    try:
        context = resolve_planning_context(PLANNING_PATH, req.repo_id, req.slice_id)
    except TreeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SliceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    run_id = f"crewai-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc)
    status = "success"
    summary = ""
    task_outputs: list[dict[str, Any]] = []
    error_message: str | None = None

    try:
        result, task_outputs = run_crew_kickoff(req.crew_type, context, dry_run=True)
        summary = str(getattr(result, "raw", "") or getattr(result, "output", "") or "").strip()
        if not summary:
            summary = f"CrewAI {req.crew_type} completed for {req.slice_id}"
    except Exception as exc:  # noqa: BLE001 — capture crew failures as evidence
        status = "failure"
        summary = f"CrewAI {req.crew_type} failed for {req.slice_id}"
        error_message = str(exc)

    completed_at = datetime.now(timezone.utc)
    evidence = build_crewai_evidence(
        run_id=run_id,
        crew_type=req.crew_type,
        slice_id=req.slice_id,
        repo_id=req.repo_id,
        summary=summary,
        task_outputs=task_outputs,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        error_message=error_message,
    )

    _validate_evidence_payload(evidence)
    _save_crewai_run(run_id, evidence)

    return CrewaiDispatchResponse(
        run_id=run_id,
        status=evidence["status"],
        crew_type=req.crew_type,
        slice_id=req.slice_id,
    )


@router.get("/runs")
def list_crewai_runs(
    repo_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, list[dict[str, Any]]]:
    records = _list_crewai_runs(repo_id=repo_id, limit=limit)
    return {"runs": [run_summary_from_evidence(record) for record in records]}


@router.get("/runs/{run_id}")
def get_crewai_run(run_id: str) -> dict[str, Any]:
    if not SAFE_RUN_ID.match(run_id):
        raise HTTPException(status_code=422, detail="run_id contains unsafe characters")
    return _load_crewai_run(run_id)
