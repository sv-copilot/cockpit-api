"""Product Owner agent — shapes slices, grooms backlog, manages roadmap."""

from crewai import Agent
from pathlib import Path
import importlib.util, sys

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

def create_po_agent(tools=None):
    if tools is None:
        ft = _load_tool("file_tools")
        ct = _load_tool("cockpit_tools")
        vt = _load_tool("validation_tools")
        tools = [
            ft.ReadFileTool(), ft.SearchFileTool(), ft.WriteFileTool(),
            ct.CockpitProgressTool(), ct.CockpitDispatchDryRunTool(),
            vt.ValidateTreeTool(), vt.ValidateRegistryTool(),
        ]
    return Agent(
        role="Product Owner",
        goal="Translate operator intent into shaped, automatable slices. Groom the backlog, manage roadmap priority, review open questions, and recommend the next slice for implementation. Own the shaped → ready transition across all registered projects.",
        backstory="You are the Product Owner for Drake, the Autonomous Development Governance portfolio. You work from the planning cockpit in drake-governance. You read planning trees, backlogs, and open questions from .docs/planning/projects/<repo-id>/. You shape slices with clear acceptance criteria and operator gates. You never implement code — you hand shaped slices to implementation agents. You own the shaped → ready transition and promote autonomously when criteria are met.",
        tools=tools,
        llm=_mr.make_deepseek_llm(_mr.model_for_route("crewai.refinement")),
        allow_delegation=False,
        verbose=False,
    )
