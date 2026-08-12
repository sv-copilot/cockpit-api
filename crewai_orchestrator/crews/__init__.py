"""Crew factory — refinement, audit, and sync crew compositions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from crewai import Crew

_CREWS_DIR = Path(__file__).resolve().parent
_ORCH_DIR = _CREWS_DIR.parent

DRY_RUN_PREAMBLE = (
    "DRY-RUN MODE: Do not write, commit, push, merge, or modify any files. "
    "Produce analysis, drafts, and recommendations only. "
    "Explicit task authorization is required before any mutating action."
)

MUTATING_TOOL_NAMES = frozenset(
    {
        "write_file",
        "git_commit",
        "git_push",
        "gh_pr_create",
        "gh_pr_merge",
    }
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_crew_module(stem: str):
    return _load_module(stem, _CREWS_DIR / f"{stem}.py")


def filter_mutating_tools(tools: list[Any], *, dry_run: bool) -> list[Any]:
    """Return a tool list with mutating tools removed when dry_run is True."""
    if not dry_run:
        return list(tools)
    return [tool for tool in tools if getattr(tool, "name", None) not in MUTATING_TOOL_NAMES]


def with_dry_run_preamble(description: str, *, dry_run: bool) -> str:
    if dry_run:
        return f"{DRY_RUN_PREAMBLE}\n\n{description}"
    return description


def create_crew(
    crew_type: str,
    *,
    dry_run: bool = False,
    **context: Any,
) -> Crew:
    """Instantiate a crew by type name.

    Supported crew_type values: refinement, audit, sync.
    """
    factories = {
        "refinement": _load_crew_module("refinement_crew").create_refinement_crew,
        "audit": _load_crew_module("audit_crew").create_audit_crew,
        "sync": _load_crew_module("sync_crew").create_sync_crew,
    }
    factory = factories.get(crew_type)
    if factory is None:
        raise ValueError(f"unknown crew_type: {crew_type}")
    return factory(dry_run=dry_run, **context)


__all__ = [
    "DRY_RUN_PREAMBLE",
    "MUTATING_TOOL_NAMES",
    "create_crew",
    "filter_mutating_tools",
    "with_dry_run_preamble",
]
