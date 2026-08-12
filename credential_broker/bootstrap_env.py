#!/usr/bin/env python3
"""Hydrate process env vars from the encrypted credential broker at startup."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from credential_broker.broker import bootstrap_env_from_broker  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--names",
        default=os.getenv("CREDENTIAL_BOOTSTRAP_REFS", ""),
        help="Comma-separated credential env var names to hydrate",
    )
    parser.add_argument(
        "--environment",
        default=os.getenv("CREDENTIALS_INVENTORY_ENV", "staging"),
        help="Broker environment tier",
    )
    args = parser.parse_args(argv)
    names = [part.strip() for part in args.names.split(",") if part.strip()]
    if not names:
        print("no credential names requested", file=sys.stderr)
        return 0
    hydrated = bootstrap_env_from_broker(names, environment=args.environment)
    print(f"hydrated {len(hydrated)} credential(s) from broker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
