"""Credential inventory and write-only broker routes for cockpit-api."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from credential_broker.write_gate import require_credentials_write_access

from credentials_inventory import (
    PortfolioCredentialInventory,
    RepoCredentialInventory,
    assert_response_has_no_secret_values,
    build_portfolio_inventory,
    build_repo_inventory,
)
from credential_broker.broker import get_broker

PLANNING_PATH = Path(os.getenv("PLANNING_CHECKOUT_PATH", "/data/planning/drake-governance"))
OPERATOR_CONFIRM_HEADER = "x-operator-confirm"


class CredentialUpsertRequest(BaseModel):
    value: str = Field(min_length=1)
    environment: str = "staging"
    scope_repo_id: str | None = None


class CredentialUpsertResponse(BaseModel):
    name: str
    environment: str
    fingerprint: str
    updated_at: str
    scope_repo_id: str | None = None


class CredentialTestResponse(BaseModel):
    name: str
    status: str
    error_class: str | None = None


class CredentialRevokeResponse(BaseModel):
    name: str
    environment: str
    revoked: bool


router = APIRouter(prefix="/credentials", tags=["credentials"])


def _load_registry() -> dict:
    import json

    return json.loads((PLANNING_PATH / ".docs/projects-registry.json").read_text(encoding="utf-8"))


def _require_broker():
    broker = get_broker()
    if broker is None:
        raise HTTPException(
            503,
            "Credential broker unavailable: set COCKPIT_SECRETS_MASTER_KEY "
            "(and DATABASE_URL for Postgres persistence)",
        )
    return broker


def _broker_index() -> dict[str, dict[str, Any]]:
    broker = get_broker()
    if broker is None:
        return {}
    return broker.build_index()


@router.get("/inventory", response_model=PortfolioCredentialInventory)
def credentials_inventory() -> PortfolioCredentialInventory:
    inventory = build_portfolio_inventory(
        _load_registry(),
        planning_path=PLANNING_PATH,
        broker_index=_broker_index(),
    )
    assert_response_has_no_secret_values(inventory.model_dump())
    return inventory


@router.get("/inventory/repos/{repo_id}", response_model=RepoCredentialInventory)
def credentials_inventory_repo(repo_id: str) -> RepoCredentialInventory:
    registry = _load_registry()
    try:
        inventory = build_repo_inventory(
            registry,
            repo_id,
            planning_path=PLANNING_PATH,
            broker_index=_broker_index(),
        )
    except KeyError as exc:
        raise HTTPException(404, f"Unknown repo_id: {repo_id}") from exc
    assert_response_has_no_secret_values(inventory.model_dump())
    return inventory


@router.post("/{name}", response_model=CredentialUpsertResponse)
def upsert_credential(
    name: str,
    body: CredentialUpsertRequest,
    request: Request,
) -> CredentialUpsertResponse:
    require_credentials_write_access(request)
    broker = _require_broker()
    try:
        saved = broker.upsert_credential(
            name,
            body.value,
            environment=body.environment,
            scope_repo_id=body.scope_repo_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    body.value = ""
    response = CredentialUpsertResponse(**saved)
    assert_response_has_no_secret_values(response.model_dump())
    return response


@router.delete("/{name}", response_model=CredentialRevokeResponse)
def revoke_credential(
    name: str,
    request: Request,
    environment: str = "staging",
    x_operator_confirm: str | None = Header(default=None, alias=OPERATOR_CONFIRM_HEADER),
) -> CredentialRevokeResponse:
    require_credentials_write_access(request)
    if x_operator_confirm != "revoke":
        raise HTTPException(
            400,
            f"Missing {OPERATOR_CONFIRM_HEADER}: revoke header for credential deletion",
        )
    broker = _require_broker()
    revoked = broker.revoke_credential(name, environment=environment)
    if not revoked:
        raise HTTPException(404, f"Credential {name!r} not found for environment {environment!r}")
    return CredentialRevokeResponse(name=name, environment=environment, revoked=True)


@router.post("/{name}/test", response_model=CredentialTestResponse)
def test_credential(
    name: str,
    request: Request,
    environment: str = "staging",
) -> CredentialTestResponse:
    # Test endpoint is read-only — no write gate required.
    # It only returns pass/fail status, never secret values.
    broker = _require_broker()
    result = broker.test_credential(name, environment=environment)
    response = CredentialTestResponse(**result)
    assert_response_has_no_secret_values(response.model_dump())
    return response
