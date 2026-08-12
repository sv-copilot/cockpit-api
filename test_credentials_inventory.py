"""Tests for COCKPIT-CREDENTIALS-INVENTORY-1 (#104)."""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent
COCKPIT_API_ROOT = REPO_ROOT

sys.path.insert(0, str(COCKPIT_API_ROOT))

from credentials_inventory import (  # noqa: E402
    RefStatus,
    assert_response_has_no_secret_values,
    build_portfolio_inventory,
    build_repo_inventory,
    parse_env_example,
    parse_mcp_credential_refs,
    parse_secrets_catalog,
    resolve_ref_status,
)

SECRET_LIKE = re.compile(
    r"(?:ghp_[A-Za-z0-9]{20,}|ghs_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})"
)


@pytest.fixture
def registry() -> dict:
    return json.loads((REPO_ROOT / ".docs/projects-registry.json").read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PLANNING_CHECKOUT_PATH", str(REPO_ROOT))
    monkeypatch.setenv("RUNS_PATH", str(tmp_path / "cockpit" / "runs"))
    monkeypatch.setenv("QUEUE_PATH", str(tmp_path / "cockpit" / "queue.json"))
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path / "cockpit" / "sources.json"))
    sys.path.insert(0, str(COCKPIT_API_ROOT))
    import main as cockpit_main

    importlib.reload(cockpit_main)
    cockpit_main._ensure_runs_dir()
    return TestClient(cockpit_main.app)


def test_parse_secrets_catalog_extracts_registry_names() -> None:
    names = parse_secrets_catalog(COCKPIT_API_ROOT / "SECRETS.example.md")
    assert "GH_TOKEN" in names
    assert "PORTFOLIO_PLAN_ORCHESTRATOR_WEBHOOK_URL" in names


def test_parse_env_example_extracts_hetzner_vars() -> None:
    names = parse_env_example(COCKPIT_API_ROOT / ".env.example")
    assert "GH_TOKEN" in names
    assert "ANTHROPIC_API_KEY" in names
    assert "COCKPIT_API_URL" in names


def test_parse_mcp_profile_refs_for_simon_projects() -> None:
    refs = parse_mcp_credential_refs(
        REPO_ROOT / ".docs/mcp_environment_profile.json",
        tier="staging",
    )
    assert refs == {"GH_TOKEN"}


def test_resolve_ref_status_missing_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert resolve_ref_status("GH_TOKEN") == RefStatus.missing


def test_resolve_ref_status_configured_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "configured-but-not-a-real-token")
    assert resolve_ref_status("GH_TOKEN") == RefStatus.configured


def test_build_repo_inventory_lists_research_service_worker_refs(registry: dict) -> None:
    inventory = build_repo_inventory(
        registry,
        "research-service",
        planning_path=REPO_ROOT,
    )
    ref_names = {ref.name for ref in inventory.required_refs}
    assert "GH_TOKEN" in ref_names
    assert "RESEARCH_SERVICE_SLICE_PIPELINE_WEBHOOK_URL" in ref_names
    assert "RESEARCH_SERVICE_SLICE_PIPELINE_WEBHOOK_TOKEN" in ref_names
    assert "JOBHUNTER_SLICE_PIPELINE_WEBHOOK_URL" not in ref_names
    assert len(inventory.workers) == 1
    assert inventory.workers[0].worker_id == "research-service-slice-pipeline"


def test_build_portfolio_inventory_includes_all_registry_projects(registry: dict) -> None:
    inventory = build_portfolio_inventory(registry, planning_path=REPO_ROOT)
    repo_ids = {repo.repo_id for repo in inventory.repos}
    registry_ids = {project["id"] for project in registry["projects"]}
    assert registry_ids.issubset(repo_ids)
    assert inventory.summary.total_refs > 0
    assert "worker" in inventory.summary.by_environment


def test_registry_refs_appear_without_broker(registry: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    inventory = build_repo_inventory(
        registry,
        "research-service",
        planning_path=REPO_ROOT,
        broker_index={},
    )
    gh_token = next(ref for ref in inventory.required_refs if ref.name == "GH_TOKEN")
    assert gh_token.status == RefStatus.missing
    assert any(source.startswith("worker:") for source in gh_token.sources)


def test_assert_response_has_no_secret_values_rejects_tokens() -> None:
    with pytest.raises(ValueError, match="secret values"):
        assert_response_has_no_secret_values({"token": "ghp_abcdefghijklmnopqrstuvwxyz123456"})


def test_get_credentials_inventory_returns_repos(client: TestClient) -> None:
    response = client.get("/credentials/inventory")
    assert response.status_code == 200
    payload = response.json()
    assert "repos" in payload
    assert payload["summary"]["total_refs"] > 0
    assert any(repo["repo_id"] == "research-service" for repo in payload["repos"])
    assert not SECRET_LIKE.search(json.dumps(payload))


def test_get_credentials_inventory_repo_research_service(client: TestClient) -> None:
    response = client.get("/credentials/inventory/repos/research-service")
    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_id"] == "research-service"
    ref_names = {ref["name"] for ref in payload["required_refs"]}
    assert "GH_TOKEN" in ref_names
    assert "RESEARCH_SERVICE_SLICE_PIPELINE_WEBHOOK_URL" in ref_names
    assert "SIMON_PROJECTS_SLICE_PIPELINE_WEBHOOK_URL" not in ref_names
    assert not SECRET_LIKE.search(json.dumps(payload))


def test_get_credentials_inventory_repo_unknown_returns_404(client: TestClient) -> None:
    response = client.get("/credentials/inventory/repos/not-a-real-repo")
    assert response.status_code == 404


def test_response_never_contains_webhook_url_values(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "RESEARCH_SERVICE_SLICE_PIPELINE_WEBHOOK_URL",
        "https://hooks.example.com/secret-path",
    )
    response = client.get("/credentials/inventory/repos/research-service")
    body = response.text
    assert "hooks.example.com" not in body
    assert "secret-path" not in body
