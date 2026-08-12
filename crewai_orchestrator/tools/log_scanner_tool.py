"""
LogScannerTool – scans recent container logs for ERROR/CRITICAL/Traceback patterns.
Returns structured JSON.
"""
import json
import subprocess
import re
from crewai_tools import BaseTool


class LogScannerTool(BaseTool):
    name: str = "LogScannerTool"
    description: str = (
        "Scan recent container logs for ERROR, CRITICAL, or Traceback patterns. "
        "Takes optional container filter. Returns JSON with matched lines per container."
    )

    PATTERNS = re.compile(r'\b(ERROR|CRITICAL|Traceback)\b', re.IGNORECASE)

    def _run(self, container: str = None, tail: int = 200) -> str:
        """
        Fetch logs for running containers and scan for patterns.

        Args:
            container: specific container name (optional)
            tail: number of log lines per container (default 200)

        Returns:
            JSON string with container -> list of matching lines
        """
        containers = self._get_containers(container)
        results = {}
        for cname in containers:
            try:
                result = subprocess.run(
                    ["docker", "logs", "--tail", str(tail), cname],
                    capture_output=True, text=True, check=True
                )
                lines = result.stdout.splitlines()
                matches = [line for line in lines if self.PATTERNS.search(line)]
                if matches:
                    results[cname] = matches
            except subprocess.CalledProcessError as e:
                results[cname] = {"error": f"docker logs failed: {e.stderr}"}
        return json.dumps({"errors_found": results})

    def _get_containers(self, container: str = None) -> list:
        if container:
            return [container]
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip().splitlines()
        except subprocess.CalledProcessError:
            return []
