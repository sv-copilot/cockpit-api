"""Audit crew — EL-led sequential pipeline for technical audits."""

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


_crews_pkg = _load_module("crews_helpers_audit", _CREWS_DIR / "__init__.py")
_agents_pkg = _load_module("agents_pkg_audit", _ORCH_DIR / "agents" / "__init__.py")

create_el_agent = _agents_pkg.create_el_agent
filter_mutating_tools = _crews_pkg.filter_mutating_tools
with_dry_run_preamble = _crews_pkg.with_dry_run_preamble

AUDIT_TASK_SPECS: list[tuple[str, str, str]] = [
    (
        "build_snapshot",
        (
            "Build a technical current-state snapshot for repo {repo_id}. Cover architecture, "
            "test coverage signals, CI posture, and notable technical debt with file references."
        ),
        (
            "Current-state snapshot with architecture overview, test/CI posture, and file-referenced "
            "observations."
        ),
    ),
    (
        "classify_findings",
        (
            "Classify audit findings by severity (critical, high, medium, low, informational). "
            "Each finding must include a file reference and remediation hint."
        ),
        (
            "Findings table classified by severity with file paths, summary, and remediation hints."
        ),
    ),
    (
        "propose_remediation_slices",
        (
            "Propose remediation slices that address classified findings for repo {repo_id}. "
            "Include acceptance criteria and dependency links."
        ),
        (
            "Remediation slice proposals with severity mapping, acceptance criteria, and dependencies."
        ),
    ),
    (
        "write_adr",
        (
            "If architecture changes are required, draft an ADR covering context, decision, "
            "consequences, and alternatives. Skip with rationale when no architecture change is needed."
        ),
        (
            "ADR draft or explicit no-ADR rationale when architecture is unchanged."
        ),
    ),
    (
        "update_slice_notes",
        (
            "Update slice detail docs with technical notes for affected slices under "
            ".docs/planning/projects/{repo_id}/slices/."
        ),
        (
            "Technical notes appendix for affected slices with severity-tagged findings and "
            "implementation guidance."
        ),
    ),
]


def _build_tasks(agent: Any, *, dry_run: bool) -> list[Task]:
    tasks: list[Task] = []
    previous: Task | None = None
    for _name, description, expected_output in AUDIT_TASK_SPECS:
        kwargs: dict[str, Any] = {
            "description": with_dry_run_preamble(description, dry_run=dry_run),
            "expected_output": expected_output,
            "agent": agent,
        }
        if previous is not None:
            kwargs["context"] = [previous]
        task = Task(**kwargs)
        tasks.append(task)
        previous = task
    return tasks


def create_audit_crew(
    *,
    dry_run: bool = False,
    repo_id: str = "drake-governance",
    **_: Any,
) -> Crew:
    """Build the EL-led audit crew with five sequential tasks."""
    agent = create_el_agent()
    if dry_run:
        agent = create_el_agent(tools=filter_mutating_tools(agent.tools or [], dry_run=True))
    tasks = _build_tasks(agent, dry_run=dry_run)
    return Crew(
        agents=[agent],
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
        memory=False,
    )
