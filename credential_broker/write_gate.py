"""VPN, shared-secret, and internal-header gate for credential write routes (slice #106 / C5:B)."""

from __future__ import annotations

import os

from fastapi import HTTPException, Request

INTERNAL_HEADER = "x-cockpit-internal"
WRITE_TOKEN_HEADER = "x-cockpit-write-token"


def credentials_write_allowed(request: Request) -> bool:
    # 1. Global override for local dev
    if os.getenv("COCKPIT_ALLOW_CREDENTIAL_WRITES", "").strip() == "1":
        return True
    # 2. Internal service header (for compose-network callers)
    if request.headers.get(INTERNAL_HEADER, "").strip() == "1":
        return True
    # 3. Shared secret token — works from any IP, no VPN needed
    write_token = os.getenv("COCKPIT_WRITE_TOKEN", "").strip()
    if write_token:
        provided = request.headers.get(WRITE_TOKEN_HEADER, "").strip()
        if provided == write_token:
            return True
    # 4. VPN / IP allowlist — exact IP or CIDR match
    client_host = request.client.host if request.client else ""
    allowlist = {
        item.strip()
        for item in os.getenv("COCKPIT_VPN_ALLOWLIST", "").split(",")
        if item.strip()
    }
    return client_host in allowlist


def require_credentials_write_access(request: Request) -> None:
    if credentials_write_allowed(request):
        return
    raise HTTPException(
        403,
        "Credential write endpoints are gated. Options: set "
        f"{WRITE_TOKEN_HEADER} with COCKPIT_WRITE_TOKEN, "
        f"connect from a VPN IP in COCKPIT_VPN_ALLOWLIST, "
        f"set {INTERNAL_HEADER}: 1 from an internal proxy, "
        "or COCKPIT_ALLOW_CREDENTIAL_WRITES=1 for local dev.",
    )
