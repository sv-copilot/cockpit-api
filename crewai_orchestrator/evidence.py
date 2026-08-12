"""Evidence contract adapter for CrewAI crew runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ADAPTER_VERSION = "0.1.0"


def extract_task_outputs(crew: Any) -> list[dict[str, Any]]:
    """Collect per-task outputs from a crew after kickoff."""
    outputs: list[dict[str, Any]] = []
    for index, task in enumerate(getattr(crew, "tasks", []) or []):
        description = getattr(task, "description", "") or ""
        if len(description) > 240:
            description = description[:237] + "..."
        outputs.append(
            {
                "task_index": index,
                "description": description,
                "output": str(getattr(task, "output", "") or ""),
            }
        )
    return outputs


def _map_status(raw_status: str) -> str:
    normalized = raw_status.lower()
    if normalized in {"success", "failure", "partial", "blocked", "skipped", "cancelled"}:
        return normalized
    if normalized in {"complete", "completed", "ok"}:
        return "success"
    if normalized in {"failed", "error"}:
        return "failure"
    return "partial"


def build_crewai_evidence(
    *,
    run_id: str,
    crew_type: str,
    slice_id: str,
    repo_id: str,
    summary: str,
    task_outputs: list[dict[str, Any]],
    started_at: datetime,
    completed_at: datetime,
    status: str = "success",
    evidence_items: list[dict[str, Any]] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Build an evidence-contract payload for a CrewAI run."""
    started = started_at.astimezone(timezone.utc)
    completed = completed_at.astimezone(timezone.utc)
    duration_ms = max(int((completed - started).total_seconds() * 1000), 0)
    mapped_status = _map_status(status)

    items = list(evidence_items or [])
    if error_message and mapped_status != "success":
        summary = f"{summary}\n\nError: {error_message}".strip()

    payload: dict[str, Any] = {
        "task_id": run_id,
        "run_id": run_id,
        "slice_id": slice_id,
        "repo_id": repo_id,
        "crew_type": crew_type,
        "status": mapped_status,
        "summary": summary or f"CrewAI {crew_type} run for {slice_id}",
        "evidence_items": items,
        "adapter_info": {
            "adapter_type": "crewai",
            "adapter_version": ADAPTER_VERSION,
            "runtime": f"crewai-orchestrator/{crew_type}",
        },
        "completed_at": completed.isoformat().replace("+00:00", "Z"),
        "timestamps": {
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "completed_at": completed.isoformat().replace("+00:00", "Z"),
            "duration_ms": duration_ms,
        },
        "task_outputs": task_outputs,
    }
    return payload


def run_summary_from_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Project a stored evidence record into a list-view summary."""
    timestamps = record.get("timestamps") if isinstance(record.get("timestamps"), dict) else {}
    started_at = record.get("started_at") or timestamps.get("started_at")
    completed_at = record.get("completed_at") or timestamps.get("completed_at")
    duration_ms = timestamps.get("duration_ms")
    if duration_ms is None and started_at and completed_at:
        try:
            start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
            duration_ms = max(int((end - start).total_seconds() * 1000), 0)
        except ValueError:
            duration_ms = None

    return {
        "run_id": record.get("run_id") or record.get("task_id"),
        "status": record.get("status", "unknown"),
        "crew_type": record.get("crew_type"),
        "slice_id": record.get("slice_id"),
        "repo_id": record.get("repo_id"),
        "duration_ms": duration_ms,
        "started_at": started_at,
        "completed_at": completed_at,
    }
