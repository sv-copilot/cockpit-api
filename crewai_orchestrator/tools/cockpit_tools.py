"""Cockpit API tools for CrewAI agents."""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


COCKPIT_URL = "http://localhost:8080"


def _cockpit_get(path: str) -> str:
    try:
        with urllib.request.urlopen(f"{COCKPIT_URL}{path}", timeout=10) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        return json.dumps({"error": f"cockpit API unreachable: {exc.reason}"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _cockpit_post(path: str, body: dict) -> str:
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{COCKPIT_URL}{path}", data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        return json.dumps({"error": f"cockpit API unreachable: {exc.reason}"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


class _EmptyArgs(BaseModel):
    pass


class _DispatchArgs(BaseModel):
    repo_id: str = Field(..., description="Registered repo ID")
    slice_id: str = Field(..., description="Slice ID to dry-run")


class CockpitProgressTool(BaseTool):
    """Fetch the portfolio progress summary from the cockpit API."""
    name: str = "cockpit_progress"
    description: str = "Fetch cockpit progress summary: promotion gaps, ready slices, project rollups."
    args_schema: Type[BaseModel] = _EmptyArgs

    def _run(self) -> str:
        return _cockpit_get("/cockpit/progress")


class CockpitDispatchDryRunTool(BaseTool):
    """Run a dispatch dry-run for a slice via the cockpit API."""
    name: str = "cockpit_dispatch_dry_run"
    description: str = "Dry-run a slice dispatch — preview gates, routing, and task packet."
    args_schema: Type[BaseModel] = _DispatchArgs

    def _run(self, repo_id: str, slice_id: str) -> str:
        return _cockpit_post("/dispatch/dry-run", {"repo_id": repo_id, "slice_id": slice_id})
