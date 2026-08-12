"""Git operation tools for CrewAI agents."""

from __future__ import annotations

import subprocess
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class _GitPathArgs(BaseModel):
    repo_path: str = Field(default=".", description="Path to the git repository")


class _GitCommitArgs(BaseModel):
    repo_path: str = Field(default=".", description="Path to the git repository")
    message: str = Field(..., description="Commit message")


class GitStatusTool(BaseTool):
    """Run 'git status --porcelain' and return the output."""
    name: str = "git_status"
    description: str = "Run git status --porcelain to see working tree changes."
    args_schema: Type[BaseModel] = _GitPathArgs

    def _run(self, repo_path: str = ".") -> str:
        result = subprocess.run(
            ["git", "-C", repo_path, "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or "(clean working tree)"


class GitDiffTool(BaseTool):
    """Run 'git diff' and return the output."""
    name: str = "git_diff"
    description: str = "Run git diff to see unstaged changes."
    args_schema: Type[BaseModel] = _GitPathArgs

    def _run(self, repo_path: str = ".") -> str:
        result = subprocess.run(
            ["git", "-C", repo_path, "diff"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or "(no unstaged changes)"


class GitCommitTool(BaseTool):
    """Stage all changes and commit with a message."""
    name: str = "git_commit"
    description: str = "Stage all changes and create a git commit with the given message."
    args_schema: Type[BaseModel] = _GitCommitArgs

    def _run(self, repo_path: str = ".", message: str = "") -> str:
        subprocess.run(
            ["git", "-C", repo_path, "add", "-A"],
            capture_output=True, text=True, timeout=30,
        )
        result = subprocess.run(
            ["git", "-C", repo_path, "commit", "-m", message],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or result.stderr


class GitPushTool(BaseTool):
    """Push the current branch to origin."""
    name: str = "git_push"
    description: str = "Push current branch to origin."
    args_schema: Type[BaseModel] = _GitPathArgs

    def _run(self, repo_path: str = ".") -> str:
        result = subprocess.run(
            ["git", "-C", repo_path, "push", "origin", "HEAD"],
            capture_output=True, text=True, timeout=60,
        )
        return result.stdout or result.stderr
