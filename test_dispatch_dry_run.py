"""Tests for COCKPIT-API-BRANCH-RESOLVE-1 (#204).

`POST /dispatch/dry-run` must resolve `integration_branch` and `worker_routing`
dynamically from the registry instead of hardcoding `ai-dev` and a fixed worker.

Red→Green: these tests fail while `dispatch_dry_run` hardcodes those values.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _planning_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the planning checkout at the repo root for every test."""
    monkeypatch.setenv("PLANNING_CHECKOUT_PATH", str(REPO_ROOT))


@pytest.fixture
def cockpit_main():
    """Import main lazily so router modules' module-level PLANNING_PATH bindings
    are not captured against the default env during pytest collection."""
    import main as cockpit_main  # noqa: E402

    return cockpit_main


# ── Unit tests: resolver helpers ──────────────────────────────────────────


def test_resolve_integration_branch_uses_registry_value(cockpit_main):
    project = {"id": "test-repo", "integration_branch": "dev"}
    assert cockpit_main._resolve_integration_branch(project) == "dev"


def test_resolve_integration_branch_falls_back_to_dev_when_absent(cockpit_main):
    assert cockpit_main._resolve_integration_branch(None) == "dev"
    assert cockpit_main._resolve_integration_branch({}) == "dev"


def test_resolve_worker_routing_from_primary_worker(cockpit_main):
    project = {
        "workers": [
            {
                "worker_id": "test-slice-pipeline",
                "adapter_type": "cursor",
                "role": "slice_pipeline",
                "enabled": True,
                "primary": True,
                "webhook_env": {
                    "url": "TEST_WEBHOOK_URL",
                    "token": "TEST_WEBHOOK_TOKEN",
                },
            }
        ]
    }
    routing = cockpit_main._resolve_worker_routing(project)
    assert routing["adapter_type"] == "cursor"
    assert routing["worker_id"] == "test-slice-pipeline"
    assert routing["webhook_env_name"] == "TEST_WEBHOOK_URL"


def test_resolve_worker_routing_falls_back_when_no_project(cockpit_main):
    routing = cockpit_main._resolve_worker_routing(None)
    assert routing["adapter_type"] == "cline"
    assert routing["worker_id"] == "self-hosted-cline-runner"
    assert routing["webhook_env_name"] == "COCKPIT_API_URL"


# ── Endpoint test: /dispatch/dry-run uses registry values ────────────────

_PROJECT = {
    "id": "test-repo",
    "github_slug": "sv-copilot/test-repo",
    "integration_branch": "dev",
    "workers": [
        {
            "worker_id": "test-slice-pipeline",
            "adapter_type": "cursor",
            "role": "slice_pipeline",
            "enabled": True,
            "primary": True,
            "webhook_env": {
                "url": "TEST_WEBHOOK_URL",
                "token": "TEST_WEBHOOK_TOKEN",
            },
        }
    ],
}

_TREE = {
    "slices": [
        {
            "slice_id": "TEST-SLICE-1",
            "slice_number": 1,
            "title": "Test slice",
            "state": "ready",
            "status": "ready",
            "dependencies": [],
            "blocks": [],
            "operator_gates": [],
            "automation_eligible": True,
            "tier": "P1",
            "risk": "low",
            "effort": "small",
        }
    ]
}


def test_dry_run_preview_uses_registry_values(
    cockpit_main, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(cockpit_main, "_registry", lambda: {"projects": [_PROJECT]})
    monkeypatch.setattr(cockpit_main, "_tree", lambda repo_id: _TREE)

    client = TestClient(cockpit_main.app)
    resp = client.post(
        "/dispatch/dry-run",
        json={"repo_id": "test-repo", "slice_id": "TEST-SLICE-1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["integration_branch"] == "dev"
    assert body["worker_routing"]["adapter_type"] == "cursor"
    assert body["worker_routing"]["worker_id"] == "test-slice-pipeline"
    assert body["worker_routing"]["webhook_env_name"] == "TEST_WEBHOOK_URL"
