"""Tests for cockpit-api /crewai dispatch and run tracking endpoints."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import jsonschema
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent
COCKPIT_API_ROOT = REPO_ROOT
ORCH_ROOT = REPO_ROOT / "crewai_orchestrator"

VALID_SLICE_ID = "CREWAI-COCKPIT-1"
VALID_REPO_ID = "drake-governance"


@pytest.fixture
def crewai_runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated crewai runs directory and planning checkout."""
    runs_path = tmp_path / "cockpit" / "crewai-runs"
    monkeypatch.setenv("PLANNING_CHECKOUT_PATH", str(REPO_ROOT))
    monkeypatch.setenv("RUNS_PATH", str(tmp_path / "cockpit" / "runs"))
    monkeypatch.setenv("CREWAI_RUNS_PATH", str(runs_path))
    monkeypatch.setenv("QUEUE_PATH", str(tmp_path / "cockpit" / "queue.json"))
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path / "cockpit" / "sources.json"))
    return runs_path


@pytest.fixture
def client(crewai_runs_dir: Path) -> TestClient:
    sys.path.insert(0, str(COCKPIT_API_ROOT))
    import main as cockpit_main

    importlib.reload(cockpit_main)
    cockpit_main._ensure_runs_dir()
    return TestClient(cockpit_main.app)


def _evidence_schema() -> dict:
    schema_path = REPO_ROOT / "schemas" / "evidence-contract.schema.json"
    validation_path = REPO_ROOT / "schemas" / "validation-results.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validation_results = json.loads(validation_path.read_text(encoding="utf-8"))
    resolver = jsonschema.RefResolver(
        base_uri=schema["$id"],
        referrer=schema,
        store={
            "validation-results.schema.json": validation_results,
            validation_results.get("$id", ""): validation_results,
        },
    )
    return schema, resolver


def _assert_valid_evidence(payload: dict) -> None:
    schema, resolver = _evidence_schema()
    jsonschema.Draft7Validator(schema, resolver=resolver).validate(payload)


def _mock_kickoff_result() -> MagicMock:
    mock = MagicMock()
    mock.raw = "Crew run completed successfully."
    return mock


def _dispatch_payload(
    *,
    crew_type: str = "refinement",
    slice_id: str = VALID_SLICE_ID,
    repo_id: str = VALID_REPO_ID,
) -> dict:
    return {"crew_type": crew_type, "slice_id": slice_id, "repo_id": repo_id}


@patch("crewai_routes.run_crew_kickoff")
def test_dispatch_valid_refinement_returns_run_id(
    mock_kickoff: MagicMock,
    client: TestClient,
    crewai_runs_dir: Path,
) -> None:
    mock_kickoff.return_value = (
        _mock_kickoff_result(),
        [{"task_index": 0, "description": "read_planning_tree", "output": "summary"}],
    )

    resp = client.post("/crewai/dispatch", json=_dispatch_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert body["status"] in ("success", "complete", "running")
    run_id = body["run_id"]
    assert (crewai_runs_dir / f"{run_id}.json").exists()
    mock_kickoff.assert_called_once()


def test_dispatch_rejects_invalid_crew_type(client: TestClient) -> None:
    resp = client.post(
        "/crewai/dispatch",
        json=_dispatch_payload(crew_type="nonexistent"),
    )
    assert resp.status_code == 400
    assert "crew_type" in resp.json()["detail"].lower()


def test_dispatch_rejects_missing_tree(client: TestClient) -> None:
    resp = client.post(
        "/crewai/dispatch",
        json=_dispatch_payload(repo_id="no-such-repo"),
    )
    assert resp.status_code == 404


def test_dispatch_rejects_missing_slice(client: TestClient) -> None:
    resp = client.post(
        "/crewai/dispatch",
        json=_dispatch_payload(slice_id="NO-SUCH-SLICE"),
    )
    assert resp.status_code == 404


@patch("crewai_routes.run_crew_kickoff")
def test_list_crewai_runs_returns_status_and_duration(
    mock_kickoff: MagicMock,
    client: TestClient,
) -> None:
    mock_kickoff.return_value = (_mock_kickoff_result(), [])

    dispatch = client.post("/crewai/dispatch", json=_dispatch_payload())
    assert dispatch.status_code == 200
    run_id = dispatch.json()["run_id"]

    resp = client.get("/crewai/runs")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) >= 1
    match = next(r for r in runs if r["run_id"] == run_id)
    assert match["status"]
    assert match["crew_type"] == "refinement"
    assert match["slice_id"] == VALID_SLICE_ID
    assert "duration_ms" in match


@patch("crewai_routes.run_crew_kickoff")
def test_get_crewai_run_detail_returns_evidence_contract(
    mock_kickoff: MagicMock,
    client: TestClient,
) -> None:
    mock_kickoff.return_value = (
        _mock_kickoff_result(),
        [{"task_index": 0, "description": "read_planning_tree", "output": "tree summary"}],
    )

    dispatch = client.post("/crewai/dispatch", json=_dispatch_payload())
    run_id = dispatch.json()["run_id"]

    resp = client.get(f"/crewai/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["crew_type"] == "refinement"
    assert body["slice_id"] == VALID_SLICE_ID
    assert body["task_outputs"]
    assert body["summary"]
    _assert_valid_evidence(body)


def test_get_crewai_run_not_found(client: TestClient) -> None:
    resp = client.get("/crewai/runs/does-not-exist")
    assert resp.status_code == 404


def test_health_unaffected(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_cockpit_progress_unaffected(client: TestClient) -> None:
    resp = client.get("/cockpit/progress")
    assert resp.status_code == 200
    assert "projects" in resp.json()
