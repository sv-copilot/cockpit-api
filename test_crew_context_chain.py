"""Tests for CrewAI crew context chaining (COCKPIT-API-CREWAI-MODEL-ROUTING-1).

Mechanical crews (sync, handmaiden) must NOT chain `context=[previous]`; their
tasks are independent. Reasoning crews (refinement, audit) may forward only the
immediately-prior task (never an accumulated context list).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent
ORCH_ROOT = REPO_ROOT / "crewai_orchestrator"


class _MockTask:
    """Preserves constructor kwargs as attributes for test assertions."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockAgent:
    """Preserves constructor kwargs so tests can inspect role/goal/llm/verbose."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockLLM:
    """Preserves constructor kwargs."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockCrew:
    """Preserves constructor kwargs so tests can inspect verbose/memory."""

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

    # Tool modules import from both crewai.tools and the legacy crewai_tools.
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


def _load_crew(stem: str):
    return _load_module(f"cockpit_crew_{stem}", ORCH_ROOT / "crews" / f"{stem}.py")


def _context_of(task) -> object:
    return getattr(task, "context", None)


class TestMechanicalCrewsHaveNoContextChaining:
    """sync and handmaiden tasks must not chain context=[previous]."""

    def test_sync_crew_tasks_have_no_context(self, crewai_mock: MagicMock) -> None:
        sync = _load_crew("sync_crew")
        tasks = sync._build_tasks(MagicMock(), dry_run=False)
        assert len(tasks) >= 2
        for task in tasks:
            assert _context_of(task) is None, f"sync task must not chain context: {task}"

    def test_handmaiden_task_has_no_context(self, crewai_mock: MagicMock) -> None:
        handmaiden = _load_crew("handmaiden")
        mock_agent = _MockAgent(role="Infrastructure Steward", goal="healthy")
        with patch.object(handmaiden, "create_steward_agent", return_value=mock_agent):
            crew = handmaiden.HandmaidenCrew()
        task = crew.health_sweep_task()
        assert _context_of(task) is None


class TestReasoningCrewsForwardOnlyPriorTask:
    """refinement and audit may forward only the immediately-prior task."""

    def test_refinement_tasks_forward_single_prior_task(self, crewai_mock: MagicMock) -> None:
        refinement = _load_crew("refinement_crew")
        tasks = refinement._build_tasks(MagicMock(), dry_run=False)
        assert len(tasks) >= 3
        assert _context_of(tasks[0]) is None
        for index in range(1, len(tasks)):
            context = _context_of(tasks[index])
            assert context == [tasks[index - 1]], (
                f"refinement task {index} must forward only the prior task, got {context!r}"
            )

    def test_audit_tasks_forward_single_prior_task(self, crewai_mock: MagicMock) -> None:
        audit = _load_crew("audit_crew")
        tasks = audit._build_tasks(MagicMock(), dry_run=False)
        assert len(tasks) >= 3
        assert _context_of(tasks[0]) is None
        for index in range(1, len(tasks)):
            context = _context_of(tasks[index])
            assert context == [tasks[index - 1]], (
                f"audit task {index} must forward only the prior task, got {context!r}"
            )


class TestCrewVerboseOff:
    """Every crew factory must build its Crew with verbose=False."""

    def test_refinement_crew_verbose_off(self, crewai_mock: MagicMock) -> None:
        refinement = _load_crew("refinement_crew")
        crew = refinement.create_refinement_crew(dry_run=True)
        assert crew.verbose is False

    def test_audit_crew_verbose_off(self, crewai_mock: MagicMock) -> None:
        audit = _load_crew("audit_crew")
        crew = audit.create_audit_crew(dry_run=True)
        assert crew.verbose is False

    def test_sync_crew_verbose_off(self, crewai_mock: MagicMock) -> None:
        sync = _load_crew("sync_crew")
        crew = sync.create_sync_crew(dry_run=True)
        assert crew.verbose is False

    def test_handmaiden_crew_verbose_off(self, crewai_mock: MagicMock) -> None:
        handmaiden = _load_crew("handmaiden")
        mock_agent = _MockAgent(role="Infrastructure Steward", goal="healthy")
        with patch.object(handmaiden, "create_steward_agent", return_value=mock_agent):
            crew = handmaiden.HandmaidenCrew()
            built = crew._build_crew()
        assert built.verbose is False
