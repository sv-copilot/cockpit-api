"""GitHub webhook receiver — triggers orchestrator on PR merge events.

Receives push/PR events from GitHub, validates the webhook secret, and
triggers an orchestrator cycle when a PR is merged to ai-dev.

Webhook setup on GitHub:
  URL:  https://cockpit-api-staging.spencervaradi.com/webhook/github
  Events: Pull requests, Push
  Secret: COCKPIT_GITHUB_WEBHOOK_SECRET (set in .env)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/webhook", tags=["webhooks"])

PLANNING_PATH = Path(os.getenv("PLANNING_CHECKOUT_PATH", "/data/planning/drake-governance"))


def _verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Verify GitHub webhook signature (HMAC-SHA256)."""
    secret = os.getenv("COCKPIT_GITHUB_WEBHOOK_SECRET", "").strip()
    if not secret:
        # No secret configured — accept all (dev mode)
        return True
    if not signature_header:
        return False
    # Expected format: sha256=<hex>
    try:
        algo, sig = signature_header.split("=", 1)
        if algo != "sha256":
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _trigger_orchestrator() -> dict:
    """Run one orchestrator cycle. Non-blocking — fires and returns."""
    orchestrator = PLANNING_PATH / "tools" / "headless-runner" / "orchestrator.py"
    if not orchestrator.exists():
        return {"status": "skipped", "reason": "orchestrator not found"}

    try:
        result = subprocess.run(
            ["python3", str(orchestrator), "--once"],
            capture_output=True, text=True, timeout=120, cwd=str(PLANNING_PATH),
            env={**os.environ, "PLANNING_REPO_PATH": str(PLANNING_PATH)},
        )
        # Extract summary from output
        summary = "cycle complete"
        for line in (result.stdout + result.stderr).splitlines():
            if "PHASE:" in line or "FAN-OUT:" in line or "AUDIT" in line or "REFINE" in line:
                summary = line.strip()
        return {
            "status": "triggered",
            "exit_code": result.returncode,
            "summary": summary,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": "orchestrator did not complete within 120s"}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
):
    """Receive GitHub webhook events. Triggers orchestrator on PR merge/push to ai-dev."""
    body = await request.body()

    # Validate signature
    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(401, "Invalid webhook signature")

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")

    event = x_github_event or "unknown"
    action = payload.get("action", "")

    # Determine if this should trigger the orchestrator
    should_trigger = False
    trigger_reason = ""

    if event == "pull_request" and action == "closed" and payload.get("pull_request", {}).get("merged"):
        base_ref = payload["pull_request"].get("base", {}).get("ref", "")
        if base_ref == "ai-dev" or base_ref == "refs/heads/ai-dev":
            should_trigger = True
            trigger_reason = f"PR #{payload['pull_request']['number']} merged to ai-dev"

    elif event == "push":
        ref = payload.get("ref", "")
        if ref in ("refs/heads/ai-dev", "ai-dev"):
            should_trigger = True
            trigger_reason = f"push to ai-dev ({payload.get('head_commit', {}).get('id', 'unknown')[:7]})"

    elif event == "ping":
        return {"status": "ok", "message": "webhook configured correctly"}

    if should_trigger:
        result = _trigger_orchestrator()
        return {
            "status": "ok",
            "event": event,
            "trigger": trigger_reason,
            "orchestrator": result,
        }

    return {"status": "ignored", "event": event, "action": action, "reason": "not a merge to ai-dev"}


@router.post("/trigger")
async def manual_trigger():
    """Manual trigger for orchestrator — useful for daily cron."""
    result = _trigger_orchestrator()
    return {"status": "ok", "orchestrator": result}
