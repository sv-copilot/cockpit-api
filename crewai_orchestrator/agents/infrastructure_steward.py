"""
Infrastructure Steward agent definition.
"""
import importlib.util
import sys
from pathlib import Path

from crewai import Agent


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent.parent / "tools" / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_model_routing():
    spec = importlib.util.spec_from_file_location(
        "model_routing", Path(__file__).resolve().parent.parent / "model_routing.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["model_routing"] = mod
    spec.loader.exec_module(mod)
    return mod


_mr = _load_model_routing()


def create_steward_agent(tools=None) -> Agent:
    """
    Creates and returns the Infrastructure Steward agent.

    Returns:
        Agent: configured CrewAI agent
    """
    if tools is None:
        docker_tool = _load_tool("docker_tool")
        system_stats_tool = _load_tool("system_stats_tool")
        cockpit_health_tool = _load_tool("cockpit_health_tool")
        log_scanner_tool = _load_tool("log_scanner_tool")
        tools = [
            docker_tool.DockerTool(),
            system_stats_tool.SystemStatsTool(),
            cockpit_health_tool.CockpitHealthTool(),
            log_scanner_tool.LogScannerTool(),
        ]
    return Agent(
        role="Infrastructure Steward",
        goal=(
            "Keep the Drake VPS and all cockpit services healthy. "
            "Detect container crashes within 60 seconds, API degradation within 2 polls, "
            "and never let a crash go unreported for more than 5 minutes."
        ),
        backstory=(
            "You are the silent guardian of the Drake operations layer on the Hetzner CX33 VPS. "
            "You watch Docker containers, system resources, API endpoints, and logs. "
            "You do not shape work (that's the PO) or audit code (that's the EL) — you keep the lights on. "
            "You write structured problem reports so the PO can slice fixes. "
            "You never restart a container without explicit operator approval (that comes in a later slice)."
        ),
        tools=tools,
        llm=_mr.make_deepseek_llm(_mr.model_for_route("crewai.handmaiden")),
        allow_delegation=False,
        verbose=False,
    )
