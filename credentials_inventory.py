"""Credential inventory read model for cockpit-api (slice #104)."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

_SCRIPTS_ROOT = Path(__file__).resolve().parent / "scripts"
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from projects_registry import resolve_worker_credential_refs  # noqa: E402

SECRET_VALUE_PATTERN = re.compile(
    r"(?:ghp_[A-Za-z0-9]{20,}|ghs_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9]{20,}|"
    r"https?://[^\s\"']+@[^\s\"']+)"
)
SECRETS_TABLE_NAME = re.compile(r"\|\s*`([A-Z][A-Z0-9_]+)`\s*\|")
ENV_EXAMPLE_NAME = re.compile(r"^([A-Z][A-Z0-9_]+)=")


class RefStatus(str, Enum):
    missing = "missing"
    configured = "configured"
    stale = "stale"
    test_failed = "test_failed"
    unknown = "unknown"


class CredentialRefRecord(BaseModel):
    name: str
    status: RefStatus
    environment: str
    sources: list[str] = Field(default_factory=list)


class WorkerCredentialView(BaseModel):
    worker_id: str
    role: str
    adapter_type: str
    enabled: bool
    credential_refs: list[str]
    webhook_env: dict[str, str]


class RepoCredentialInventory(BaseModel):
    repo_id: str
    github_slug: Optional[str] = None
    automation_enabled: bool = False
    missing_count: int = 0
    configured_count: int = 0
    stale_count: int = 0
    test_failed_count: int = 0
    unknown_count: int = 0
    workers: list[WorkerCredentialView] = Field(default_factory=list)
    required_refs: list[CredentialRefRecord] = Field(default_factory=list)
    mcp_credential_refs: list[str] = Field(default_factory=list)


class EnvironmentSummary(BaseModel):
    missing_count: int = 0
    configured_count: int = 0
    stale_count: int = 0
    test_failed_count: int = 0
    unknown_count: int = 0


class PortfolioCredentialSummary(BaseModel):
    total_refs: int = 0
    missing_count: int = 0
    configured_count: int = 0
    stale_count: int = 0
    test_failed_count: int = 0
    unknown_count: int = 0
    by_environment: dict[str, EnvironmentSummary] = Field(default_factory=dict)


class PortfolioCredentialInventory(BaseModel):
    generated_at: str
    environment: str
    summary: PortfolioCredentialSummary
    repos: list[RepoCredentialInventory] = Field(default_factory=list)
    portfolio_catalog_refs: list[CredentialRefRecord] = Field(default_factory=list)
    hetzner_catalog_refs: list[CredentialRefRecord] = Field(default_factory=list)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_secrets_catalog(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        for match in SECRETS_TABLE_NAME.finditer(line):
            names.add(match.group(1))
    return names


def parse_env_example(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ENV_EXAMPLE_NAME.match(stripped)
        if match:
            names.add(match.group(1))
    return names


def parse_mcp_credential_refs(profile_path: Path, *, tier: str | None = None) -> set[str]:
    if not profile_path.is_file():
        return set()
    profile = _load_json(profile_path)
    selected_tier = tier or profile.get("default_tier") or "dev"
    tiers = profile.get("tiers") or {}
    tier_doc = tiers.get(selected_tier) or {}
    refs: set[str] = set()
    for server in (tier_doc.get("mcp_servers") or {}).values():
        for ref in server.get("credential_refs") or []:
            if isinstance(ref, str) and ref:
                refs.add(ref)
    return refs


def resolve_ref_status(name: str, broker_index: dict[str, dict[str, Any]] | None = None) -> RefStatus:
    broker_index = broker_index or {}
    broker_meta = broker_index.get(name)
    if broker_meta:
        last_status = broker_meta.get("last_test_status")
        if last_status == "failed":
            return RefStatus.test_failed
        if broker_meta.get("configured") is True:
            if broker_meta.get("stale") is True and last_status is not None:
                return RefStatus.stale
            return RefStatus.configured
        if broker_meta.get("configured") is False:
            return RefStatus.missing

    value = os.environ.get(name)
    if isinstance(value, str) and value.strip():
        return RefStatus.configured
    return RefStatus.missing


def _project_by_id(registry: dict[str, Any], repo_id: str) -> dict[str, Any] | None:
    for project in registry.get("projects", []):
        if project.get("id") == repo_id:
            return project
    return None


def _merge_ref(
    index: dict[str, CredentialRefRecord],
    *,
    name: str,
    environment: str,
    source: str,
    broker_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    status = resolve_ref_status(name, broker_index)
    existing = index.get(name)
    if existing is None:
        index[name] = CredentialRefRecord(
            name=name,
            status=status,
            environment=environment,
            sources=[source],
        )
        return
    if source not in existing.sources:
        existing.sources.append(source)
    # Prefer worse status for operator visibility.
    priority = {
        RefStatus.test_failed: 5,
        RefStatus.missing: 4,
        RefStatus.stale: 3,
        RefStatus.unknown: 2,
        RefStatus.configured: 1,
    }
    if priority[status] > priority[existing.status]:
        existing.status = status


def _count_statuses(refs: list[CredentialRefRecord]) -> dict[str, int]:
    counts = {
        "missing_count": 0,
        "configured_count": 0,
        "stale_count": 0,
        "test_failed_count": 0,
        "unknown_count": 0,
    }
    for ref in refs:
        if ref.status == RefStatus.missing:
            counts["missing_count"] += 1
        elif ref.status == RefStatus.configured:
            counts["configured_count"] += 1
        elif ref.status == RefStatus.stale:
            counts["stale_count"] += 1
        elif ref.status == RefStatus.test_failed:
            counts["test_failed_count"] += 1
        else:
            counts["unknown_count"] += 1
    return counts


def build_repo_inventory(
    registry: dict[str, Any],
    repo_id: str,
    *,
    planning_path: Path,
    inventory_environment: str = "staging",
    broker_index: dict[str, dict[str, Any]] | None = None,
) -> RepoCredentialInventory:
    project = _project_by_id(registry, repo_id)
    if project is None:
        raise KeyError(repo_id)

    ref_index: dict[str, CredentialRefRecord] = {}
    worker_views: list[WorkerCredentialView] = []

    for worker in project.get("workers") or []:
        worker_id = str(worker.get("worker_id", "unknown"))
        role = str(worker.get("role", "unknown"))
        source_prefix = f"worker:{worker_id}"
        for ref in resolve_worker_credential_refs(worker):
            _merge_ref(
                ref_index,
                name=ref,
                environment="worker",
                source=source_prefix,
                broker_index=broker_index,
            )
        webhook_env = worker.get("webhook_env") or {}
        webhook_names = {
            value
            for value in (webhook_env.get("url"), webhook_env.get("token"))
            if isinstance(value, str) and value
        }
        for name in sorted(webhook_names):
            _merge_ref(
                ref_index,
                name=name,
                environment="worker",
                source=f"webhook_env:{worker_id}",
                broker_index=broker_index,
            )
        worker_views.append(
            WorkerCredentialView(
                worker_id=worker_id,
                role=role,
                adapter_type=str(worker.get("adapter_type", "unknown")),
                enabled=worker.get("enabled") is not False,
                credential_refs=list(resolve_worker_credential_refs(worker)),
                webhook_env={
                    key: value
                    for key, value in webhook_env.items()
                    if isinstance(value, str)
                },
            )
        )

    mcp_refs: set[str] = set()
    if repo_id == "drake-governance":
        mcp_refs = parse_mcp_credential_refs(
            planning_path / ".docs/mcp_environment_profile.json",
            tier=inventory_environment if inventory_environment in {"dev", "staging", "production"} else None,
        )
        for name in sorted(mcp_refs):
            _merge_ref(
                ref_index,
                name=name,
                environment="mcp",
                source="mcp_profile:drake-governance",
                broker_index=broker_index,
            )

    required_refs = sorted(ref_index.values(), key=lambda item: item.name)
    status_counts = _count_statuses(required_refs)
    return RepoCredentialInventory(
        repo_id=repo_id,
        github_slug=project.get("github_slug"),
        automation_enabled=project.get("automation_enabled") is True,
        workers=worker_views,
        required_refs=required_refs,
        mcp_credential_refs=sorted(mcp_refs),
        **status_counts,
    )


def build_portfolio_catalog_refs(
    planning_path: Path,
    *,
    broker_index: dict[str, dict[str, Any]] | None = None,
) -> list[CredentialRefRecord]:
    catalog = parse_secrets_catalog(planning_path / "SECRETS.example.md")
    refs: list[CredentialRefRecord] = []
    for name in sorted(catalog):
        refs.append(
            CredentialRefRecord(
                name=name,
                status=resolve_ref_status(name, broker_index),
                environment="portfolio",
                sources=["secrets_catalog:SECRETS.example.md"],
            )
        )
    return refs


def build_hetzner_catalog_refs(
    planning_path: Path,
    *,
    broker_index: dict[str, dict[str, Any]] | None = None,
) -> list[CredentialRefRecord]:
    catalog = parse_env_example(planning_path / "deploy/cline-operator/.env.example")
    refs: list[CredentialRefRecord] = []
    for name in sorted(catalog):
        refs.append(
            CredentialRefRecord(
                name=name,
                status=resolve_ref_status(name, broker_index),
                environment="hetzner",
                sources=["hetzner_env:deploy/cline-operator/.env.example"],
            )
        )
    return refs


def build_portfolio_inventory(
    registry: dict[str, Any],
    *,
    planning_path: Path,
    inventory_environment: str | None = None,
    broker_index: dict[str, dict[str, Any]] | None = None,
) -> PortfolioCredentialInventory:
    environment = inventory_environment or os.getenv("CREDENTIALS_INVENTORY_ENV", "staging")
    repos = [
        build_repo_inventory(
            registry,
            project["id"],
            planning_path=planning_path,
            inventory_environment=environment,
            broker_index=broker_index,
        )
        for project in registry.get("projects", [])
    ]
    portfolio_catalog_refs = build_portfolio_catalog_refs(
        planning_path,
        broker_index=broker_index,
    )
    hetzner_catalog_refs = build_hetzner_catalog_refs(
        planning_path,
        broker_index=broker_index,
    )

    by_environment: dict[str, EnvironmentSummary] = {}
    all_refs: list[CredentialRefRecord] = []
    for repo in repos:
        all_refs.extend(repo.required_refs)
    all_refs.extend(portfolio_catalog_refs)
    all_refs.extend(hetzner_catalog_refs)

    for ref in all_refs:
        bucket = by_environment.setdefault(ref.environment, EnvironmentSummary())
        if ref.status == RefStatus.missing:
            bucket.missing_count += 1
        elif ref.status == RefStatus.configured:
            bucket.configured_count += 1
        elif ref.status == RefStatus.stale:
            bucket.stale_count += 1
        elif ref.status == RefStatus.test_failed:
            bucket.test_failed_count += 1
        else:
            bucket.unknown_count += 1

    status_counts = _count_statuses(all_refs)
    summary = PortfolioCredentialSummary(
        total_refs=len(all_refs),
        by_environment=by_environment,
        **status_counts,
    )
    return PortfolioCredentialInventory(
        generated_at=datetime.now(timezone.utc).isoformat(),
        environment=environment,
        summary=summary,
        repos=repos,
        portfolio_catalog_refs=portfolio_catalog_refs,
        hetzner_catalog_refs=hetzner_catalog_refs,
    )


def assert_response_has_no_secret_values(payload: Any) -> None:
    serialized = json.dumps(payload)
    if SECRET_VALUE_PATTERN.search(serialized):
        raise ValueError("credential inventory response must not include secret values")
