"""Credential metadata storage backends for the write-only broker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class CredentialMetadata:
    name: str
    environment: str
    scope_repo_id: str | None
    ciphertext: str
    key_id: str
    fingerprint: str
    updated_at: datetime
    last_test_at: datetime | None
    last_test_status: str | None

    @property
    def is_stale(self) -> bool:
        if self.last_test_at is None:
            return True
        return self.last_test_at < self.updated_at


class CredentialStore(Protocol):
    def ensure_schema(self) -> None: ...

    def upsert(self, record: CredentialMetadata) -> CredentialMetadata: ...

    def delete(self, name: str, *, environment: str) -> bool: ...

    def get(self, name: str, *, environment: str) -> CredentialMetadata | None: ...

    def list_metadata(self, *, environment: str | None = None) -> list[CredentialMetadata]: ...


class MemoryCredentialStore:
    """Fake Postgres-compatible store for tests and local dry runs."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], CredentialMetadata] = {}

    def ensure_schema(self) -> None:
        return None

    def upsert(self, record: CredentialMetadata) -> CredentialMetadata:
        key = (record.name, record.environment)
        self._records[key] = record
        return record

    def delete(self, name: str, *, environment: str) -> bool:
        return self._records.pop((name, environment), None) is not None

    def get(self, name: str, *, environment: str) -> CredentialMetadata | None:
        return self._records.get((name, environment))

    def list_metadata(self, *, environment: str | None = None) -> list[CredentialMetadata]:
        records = list(self._records.values())
        if environment is not None:
            records = [record for record in records if record.environment == environment]
        return sorted(records, key=lambda item: (item.environment, item.name))


class PostgresCredentialStore:
    """Postgres-backed credential store."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def ensure_schema(self) -> None:
        from pathlib import Path

        schema = (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema)
            conn.commit()

    def upsert(self, record: CredentialMetadata) -> CredentialMetadata:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO credentials (
                        name, environment, scope_repo_id, ciphertext, key_id,
                        fingerprint, updated_at, last_test_at, last_test_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name, environment) DO UPDATE SET
                        scope_repo_id = EXCLUDED.scope_repo_id,
                        ciphertext = EXCLUDED.ciphertext,
                        key_id = EXCLUDED.key_id,
                        fingerprint = EXCLUDED.fingerprint,
                        updated_at = EXCLUDED.updated_at,
                        last_test_at = EXCLUDED.last_test_at,
                        last_test_status = EXCLUDED.last_test_status
                    """,
                    (
                        record.name,
                        record.environment,
                        record.scope_repo_id,
                        record.ciphertext,
                        record.key_id,
                        record.fingerprint,
                        record.updated_at,
                        record.last_test_at,
                        record.last_test_status,
                    ),
                )
            conn.commit()
        return record

    def delete(self, name: str, *, environment: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM credentials WHERE name = %s AND environment = %s",
                    (name, environment),
                )
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def get(self, name: str, *, environment: str) -> CredentialMetadata | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name, environment, scope_repo_id, ciphertext, key_id,
                           fingerprint, updated_at, last_test_at, last_test_status
                    FROM credentials
                    WHERE name = %s AND environment = %s
                    """,
                    (name, environment),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return _row_to_metadata(row)

    def list_metadata(self, *, environment: str | None = None) -> list[CredentialMetadata]:
        query = """
            SELECT name, environment, scope_repo_id, ciphertext, key_id,
                   fingerprint, updated_at, last_test_at, last_test_status
            FROM credentials
        """
        params: tuple[str, ...] = ()
        if environment is not None:
            query += " WHERE environment = %s"
            params = (environment,)
        query += " ORDER BY environment, name"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        return [_row_to_metadata(row) for row in rows]


def _row_to_metadata(row: tuple) -> CredentialMetadata:
    return CredentialMetadata(
        name=row[0],
        environment=row[1],
        scope_repo_id=row[2],
        ciphertext=row[3],
        key_id=row[4],
        fingerprint=row[5],
        updated_at=row[6],
        last_test_at=row[7],
        last_test_status=row[8],
    )
