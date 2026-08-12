"""Shared projects registry helpers for schema v2 migration and lookup."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

CURRENT_REGISTRY_SCHEMA_VERSION = 3

ADAPTER_TYPES = frozenset(
    {"cursor", "cline", "openhands", "aider", "opencode", "custom"}
)

WORKER_ROLES = frozenset({"slice_pipeline", "plan_next_slice"})

V1_DEFAULT_ADAPTER_TYPE = "cursor"

SLICE_PIPELINE_BASE_CREDENTIAL_REFS = frozenset({"GH_TOKEN"})
PORTFOLIO_ORCHESTRATOR_CREDENTIAL_REFS = frozenset(
    {
        "PORTFOLIO_PLAN_ORCHESTRATOR_WEBHOOK_URL",
        "PORTFOLIO_PLAN_ORCHESTRATOR_WEBHOOK_TOKEN",
    }
)
PLAN_NEXT_SLICE_CHAINING_REFS = frozenset(
    {
        "SLICE_PIPELINE_WEBHOOK_URL",
        "SLICE_PIPELINE_WEBHOOK_TOKEN",
    }
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry(path: Path | str) -> dict[str, Any]:
    """Load registry JSON and return canonical schema v2 form."""
    payload = load_json(Path(path))
    migrated, _changes = migrate_registry(payload)
    return migrated


def migrate_registry(registry: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Upgrade legacy registry documents to canonical schema v3."""
    changes: list[str] = []
    migrated = deepcopy(registry)
    version = migrated.get("schema_version", 1)

    if version == CURRENT_REGISTRY_SCHEMA_VERSION:
        project_changes = _normalize_v2_projects(migrated)
        changes.extend(project_changes)
        if project_changes:
            changes.insert(0, "normalized schema v3 project workers")
        return migrated, changes

    if version == 2:
        migrated["schema_version"] = CURRENT_REGISTRY_SCHEMA_VERSION
        changes.append("upgraded schema_version 2 -> 3")
        project_changes = _normalize_v2_projects(migrated)
        changes.extend(project_changes)
        return migrated, changes

    if version != 1:
        changes.append(f"unsupported schema_version {version!r}")
        return migrated, changes

    migrated["schema_version"] = 2
    changes.append("upgraded schema_version 1 -> 2")

    for project in migrated.get("projects", []):
        project_id = project.get("id", "unknown")
        workers, worker_changes = _workers_from_v1_project(project)
        project["workers"] = workers
        project.pop("automations", None)
        project.pop("webhook_env", None)
        for change in worker_changes:
            changes.append(f"project {project_id}: {change}")

    migrated["schema_version"] = CURRENT_REGISTRY_SCHEMA_VERSION
    changes.append("upgraded schema_version 2 -> 3")
    project_changes = _normalize_v2_projects(migrated)
    changes.extend(project_changes)
    return migrated, changes


def _workers_from_v1_project(project: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    changes: list[str] = []
    project_id = str(project.get("id", "project"))
    automations = project.get("automations") or {}
    webhook_env = project.get("webhook_env") or {}
    workers: list[dict[str, Any]] = []

    slice_pipeline_id = automations.get("slice_pipeline_id")
    if slice_pipeline_id is not None or webhook_env:
        worker: dict[str, Any] = {
            "worker_id": f"{project_id}-slice-pipeline",
            "adapter_type": V1_DEFAULT_ADAPTER_TYPE,
            "role": "slice_pipeline",
            "automation_id": slice_pipeline_id,
            "model_slug": automations.get("slice_pipeline_model_slug") or "composer-2.5",
            "enabled": True,
            "primary": True,
        }
        url = webhook_env.get("slice_pipeline_url")
        token = webhook_env.get("slice_pipeline_token")
        if url and token:
            worker["webhook_env"] = {"url": url, "token": token}
        workers.append(worker)
        changes.append("migrated slice_pipeline worker from automations/webhook_env")

    plan_next_slice_id = automations.get("plan_next_slice_id")
    if plan_next_slice_id:
        workers.append(
            {
                "worker_id": f"{project_id}-plan-next-slice",
                "adapter_type": V1_DEFAULT_ADAPTER_TYPE,
                "role": "plan_next_slice",
                "automation_id": plan_next_slice_id,
                "model_slug": automations.get("slice_pipeline_model_slug") or "composer-2.5",
                "enabled": True,
                "primary": False,
            }
        )
        changes.append("migrated plan_next_slice worker from automations")

    return workers, changes


def _normalize_v2_projects(registry: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    for project in registry.get("projects", []):
        project_id = project.get("id", "unknown")
        workers = project.get("workers")
        if not isinstance(workers, list):
            continue
        for worker in workers:
            if worker.get("enabled") is None:
                worker["enabled"] = True
                changes.append(f"project {project_id}: defaulted worker.enabled=true")
            if worker.get("primary") is None:
                worker["primary"] = worker.get("role") == "slice_pipeline"
                changes.append(f"project {project_id}: defaulted worker.primary")
    return changes


def get_workers(
    project: dict[str, Any],
    *,
    role: str | None = None,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    workers = list(project.get("workers") or [])
    if role is not None:
        workers = [worker for worker in workers if worker.get("role") == role]
    if enabled_only:
        workers = [worker for worker in workers if worker.get("enabled") is True]
    return workers


def get_primary_worker(
    project: dict[str, Any],
    role: str = "slice_pipeline",
) -> dict[str, Any] | None:
    candidates = get_workers(project, role=role, enabled_only=True)
    for worker in candidates:
        if worker.get("primary") is True:
            return worker
    return candidates[0] if candidates else None


def default_credential_refs_for_worker(worker: dict[str, Any]) -> list[str]:
    """Return role-based worker-scoped credential env var names (no values)."""
    role = worker.get("role")
    refs = set(SLICE_PIPELINE_BASE_CREDENTIAL_REFS)
    if role == "slice_pipeline":
        refs |= PORTFOLIO_ORCHESTRATOR_CREDENTIAL_REFS
    elif role == "plan_next_slice":
        refs |= PLAN_NEXT_SLICE_CHAINING_REFS
    return sorted(refs)


def resolve_worker_credential_refs(worker: dict[str, Any]) -> list[str]:
    """Return declared credential_refs or role defaults for a worker."""
    declared = worker.get("credential_refs")
    if isinstance(declared, list) and declared:
        return sorted(set(declared))
    return default_credential_refs_for_worker(worker)


def resolve_dispatch_webhook_env(project: dict[str, Any]) -> dict[str, str] | None:
    """Return url/token env var names for orchestrator dispatch."""
    worker = get_primary_worker(project, role="slice_pipeline")
    if not worker:
        return None
    webhook_env = worker.get("webhook_env") or {}
    url = webhook_env.get("url")
    token = webhook_env.get("token")
    if url and token:
        return {"url": url, "token": token}
    return None


def registries_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)
