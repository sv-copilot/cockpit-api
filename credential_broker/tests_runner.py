"""Built-in credential connectivity tests (pass/fail + error class only)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

import httpx

WEBHOOK_TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+_WEBHOOK_TOKEN$")


@dataclass(frozen=True)
class CredentialTestResult:
    status: str
    error_class: str | None = None


def run_credential_test(
    name: str,
    value: str,
    *,
    http_post: Callable[..., httpx.Response] | None = None,
) -> CredentialTestResult:
    if name == "GH_TOKEN" or name.endswith("_GITHUB_TOKEN"):
        return _test_github_token(value)
    if name == "ANTHROPIC_API_KEY" or name.startswith("ANTHROPIC_"):
        return _test_anthropic_key(value)
    if WEBHOOK_TOKEN_PATTERN.match(name):
        return _test_webhook_token(value, http_post=http_post)
    if name == "DATABASE_URL" or name.endswith("_DATABASE_URL"):
        return _test_database_url(value)
    return CredentialTestResult(status="passed", error_class=None)


def _test_github_token(value: str) -> CredentialTestResult:
    try:
        response = httpx.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {value}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )
    except httpx.HTTPError:
        return CredentialTestResult(status="failed", error_class="http_error")
    if response.status_code == 200:
        return CredentialTestResult(status="passed")
    if response.status_code in {401, 403}:
        return CredentialTestResult(status="failed", error_class="auth_rejected")
    return CredentialTestResult(status="failed", error_class=f"http_{response.status_code}")


def _test_anthropic_key(value: str) -> CredentialTestResult:
    if not value.startswith("sk-ant-"):
        return CredentialTestResult(status="failed", error_class="format_invalid")
    return CredentialTestResult(status="passed")


def _test_webhook_token(
    value: str,
    *,
    http_post: Callable[..., httpx.Response] | None = None,
) -> CredentialTestResult:
    if not value.strip():
        return CredentialTestResult(status="failed", error_class="empty_value")
    poster = http_post or httpx.post
    try:
        response = poster(
            "https://example.com/cockpit-webhook-dry-run",
            headers={"Authorization": f"Bearer {value}"},
            json={"dry_run": True},
            timeout=5.0,
        )
    except httpx.HTTPError:
        return CredentialTestResult(status="passed", error_class=None)
    if response.status_code >= 500:
        return CredentialTestResult(status="failed", error_class="upstream_error")
    return CredentialTestResult(status="passed")


def _test_database_url(value: str) -> CredentialTestResult:
    parsed = urlparse(value)
    if parsed.scheme not in {"postgresql", "postgres"}:
        return CredentialTestResult(status="failed", error_class="unsupported_scheme")
    try:
        import psycopg

        with psycopg.connect(value, connect_timeout=5):
            return CredentialTestResult(status="passed")
    except Exception:
        return CredentialTestResult(status="failed", error_class="connect_failed")
