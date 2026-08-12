"""Tests for credential write VPN/internal gate."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent
COCKPIT_API_ROOT = REPO_ROOT

sys.path.insert(0, str(COCKPIT_API_ROOT))

from credential_broker.write_gate import credentials_write_allowed  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COCKPIT_SECRETS_MASTER_KEY", "test-master-key-not-for-production-use")
    monkeypatch.setenv("CREDENTIAL_BROKER_BACKEND", "memory")
    monkeypatch.setenv("PLANNING_CHECKOUT_PATH", str(REPO_ROOT))
    monkeypatch.delenv("COCKPIT_ALLOW_CREDENTIAL_WRITES", raising=False)
    monkeypatch.setenv("RUNS_PATH", str(tmp_path / "cockpit" / "runs"))
    monkeypatch.setenv("QUEUE_PATH", str(tmp_path / "cockpit" / "queue.json"))
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path / "cockpit" / "sources.json"))
    sys.path.insert(0, str(COCKPIT_API_ROOT))
    import main as cockpit_main

    importlib.reload(cockpit_main)
    cockpit_main._ensure_runs_dir()
    return TestClient(cockpit_main.app)


def test_write_gate_blocks_without_vpn_or_override(client: TestClient) -> None:
    response = client.post("/credentials/GH_TOKEN", json={"value": "ghp_gate_test_value"})
    assert response.status_code == 403
    assert "gated" in response.json()["detail"]


def test_write_gate_allows_dev_override(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COCKPIT_ALLOW_CREDENTIAL_WRITES", "1")
    response = client.post("/credentials/GH_TOKEN", json={"value": "ghp_gate_override_value"})
    assert response.status_code == 200
    assert "ghp_gate_override_value" not in response.text


def test_write_gate_allows_internal_header(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    response = client.post(
        "/credentials/GH_TOKEN",
        json={"value": "ghp_internal_header_value"},
        headers={"X-Cockpit-Internal": "1"},
    )
    assert response.status_code == 200


def test_write_gate_allowlist_match(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyClient:
        host = "10.0.0.5"

    class DummyRequest:
        headers = {}
        client = DummyClient()

    monkeypatch.setenv("COCKPIT_VPN_ALLOWLIST", "10.0.0.5")
    assert credentials_write_allowed(DummyRequest()) is True
