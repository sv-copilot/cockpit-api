"""Engineering Lead agent — technical audits, architecture, code quality."""

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

def create_el_agent(tools=None):
    if tools is None:
        ft = _load_tool("file_tools")
        ct = _load_tool("cockpit_tools")
        vt = _load_tool("validation_tools")
        tools = [
            ft.ReadFileTool(), ft.SearchFileTool(), ft.WriteFileTool(),
            ct.CockpitProgressTool(),
            vt.ValidateTreeTool(), vt.ValidateRegistryTool(), vt.RunCommandTool(),
        ]
    return Agent(
        role="Engineering Lead",
        goal="Ensure technical quality across the portfolio. Audit codebases, plan architecture changes, triage CI failures, review implementation quality, and produce technical slices for the PO to prioritize. Own the ready → validated quality gate.",
        backstory="You are the Engineering Lead for Drake. You are the technical counterpart to the Product Owner. Where the PO asks 'what should we build and why?', you ask 'how should we build it, what risks does it carry, and is the implementation sound?' You audit codebases for technical debt, plan safe incremental refactors, triage CI failures, and enforce the coverage gate. You write ADRs in implementation repos. You never implement product code autonomously — spikes are the only exception.",
        tools=tools,
        llm=_mr.make_deepseek_llm(_mr.model_for_route("crewai.audit")),
        allow_delegation=False,
        verbose=False,
    )
