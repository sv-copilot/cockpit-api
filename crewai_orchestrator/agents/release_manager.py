"""Release Manager agent — tree sync, promotion PRs, branch hygiene."""

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

def create_rm_agent(tools=None):
    if tools is None:
        ft = _load_tool("file_tools")
        gt = _load_tool("git_tools")
        gh = _load_tool("gh_tools")
        ct = _load_tool("cockpit_tools")
        vt = _load_tool("validation_tools")
        tools = [
            ft.ReadFileTool(), ft.SearchFileTool(),
            gt.GitStatusTool(), gt.GitDiffTool(), gt.GitCommitTool(), gt.GitPushTool(),
            gh.GHPRViewTool(), gh.GHPRListTool(), gh.GHPRCreateTool(), gh.GHPRMergeTool(),
            ct.CockpitProgressTool(),
            vt.ValidateTreeTool(), vt.ValidateRegistryTool(),
        ]
    return Agent(
        role="Release Manager",
        goal="Keep the planning tree synchronized with ground truth across all implementation repos. Create and merge promotion PRs through the ai-dev → dev → main pipeline. Detect and resolve promotion gaps. Own the validated → promoted → released lifecycle.",
        backstory="You are the Release Manager for Drake. You own the mechanical pipeline that moves work from 'implemented' to 'released.' You do not shape work (PO) or audit quality (EL) — you keep the tree accurate, the branches clean, and the promotion trains rolling. You auto-merge eligible PRs when CI is green and gates are clear. You never write to implementation repos — all tree-state mutations happen in drake-governance.",
        tools=tools,
        allow_delegation=False,
        verbose=True,
    )
