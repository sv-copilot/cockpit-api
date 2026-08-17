"""Tests for COCKPIT-API-BRANCH-DEFAULT-1 (#205).

`branch_posture_summary` must roll up `merged_branch` / `promoted_branch` per
branch instead of hardcoding `{"ai-dev": N}`, and `/admin/sync` must default to
`dev` when `PLANNING_BRANCH` is unset.

Red→Green: these tests fail while the code hardcodes `ai-dev`.
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
    """Import main lazily so module-level PLANNING_PATH bindings are not
    captured against the default env during pytest collection."""
    import main as cockpit_main  # noqa: E402

    return cockpit_main


# ── Unit tests: branch posture rollup ────────────────────────────────────


def test_branch_posture_summary_empty(cockpit_main):
    assert cockpit_main._branch_posture_summary([]) == {}


def test_branch_posture_summary_rolls_up_branches(cockpit_main):
    slices = [
        {"branch_posture": {"merged_branch": "dev"}},
        {"branch_posture": {"merged_branch": "dev", "promoted_branch": "main"}},
        {"branch_posture": {"merged_branch": "dev"}},
    ]
    assert cockpit_main._branch_posture_summary(slices) == {"dev": 3, "main": 1}


def test_branch_posture_summary_ignores_missing_posture(cockpit_main):
    slices = [
        {},
        {"branch_posture": None},
        {"branch_posture": {"merged_branch": "dev"}},
    ]
    assert cockpit_main._branch_posture_summary(slices) == {"dev": 1}


# ── Unit tests: planning branch default ──────────────────────────────────


def test_planning_branch_defaults_to_dev(cockpit_main, monkeypatch):
    monkeypatch.delenv("PLANNING_BRANCH", raising=False)
    assert cockpit_main._planning_branch() == "dev"


def test_planning_branch_respects_env(cockpit_main, monkeypatch):
    monkeypatch.setenv("PLANNING_BRANCH", "rc")
    assert cockpit_main._planning_branch() == "rc"


# ── Endpoint test: /cockpit/progress summary is dynamic ──────────────────

_PROJECT = {
    "id": "test-repo",
    "github_slug": "sv-copilot/test-repo",
    "integration_branch": "dev",
}

_TREE = {
    "slices": [
        {
            "slice_id": "TEST-SLICE-1",
            "slice_number": 1,
            "title": "Merged only",
            "state": "validated",
            "status": "done",
            "dependencies": [],
            "blocks": [],
            "operator_gates": [],
            "automation_eligible": False,
            "branch_posture": {"merged_branch": "dev"},
        },
        {
            "slice_id": "TEST-SLICE-2",
            "slice_number": 2,
            "title": "Promoted",
            "state": "promoted",
            "status": "done",
            "dependencies": [],
            "blocks": [],
            "operator_gates": [],
            "automation_eligible": False,
            "branch_posture": {"merged_branch": "dev", "promoted_branch": "main"},
        },
    ]
}


def test_progress_branch_posture_summary_no_ai_dev(cockpit_main, monkeypatch):
    monkeypatch.setattr(cockpit_main, "_registry", lambda: {"projects": [_PROJECT]})
    monkeypatch.setattr(cockpit_main, "_tree", lambda repo_id: _TREE)

    client = TestClient(cockpit_main.app)
    resp = client.get("/cockpit/progress")
    assert resp.status_code == 200, resp.text

    rollup = resp.json()["projects"][0]["branch_posture_summary"]
    assert "ai-dev" not in rollup
    assert rollup == {"dev": 2, "main": 1}
