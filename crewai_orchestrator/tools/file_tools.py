"""File operation tools for CrewAI agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class _ReadFileArgs(BaseModel):
    file_path: str = Field(..., description="Absolute or relative path to the file")


class ReadFileTool(BaseTool):
    """Read a file and return its contents."""
    name: str = "read_file"
    description: str = "Read the contents of a file at the given path."
    args_schema: Type[BaseModel] = _ReadFileArgs

    def _run(self, file_path: str) -> str:
        p = Path(file_path)
        if not p.exists():
            return f"ERROR: file not found: {file_path}"
        try:
            return p.read_text(encoding="utf-8")
        except Exception as exc:
            return f"ERROR: {exc}"


class _WriteFileArgs(BaseModel):
    file_path: str = Field(..., description="Path to write to")
    content: str = Field(..., description="Content to write")


class WriteFileTool(BaseTool):
    """Write content to a file. Restricted to current workspace."""
    name: str = "write_file"
    description: str = "Write content to a file. Restricted to planning docs in drake-governance."
    args_schema: Type[BaseModel] = _WriteFileArgs

    def _run(self, file_path: str, content: str) -> str:
        p = Path(file_path).resolve()
        cwd = Path.cwd().resolve()
        try:
            p.relative_to(cwd)
        except ValueError:
            return f"ERROR: write_file refused — path outside workspace"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"OK: wrote {len(content)} bytes to {file_path}"


class _SearchFileArgs(BaseModel):
    pattern: str = Field(..., description="Glob pattern")


class SearchFileTool(BaseTool):
    """Search for files by glob pattern."""
    name: str = "search_file"
    description: str = "Search for files matching a glob pattern."
    args_schema: Type[BaseModel] = _SearchFileArgs

    def _run(self, pattern: str) -> str:
        matches = sorted(str(p) for p in Path().glob(pattern))
        if not matches:
            return "(no matches)"
        return json.dumps(matches, indent=2)
