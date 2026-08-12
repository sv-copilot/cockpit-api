"""Health check for the CrewAI background runner."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HEARTBEAT_PATH = Path(__file__).resolve().parent / ".runner_heartbeat.json"
DEFAULT_MAX_AGE_SECONDS = 300


def _parse_iso8601(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def check_health(
    *,
    heartbeat_path: Path | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> int:
    """Return 0 when runner heartbeat is fresh and process is alive, else 1."""
    path = heartbeat_path or DEFAULT_HEARTBEAT_PATH
    if not path.exists():
        return 1

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1

    last_poll_raw = str(payload.get("last_poll_at") or "")
    last_poll = _parse_iso8601(last_poll_raw)
    if last_poll is None:
        return 1

    age_seconds = (datetime.now(timezone.utc) - last_poll).total_seconds()
    if age_seconds > max_age_seconds:
        return 1

    pid = payload.get("pid")
    if isinstance(pid, int) and not _process_alive(pid):
        return 1

    return 0


def main() -> int:
    heartbeat_path = Path(os.getenv("CREWAI_RUNNER_HEARTBEAT_PATH", str(DEFAULT_HEARTBEAT_PATH)))
    max_age = int(os.getenv("CREWAI_RUNNER_HEALTH_MAX_AGE_S", str(DEFAULT_MAX_AGE_SECONDS)))
    return check_health(heartbeat_path=heartbeat_path, max_age_seconds=max_age)


if __name__ == "__main__":
    sys.exit(main())
