"""Write-only encrypted credential broker for cockpit-api."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from credential_broker.crypto import (
    DEFAULT_KEY_ID,
    decrypt_secret,
    encrypt_secret,
    fingerprint_secret,
)
from credential_broker.store import (
    CredentialMetadata,
    CredentialStore,
    MemoryCredentialStore,
    PostgresCredentialStore,
)
from credential_broker.tests_runner import CredentialTestResult, run_credential_test

CREDENTIAL_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+$")
_broker_singleton: "CredentialBroker | None" = None


class CredentialBroker:
    def __init__(
        self,
        store: CredentialStore,
        *,
        master_key: str,
        default_environment: str = "staging",
    ) -> None:
        self.store = store
        self.master_key = master_key
        self.default_environment = default_environment
        self.store.ensure_schema()

    def upsert_credential(
        self,
        name: str,
        value: str,
        *,
        environment: str | None = None,
        scope_repo_id: str | None = None,
    ) -> dict[str, Any]:
        if not CREDENTIAL_NAME_PATTERN.match(name):
            raise ValueError(f"invalid credential name: {name!r}")
        if not value.strip():
            raise ValueError("credential value must be non-empty")
        env = environment or self.default_environment
        now = datetime.now(timezone.utc)
        record = CredentialMetadata(
            name=name,
            environment=env,
            scope_repo_id=scope_repo_id,
            ciphertext=encrypt_secret(value, master_key=self.master_key),
            key_id=DEFAULT_KEY_ID,
            fingerprint=fingerprint_secret(value),
            updated_at=now,
            last_test_at=None,
            last_test_status=None,
        )
        saved = self.store.upsert(record)
        return {
            "name": saved.name,
            "environment": saved.environment,
            "fingerprint": saved.fingerprint,
            "updated_at": saved.updated_at.isoformat(),
            "scope_repo_id": saved.scope_repo_id,
        }

    def revoke_credential(self, name: str, *, environment: str | None = None) -> bool:
        env = environment or self.default_environment
        return self.store.delete(name, environment=env)

    def test_credential(
        self,
        name: str,
        *,
        environment: str | None = None,
        http_post=None,
    ) -> dict[str, Any]:
        env = environment or self.default_environment
        record = self.store.get(name, environment=env)
        if record is None:
            return {"name": name, "status": "failed", "error_class": "not_found"}
        plaintext = decrypt_secret(record.ciphertext, master_key=self.master_key)
        result: CredentialTestResult = run_credential_test(
            name,
            plaintext,
            http_post=http_post,
        )
        now = datetime.now(timezone.utc)
        updated = CredentialMetadata(
            name=record.name,
            environment=record.environment,
            scope_repo_id=record.scope_repo_id,
            ciphertext=record.ciphertext,
            key_id=record.key_id,
            fingerprint=record.fingerprint,
            updated_at=record.updated_at,
            last_test_at=now,
            last_test_status=result.status,
        )
        self.store.upsert(updated)
        return {
            "name": name,
            "status": result.status,
            "error_class": result.error_class,
        }

    def resolve_runtime(self, name: str, *, environment: str | None = None) -> str | None:
        env = environment or self.default_environment
        record = self.store.get(name, environment=env)
        if record is None:
            return None
        return decrypt_secret(record.ciphertext, master_key=self.master_key)

    def is_resolvable(self, name: str, *, environment: str | None = None) -> bool:
        existing = os.environ.get(name)
        if isinstance(existing, str) and existing.strip():
            return True
        return self.resolve_runtime(name, environment=environment) is not None

    def build_index(self, *, environment: str | None = None) -> dict[str, dict[str, Any]]:
        records = self.store.list_metadata(environment=environment)
        index: dict[str, dict[str, Any]] = {}
        for record in records:
            index[record.name] = {
                "configured": True,
                "fingerprint": record.fingerprint,
                "last_test_status": record.last_test_status,
                "stale": record.is_stale,
            }
        return index


def create_store_from_env() -> CredentialStore:
    database_url = os.getenv("DATABASE_URL", "").strip()
    backend = os.getenv("CREDENTIAL_BROKER_BACKEND", "").strip().lower()
    if backend == "memory" or not database_url:
        return MemoryCredentialStore()
    return PostgresCredentialStore(database_url)


def reset_broker_singleton() -> None:
    global _broker_singleton
    _broker_singleton = None


def get_broker(*, force_memory: bool = False) -> CredentialBroker | None:
    global _broker_singleton
    if force_memory:
        reset_broker_singleton()
    master_key = os.getenv("COCKPIT_SECRETS_MASTER_KEY", "").strip()
    if not master_key:
        return None
    if _broker_singleton is not None and not force_memory:
        return _broker_singleton
    if force_memory:
        store: CredentialStore = MemoryCredentialStore()
    else:
        store = create_store_from_env()
    _broker_singleton = CredentialBroker(
        store,
        master_key=master_key,
        default_environment=os.getenv("CREDENTIALS_INVENTORY_ENV", "staging"),
    )
    return _broker_singleton


def bootstrap_env_from_broker(names: list[str], *, environment: str | None = None) -> list[str]:
    """Populate os.environ for runtime services when values are broker-backed only."""
    broker = get_broker()
    if broker is None:
        return []
    hydrated: list[str] = []
    for name in names:
        if os.environ.get(name):
            continue
        value = broker.resolve_runtime(name, environment=environment)
        if value:
            os.environ[name] = value
            hydrated.append(name)
    return hydrated
