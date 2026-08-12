"""Validation tools for CrewAI agents."""

from __future__ import annotations

import subprocess
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class _ValidateTreeArgs(BaseModel):
    tree_path: str = Field(..., description="Path to the dependency tree JSON")


class _RunCommandArgs(BaseModel):
    command: str = Field(..., description="Shell command to run")
    cwd: str = Field(default=".", description="Working directory")


class _EmptyArgs(BaseModel):
    pass


class ValidateTreeTool(BaseTool):
    """Validate a planning tree."""
    name: str = "validate_tree"
    description: str = "Run validate_slice_dependency_tree.py on a planning tree."
    args_schema: Type[BaseModel] = _ValidateTreeArgs

    def _run(self, tree_path: str) -> str:
        result = subprocess.run(
            ["python3", "scripts/validate_slice_dependency_tree.py", "--tree", tree_path],
            capture_output=True, text=True, timeout=60,
        )
        return f"exit={result.returncode}\n{result.stdout}\n{result.stderr}"


class ValidateRegistryTool(BaseTool):
    """Validate the projects registry JSON."""
    name: str = "validate_registry"
    description: str = "Validate .docs/projects-registry.json is valid JSON."
    args_schema: Type[BaseModel] = _EmptyArgs

    def _run(self) -> str:
        result = subprocess.run(
            ["python3", "-m", "json.tool", ".docs/projects-registry.json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return "Registry: valid JSON"
        return f"Registry: INVALID — {result.stderr}"


class RunCommandTool(BaseTool):
    """Run an arbitrary shell command. Restricted to validation, tests, lint, typecheck."""
    name: str = "run_command"
    description: str = "Run a shell command. Use only for validation, tests, lint, and typecheck."
    args_schema: Type[BaseModel] = _RunCommandArgs

    def _run(self, command: str, cwd: str = ".") -> str:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=120, cwd=cwd,
        )
        return f"exit={result.returncode}\n{result.stdout}\n{result.stderr}"
