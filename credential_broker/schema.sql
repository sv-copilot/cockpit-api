-- Credential broker schema for cockpit-api (slice #105)

CREATE TABLE IF NOT EXISTS credentials (
    name TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT 'staging',
    scope_repo_id TEXT,
    ciphertext TEXT NOT NULL,
    key_id TEXT NOT NULL DEFAULT 'v1',
    fingerprint TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_test_at TIMESTAMPTZ,
    last_test_status TEXT,
    PRIMARY KEY (name, environment)
);

CREATE INDEX IF NOT EXISTS credentials_scope_repo_id_idx
    ON credentials (scope_repo_id);
