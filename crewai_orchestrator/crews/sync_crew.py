"""Sync crew — RM-led sequential pipeline for tree sync after merge."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from crewai import Crew, Process, Task

_CREWS_DIR = Path(__file__).resolve().parent
_ORCH_DIR = _CREWS_DIR.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_crews_pkg = _load_module("crews_helpers_sync", _CREWS_DIR / "__init__.py")
_agents_pkg = _load_module("agents_pkg_sync", _ORCH_DIR / "agents" / "__init__.py")

create_rm_agent = _agents_pkg.create_rm_agent
filter_mutating_tools = _crews_pkg.filter_mutating_tools
with_dry_run_preamble = _crews_pkg.with_dry_run_preamble

SYNC_TASK_SPECS: list[tuple[str, str, str]] = [
    (
        "confirm_pr_merge",
        (
            "Confirm PR #{pr_number} ({pr_title}) is merged via gh CLI for repo {repo_id}. "
            "Capture merge commit, base branch, and merge timestamp."
        ),
        (
            "PR merge confirmation with PR number, title, merge commit SHA, base branch, and status."
        ),
    ),
    (
        "identify_slice",
        (
            "Identify the slice ID from PR title: {pr_title}. Map to planning tree entry and "
            "current lifecycle state."
        ),
        (
            "Resolved slice_id, title, and pre-sync lifecycle state with file references."
        ),
    ),
    (
        "update_tree_state",
        (
            "Update slice state and branch posture in .docs/planning/projects/{repo_id}/ for the "
            "identified slice. Record validated or promoted transitions as appropriate."
        ),
        (
            "State transition table: slice_id, prior state, new state, branch posture, and rationale."
        ),
    ),
    (
        "validate_tree",
        (
            "Validate the planning tree and registry for repo {repo_id} after state updates."
        ),
        (
            "Tree validation report with pass/fail checks and any schema or dependency errors."
        ),
    ),
    (
        "commit_and_check_gaps",
        (
            "Prepare commit message and changed files for tree sync. Check promotion gaps across "
            "the portfolio and list blockers for ai-dev → dev → main."
        ),
        (
            "Commit plan (or dry-run draft), promotion gap summary, and recommended follow-up actions."
        ),
    ),
]


def _build_tasks(agent: Any, *, dry_run: bool) -> list[Task]:
    tasks: list[Task] = []
    for _name, description, expected_output in SYNC_TASK_SPECS:
        kwargs: dict[str, Any] = {
            "description": with_dry_run_preamble(description, dry_run=dry_run),
            "expected_output": expected_output,
            "agent": agent,
        }
        task = Task(**kwargs)
        tasks.append(task)
    return tasks


def create_sync_crew(
    *,
    dry_run: bool = False,
    repo_id: str = "drake-governance",
    pr_title: str = "",
    pr_number: str = "",
    **_: Any,
) -> Crew:
    """Build the RM-led sync crew with five sequential tasks."""
    agent = create_rm_agent()
    if dry_run:
        agent = create_rm_agent(tools=filter_mutating_tools(agent.tools or [], dry_run=True))
    tasks = _build_tasks(agent, dry_run=dry_run)
    return Crew(
        agents=[agent],
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
        memory=False,
    )
