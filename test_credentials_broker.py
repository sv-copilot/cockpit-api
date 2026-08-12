"""Tests for COCKPIT-CREDENTIALS-BROKER-1 (#105)."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent
COCKPIT_API_ROOT = REPO_ROOT

sys.path.insert(0, str(COCKPIT_API_ROOT))

from credential_broker.broker import CredentialBroker, bootstrap_env_from_broker, reset_broker_singleton  # noqa: E402
from credential_broker.crypto import decrypt_secret, fingerprint_secret  # noqa: E402
from credential_broker.store import MemoryCredentialStore  # noqa: E402
from credential_broker.tests_runner import run_credential_test  # noqa: E402

MASTER_KEY = "test-master-key-not-for-production-use"


@pytest.fixture(autouse=True)
def broker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_broker_singleton()
    monkeypatch.setenv("COCKPIT_SECRETS_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("CREDENTIAL_BROKER_BACKEND", "memory")
    monkeypatch.setenv("COCKPIT_ALLOW_CREDENTIAL_WRITES", "1")
    monkeypatch.setenv("PLANNING_CHECKOUT_PATH", str(REPO_ROOT))
    yield
    reset_broker_singleton()


@pytest.fixture
def broker() -> CredentialBroker:
    return CredentialBroker(MemoryCredentialStore(), master_key=MASTER_KEY)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RUNS_PATH", str(tmp_path / "cockpit" / "runs"))
    monkeypatch.setenv("QUEUE_PATH", str(tmp_path / "cockpit" / "queue.json"))
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path / "cockpit" / "sources.json"))
    sys.path.insert(0, str(COCKPIT_API_ROOT))
    import main as cockpit_main

    importlib.reload(cockpit_main)
    cockpit_main._ensure_runs_dir()
    return TestClient(cockpit_main.app)


def test_upsert_returns_fingerprint_without_plaintext(broker: CredentialBroker) -> None:
    saved = broker.upsert_credential("GH_TOKEN", "ghp_test_value_for_fixture_only")
    assert saved["fingerprint"] == fingerprint_secret("ghp_test_value_for_fixture_only")
    assert "ghp_test" not in json.dumps(saved)


def test_runtime_decrypt_roundtrip(broker: CredentialBroker) -> None:
    broker.upsert_credential("GH_TOKEN", "ghp_runtime_roundtrip_value")
    record = broker.store.get("GH_TOKEN", environment="staging")
    assert record is not None
    plaintext = decrypt_secret(record.ciphertext, master_key=MASTER_KEY)
    assert plaintext == "ghp_runtime_roundtrip_value"
    assert broker.resolve_runtime("GH_TOKEN") == plaintext


def test_build_index_marks_configured(broker: CredentialBroker) -> None:
    broker.upsert_credential("GH_TOKEN", "ghp_index_value")
    index = broker.build_index()
    assert index["GH_TOKEN"]["configured"] is True
    assert index["GH_TOKEN"]["stale"] is True


def test_webhook_token_test_uses_dry_run_post() -> None:
    mock_post = MagicMock(return_value=httpx.Response(404))
    result = run_credential_test(
        "SIMON_PROJECTS_SLICE_PIPELINE_WEBHOOK_TOKEN",
        "token-value",
        http_post=mock_post,
    )
    assert result.status == "passed"
    mock_post.assert_called_once()


def test_post_credentials_sets_without_returning_value(client: TestClient) -> None:
    response = client.post(
        "/credentials/GH_TOKEN",
        json={"value": "ghp_post_route_fixture_value", "environment": "staging"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "GH_TOKEN"
    assert "fingerprint" in body
    assert "ghp_post_route_fixture_value" not in response.text


def test_inventory_shows_configured_after_post(client: TestClient) -> None:
    client.post(
        "/credentials/GH_TOKEN",
        json={"value": "ghp_inventory_configured_value", "environment": "staging"},
    )
    inventory = client.get("/credentials/inventory/repos/drake-governance").json()
    gh = next(ref for ref in inventory["required_refs"] if ref["name"] == "GH_TOKEN")
    assert gh["status"] == "configured"


def test_delete_requires_operator_confirm_header(client: TestClient) -> None:
    client.post("/credentials/GH_TOKEN", json={"value": "ghp_delete_fixture_value"})
    denied = client.delete("/credentials/GH_TOKEN")
    assert denied.status_code == 400
    revoked = client.delete("/credentials/GH_TOKEN", headers={"X-Operator-Confirm": "revoke"})
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True


def test_test_endpoint_returns_pass_fail_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client.post("/credentials/ANTHROPIC_API_KEY", json={"value": "sk-ant-validprefix"})
    response = client.post("/credentials/ANTHROPIC_API_KEY/test")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"passed", "failed"}
    assert "sk-ant-validprefix" not in response.text


def test_dispatch_confirm_resolves_broker_token_when_env_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SIMON_PROJECTS_SLICE_PIPELINE_WEBHOOK_TOKEN", raising=False)
    client.post(
        "/credentials/SIMON_PROJECTS_SLICE_PIPELINE_WEBHOOK_TOKEN",
        json={"value": "broker-webhook-token-value"},
    )
    response = client.post(
        "/dispatch/confirm",
        json={"repo_id": "drake-governance", "slice_id": "PROD-GSG-1"},
    )
    if response.status_code == 404:
        pytest.skip("dispatch fixture slice unavailable in current tree")
    assert response.status_code == 200
    assert "broker-webhook-token-value" not in response.text
    assert response.json()["status"] == "queued"


def test_bootstrap_env_hydrates_missing_names(broker: CredentialBroker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    reset_broker_singleton()
    broker.upsert_credential("GH_TOKEN", "ghp_bootstrap_value")
    os.environ.pop("GH_TOKEN", None)
    from credential_broker import broker as broker_module

    monkeypatch.setattr(broker_module, "get_broker", lambda **kwargs: broker)
    hydrated = bootstrap_env_from_broker(["GH_TOKEN"])
    assert hydrated == ["GH_TOKEN"]
    assert os.environ.get("GH_TOKEN") == "ghp_bootstrap_value"
