"""Refinement crew — PO-led sequential pipeline for backlog shaping."""

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


_crews_pkg = _load_module("crews_helpers", _CREWS_DIR / "__init__.py")
_agents_pkg = _load_module("agents_pkg", _ORCH_DIR / "agents" / "__init__.py")

create_po_agent = _agents_pkg.create_po_agent
filter_mutating_tools = _crews_pkg.filter_mutating_tools
with_dry_run_preamble = _crews_pkg.with_dry_run_preamble

REFINEMENT_TASK_SPECS: list[tuple[str, str, str]] = [
    (
        "read_planning_tree",
        (
            "Read the planning tree at .docs/planning/projects/{repo_id}/slice_dependency_tree.json "
            "and open questions for repo {repo_id}. Summarize current slice states, blockers, and "
            "operator questions that affect refinement."
        ),
        (
            "Planning tree summary with slice states, dependencies, and a list of open questions "
            "with file references."
        ),
    ),
    (
        "identify_refinement_goal",
        (
            "From operator input: {operator_input}\n"
            "Identify the refinement goal, scope boundaries, and success criteria for this session."
        ),
        (
            "Refinement goal statement with scope, out-of-scope items, and measurable success criteria."
        ),
    ),
    (
        "shape_slices",
        (
            "Shape new or updated slices with acceptance criteria, operator gates, tier, effort, and "
            "automation eligibility. Align with the planning tree for repo {repo_id}."
        ),
        (
            "Structured slice proposals table: slice_id, title, acceptance criteria, operator gates, "
            "dependencies, and automation eligibility."
        ),
    ),
    (
        "write_slice_docs",
        (
            "Draft slice detail doc content for each shaped slice under "
            ".docs/planning/projects/{repo_id}/slices/. Include goal, deliverables, dependencies, "
            "acceptance, and operator gates."
        ),
        (
            "Slice detail doc drafts with complete markdown sections ready for PO review."
        ),
    ),
    (
        "validate_and_recommend",
        (
            "Validate the planning tree for repo {repo_id} and recommend the next slice for "
            "implementation based on shaped work and dependency order."
        ),
        (
            "Refinement summary with tree validation result, promotion risks, and a recommended "
            "next slice with rationale."
        ),
    ),
]


def _build_tasks(agent: Any, *, dry_run: bool) -> list[Task]:
    tasks: list[Task] = []
    previous: Task | None = None
    for _name, description, expected_output in REFINEMENT_TASK_SPECS:
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


def create_refinement_crew(
    *,
    dry_run: bool = False,
    repo_id: str = "drake-governance",
    operator_input: str = "",
    **_: Any,
) -> Crew:
    """Build the PO-led refinement crew with five sequential tasks."""
    agent = create_po_agent()
    if dry_run:
        agent = create_po_agent(tools=filter_mutating_tools(agent.tools or [], dry_run=True))
    tasks = _build_tasks(agent, dry_run=dry_run)
    return Crew(
        agents=[agent],
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
        memory=False,
    )
