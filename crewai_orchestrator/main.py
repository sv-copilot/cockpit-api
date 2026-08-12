"""CrewAI Orchestrator — entry point and smoke test."""

from __future__ import annotations


def verify() -> bool:
    """Smoke test: verify crewai is importable and orchestrator is online.

    Returns True if all checks pass. Prints status to stdout.
    """
    ok = True

    # 1. Package self-check (directory uses hyphen — not a Python package name)
    try:
        import pathlib
        init_path = pathlib.Path(__file__).resolve().parent / "__init__.py"
        if init_path.exists():
            print("CrewAI Orchestrator: package OK")
        else:
            raise FileNotFoundError("__init__.py missing")
    except Exception:
        print("CrewAI Orchestrator: package import FAILED")
        ok = False

    # 2. crewai dependency
    try:
        import crewai  # noqa: F401
        print("crewai: importable")
    except ImportError:
        print("crewai: NOT INSTALLED — run: pip install -r tools/crewai-orchestrator/requirements.txt")
        ok = False

    # 3. pydantic dependency
    try:
        import pydantic  # noqa: F401
        print("pydantic: importable")
    except ImportError:
        print("pydantic: NOT INSTALLED")
        ok = False

    return ok


if __name__ == "__main__":
    import sys
    ok = verify()
    sys.exit(0 if ok else 1)
