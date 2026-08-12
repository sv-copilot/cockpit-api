"""GitHub CLI tools for CrewAI agents."""

from __future__ import annotations

import subprocess
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class _GHPRViewArgs(BaseModel):
    pr_number: int = Field(..., description="Pull request number")
    repo: str = Field(..., description="GitHub repo as owner/name")


class GHPRViewTool(BaseTool):
    """View a GitHub pull request via 'gh pr view'."""
    name: str = "gh_pr_view"
    description: str = "View a GitHub PR by number. Returns JSON with state, mergedAt, mergeCommit."
    args_schema: Type[BaseModel] = _GHPRViewArgs

    def _run(self, pr_number: int, repo: str) -> str:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "state,mergedAt,mergeCommit,title,baseRefName"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or result.stderr


class _GHPRListArgs(BaseModel):
    repo: str = Field(..., description="GitHub repo as owner/name")
    base: str = Field(default="ai-dev", description="Target base branch")


class GHPRListTool(BaseTool):
    """List open PRs via 'gh pr list'."""
    name: str = "gh_pr_list"
    description: str = "List open PRs for a repo targeting a base branch."
    args_schema: Type[BaseModel] = _GHPRListArgs

    def _run(self, repo: str, base: str = "ai-dev") -> str:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--base", base,
             "--state", "open", "--json", "number,title,mergeStateStatus,mergeable,url"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or "[]"


class _GHPRCreateArgs(BaseModel):
    repo: str = Field(..., description="GitHub repo as owner/name")
    base: str = Field(default="ai-dev", description="Target base branch")
    title: str = Field(..., description="PR title")
    body: str = Field(default="", description="PR description")


class GHPRCreateTool(BaseTool):
    """Create a GitHub pull request via 'gh pr create'."""
    name: str = "gh_pr_create"
    description: str = "Create a GitHub PR from the current branch."
    args_schema: Type[BaseModel] = _GHPRCreateArgs

    def _run(self, repo: str, base: str = "ai-dev", title: str = "", body: str = "") -> str:
        cmd = ["gh", "pr", "create", "--repo", repo, "--base", base, "--title", title]
        if body:
            cmd.extend(["--body", body])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout or result.stderr


class _GHPRMergeArgs(BaseModel):
    pr_number: int = Field(..., description="Pull request number")
    repo: str = Field(..., description="GitHub repo as owner/name")


class GHPRMergeTool(BaseTool):
    """Merge a GitHub PR via 'gh pr merge'."""
    name: str = "gh_pr_merge"
    description: str = "Squash-merge a GitHub PR and delete the remote branch."
    args_schema: Type[BaseModel] = _GHPRMergeArgs

    def _run(self, pr_number: int, repo: str) -> str:
        result = subprocess.run(
            ["gh", "pr", "merge", str(pr_number), "--repo", repo,
             "--squash", "--delete-branch"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or result.stderr
