"""Regression tests for COCKPIT-API-SYNC-KICKOFF-1 (#207).

The sync crew's task descriptions interpolate ``{pr_number}`` and ``{pr_title}``
(see ``crewai_orchestrator/crews/sync_crew.py``). Before the fix,
``run_crew_kickoff`` never placed those values into ``kickoff_inputs``, so
CrewAI raised ``Missing required template variable 'pr_number'`` at kickoff.
These tests guard that contract: the planner context must expose the PR
identity, and ``run_crew_kickoff`` must forward it into ``kickoff_inputs``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent
ORCH_ROOT = REPO_ROOT / "crewai_orchestrator"


class _MockTask:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockAgent:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockLLM:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockCrew:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockBaseTool:
    name: str = ""
    description: str = ""


@pytest.fixture
def crewai_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    mock.__path__ = []
    mock.Agent = _MockAgent
    mock.LLM = _MockLLM
    mock.Crew = _MockCrew
    mock.Process = MagicMock()
    mock.Task = _MockTask
    monkeypatch.setitem(sys.modules, "crewai", mock)

    mock_tools = MagicMock()
    mock_tools.BaseTool = _MockBaseTool
    monkeypatch.setitem(sys.modules, "crewai.tools", mock_tools)
    monkeypatch.setitem(sys.modules, "crewai_tools", mock_tools)
    return mock


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_context_module():
    return _load_module("cockpit_context_207", ORCH_ROOT / "context.py")


def _write_planning_tree(planning_path: Path, slices: list[dict]) -> None:
    tree_dir = planning_path / ".docs/planning/projects" / "drake-governance"
    tree_dir.mkdir(parents=True, exist_ok=True)
    (tree_dir / "slice_dependency_tree.json").write_text(
        json.dumps({"slices": slices})
    )
    (tree_dir / "slices").mkdir(parents=True, exist_ok=True)
    (planning_path / ".docs/projects-registry.json").write_text(
        json.dumps({"projects": [{"id": "drake-governance"}]})
    )


def _slice_record(
    slice_id: str, *, title: str = "A title", posture_pr=None, top_pr=None
) -> dict:
    return {
        "slice_id": slice_id,
        "slice_number": 1,
        "title": title,
        "state": "validated",
        "branch_posture": {
            "implementation_repo_id": "drake-governance",
            "last_known_pr": posture_pr,
        },
        "last_known_pr": top_pr,
    }


class TestResolvePrIdentity:
    def test_pr_number_from_branch_posture_last_known_pr(self, tmp_path: Path) -> None:
        ctx_mod = _load_context_module()
        _write_planning_tree(
            tmp_path,
            [_slice_record("SYNC-TEST-1", title="Sync me", posture_pr=108)],
        )
        ctx = ctx_mod.resolve_planning_context(
            tmp_path, "drake-governance", "SYNC-TEST-1"
        )
        assert ctx["pr_number"] == "108"
        assert ctx["pr_title"] == "Sync me"

    def test_pr_number_falls_back_to_top_level(self, tmp_path: Path) -> None:
        ctx_mod = _load_context_module()
        _write_planning_tree(
            tmp_path, [_slice_record("SYNC-TEST-1", title="T", top_pr=42)]
        )
        ctx = ctx_mod.resolve_planning_context(
            tmp_path, "drake-governance", "SYNC-TEST-1"
        )
        assert ctx["pr_number"] == "42"

    def test_unknown_pr_falls_back_to_empty(self, tmp_path: Path) -> None:
        ctx_mod = _load_context_module()
        _write_planning_tree(
            tmp_path, [_slice_record("SYNC-TEST-1", title="No PR")]
        )
        ctx = ctx_mod.resolve_planning_context(
            tmp_path, "drake-governance", "SYNC-TEST-1"
        )
        assert ctx["pr_number"] == ""
        assert ctx["pr_title"] == "No PR"


class TestSyncCrewTemplateContract:
    def test_sync_tasks_require_pr_template_vars(self, crewai_mock: MagicMock) -> None:
        sync = _load_module(
            "cockpit_sync_crew_207", ORCH_ROOT / "crews" / "sync_crew.py"
        )
        tasks = sync._build_tasks(_MockAgent(), dry_run=False)
        descriptions = " ".join(getattr(task, "description", "") for task in tasks)
        assert "{pr_number}" in descriptions
        assert "{pr_title}" in descriptions


class TestKickoffInputs:
    def test_run_crew_kickoff_passes_pr_identity_into_inputs(
        self, crewai_mock: MagicMock
    ) -> None:
        import crewai_routes  # imported AFTER crewai is mocked

        context = {
            "repo_id": "drake-governance",
            "slice_id": "SYNC-TEST-1",
            "operator_input": "op",
            "slice_detail": "detail",
            "pr_number": "108",
            "pr_title": "Sync me",
        }
        fake_crew = MagicMock()
        fake_crew.tasks = []
        with patch.object(crewai_routes, "create_crew", return_value=fake_crew):
            crewai_routes.run_crew_kickoff("sync", context, dry_run=True)

        fake_crew.kickoff.assert_called_once()
        inputs = fake_crew.kickoff.call_args.kwargs["inputs"]
        assert inputs["pr_number"] == "108"
        assert inputs["pr_title"] == "Sync me"

    def test_run_crew_kickoff_falls_back_to_empty_strings(
        self, crewai_mock: MagicMock
    ) -> None:
        import crewai_routes

        context = {
            "repo_id": "drake-governance",
            "slice_id": "SYNC-TEST-1",
            "operator_input": "op",
            "slice_detail": "detail",
        }
        fake_crew = MagicMock()
        fake_crew.tasks = []
        with patch.object(crewai_routes, "create_crew", return_value=fake_crew):
            crewai_routes.run_crew_kickoff("sync", context, dry_run=True)

        inputs = fake_crew.kickoff.call_args.kwargs["inputs"]
        assert inputs["pr_number"] == ""
        assert inputs["pr_title"] == ""
