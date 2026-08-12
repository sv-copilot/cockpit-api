"""
CockpitHealthTool – calls GET /health on configured services.
Returns structured JSON.
"""
import json
import os
import requests
from crewai_tools import BaseTool


class CockpitHealthTool(BaseTool):
    name: str = "CockpitHealthTool"
    description: str = (
        "Check health endpoints of cockpit services (cockpit-api, drake-api, research-backend). "
        "Returns JSON with status code and response for each endpoint."
    )

    def _run(self) -> str:
        """
        Poll all configured health endpoints.
        """
        endpoints = os.getenv(
            "COCKPIT_HEALTH_ENDPOINTS",
            "http://localhost:8000/health,http://localhost:8001/health,http://localhost:8002/health"
        ).split(",")

        results = {}
        for ep in endpoints:
            ep = ep.strip()
            if not ep:
                continue
            try:
                resp = requests.get(ep, timeout=5)
                results[ep] = {
                    "status_code": resp.status_code,
                    "response": resp.text[:500],
                    "ok": resp.ok,
                }
            except requests.RequestException as e:
                results[ep] = {
                    "error": str(e),
                    "ok": False,
                }
        return json.dumps({"health_checks": results})
