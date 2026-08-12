"""Shared CrewAI tools for the Drake orchestrator.

Each tool wraps an external command or API call with typed arguments
and docstrings that CrewAI uses for function-calling resolution.
"""

# Relative imports — package directory uses hyphen, not importable as dotted name
import importlib.util
import sys
from pathlib import Path

_pkg_dir = Path(__file__).resolve().parent.parent  # tools/crewai-orchestrator/
_tools_dir = Path(__file__).resolve().parent


def _load_module(name: str, path: Path):
    """Load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load tool modules
_git = _load_module("git_tools", _tools_dir / "git_tools.py")
_gh = _load_module("gh_tools", _tools_dir / "gh_tools.py")
_file = _load_module("file_tools", _tools_dir / "file_tools.py")
_cockpit = _load_module("cockpit_tools", _tools_dir / "cockpit_tools.py")
_validation = _load_module("validation_tools", _tools_dir / "validation_tools.py")

GitStatusTool = _git.GitStatusTool
GitDiffTool = _git.GitDiffTool
GitCommitTool = _git.GitCommitTool
GitPushTool = _git.GitPushTool
GHPRViewTool = _gh.GHPRViewTool
GHPRListTool = _gh.GHPRListTool
GHPRCreateTool = _gh.GHPRCreateTool
GHPRMergeTool = _gh.GHPRMergeTool
ReadFileTool = _file.ReadFileTool
WriteFileTool = _file.WriteFileTool
SearchFileTool = _file.SearchFileTool
CockpitProgressTool = _cockpit.CockpitProgressTool
CockpitDispatchDryRunTool = _cockpit.CockpitDispatchDryRunTool
ValidateTreeTool = _validation.ValidateTreeTool
ValidateRegistryTool = _validation.ValidateRegistryTool
RunCommandTool = _validation.RunCommandTool

__all__ = [
    "GitStatusTool", "GitDiffTool", "GitCommitTool", "GitPushTool",
    "GHPRViewTool", "GHPRListTool", "GHPRCreateTool", "GHPRMergeTool",
    "ReadFileTool", "WriteFileTool", "SearchFileTool",
    "CockpitProgressTool", "CockpitDispatchDryRunTool",
    "ValidateTreeTool", "ValidateRegistryTool", "RunCommandTool",
]
