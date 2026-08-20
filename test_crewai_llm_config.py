"""Tests for CrewAI agent LLM wiring (COCKPIT-API-CREWAI-MODEL-ROUTING-1).

Asserts each agent factory passes an explicit DeepSeek LLM to `Agent(...)` and
that verbose logging is off. `crewai` is mocked (mock-captured, no network) and
`tools=[]` is passed so tool modules are never imported.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent
ORCH_ROOT = REPO_ROOT / "crewai_orchestrator"


class _MockLLM:
    """Preserves constructor kwargs so tests can inspect model/base_url/etc."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _MockAgent:
    """Preserves constructor kwargs so tests can inspect role/goal/llm/verbose."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def crewai_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    mock.__path__ = []
    mock.Agent = _MockAgent
    mock.LLM = _MockLLM
    monkeypatch.setitem(sys.modules, "crewai", mock)
    return mock


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_agent(stem: str):
    return _load_module(f"cockpit_{stem}", ORCH_ROOT / "agents" / f"{stem}.py")


def _agent_llm_model(factory, *args, **kwargs) -> str:
    agent = factory(*args, **kwargs)
    llm = getattr(agent, "llm", None)
    assert llm is not None, f"{factory.__name__} produced an Agent without llm="
    return llm.model


class TestAgentLLMIdentity:
    """Every agent factory must bind an explicit DeepSeek model via llm=."""

    def test_product_owner_uses_pro(self, crewai_mock: MagicMock) -> None:
        po = _load_agent("product_owner")
        assert _agent_llm_model(po.create_po_agent, tools=[]) == "deepseek-v4-pro"

    def test_engineering_lead_uses_pro(self, crewai_mock: MagicMock) -> None:
        el = _load_agent("engineering_lead")
        assert _agent_llm_model(el.create_el_agent, tools=[]) == "deepseek-v4-pro"

    def test_release_manager_uses_flash(self, crewai_mock: MagicMock) -> None:
        rm = _load_agent("release_manager")
        assert _agent_llm_model(rm.create_rm_agent, tools=[]) == "deepseek-v4-flash"

    def test_infrastructure_steward_uses_flash(self, crewai_mock: MagicMock) -> None:
        steward = _load_agent("infrastructure_steward")
        assert _agent_llm_model(steward.create_steward_agent, tools=[]) == "deepseek-v4-flash"


class TestAgentVerboseOff:
    """All agent factories must set verbose=False."""

    def test_all_agents_verbose_off(self, crewai_mock: MagicMock) -> None:
        factories = [
            _load_agent("product_owner").create_po_agent,
            _load_agent("engineering_lead").create_el_agent,
            _load_agent("release_manager").create_rm_agent,
            _load_agent("infrastructure_steward").create_steward_agent,
        ]
        for factory in factories:
            agent = factory(tools=[])
            assert agent.verbose is False, f"{factory.__name__} must have verbose=False"
