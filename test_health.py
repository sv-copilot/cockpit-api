"""Tests for /health expected_env presence report (COCKPIT-API-PREFLIGHT-ENV-1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    sys.path.insert(0, str(REPO_ROOT))
    monkeypatch.setenv("PLANNING_CHECKOUT_PATH", str(REPO_ROOT))
    import main as cockpit_main

    return TestClient(cockpit_main.app)


def test_health_includes_expected_env(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "expected_env" in body
    assert isinstance(body["expected_env"], dict)
    assert "DEEPSEEK_API_KEY" in body["expected_env"]


def test_expected_env_reports_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["expected_env"]["DEEPSEEK_API_KEY"] == "absent"


def test_expected_env_present_never_leaks_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_value = "sk-super-secret-value-12345"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret_value)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["expected_env"]["DEEPSEEK_API_KEY"] == "present"
    # No value material may ever appear anywhere in the serialized payload.
    assert secret_value not in resp.text


def test_expected_env_vars_override(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXPECTED_ENV_VARS", "FOO,BAR")
    resp = client.get("/health")
    body = resp.json()["expected_env"]
    assert set(body.keys()) == {"FOO", "BAR"}
    assert body["FOO"] in {"present", "absent"}
    assert body["BAR"] in {"present", "absent"}
