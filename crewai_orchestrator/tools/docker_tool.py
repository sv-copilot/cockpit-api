"""
DockerTool – wraps docker ps, docker stats, docker logs.
Returns structured JSON.
"""
import json
import subprocess
from crewai_tools import BaseTool


class DockerTool(BaseTool):
    name: str = "DockerTool"
    description: str = (
        "Execute Docker commands: ps (list containers), stats (resource usage), "
        "logs (fetch recent logs for a container). Returns JSON with structured output."
    )

    def _run(self, command: str = "ps", container: str = None, tail: int = 100) -> str:
        """
        Run a Docker command.

        Args:
            command: one of "ps", "stats", "logs"
            container: container name or ID (required for logs, optional for stats)
            tail: number of log lines to fetch (default 100)

        Returns:
            JSON string with structured data
        """
        if command == "ps":
            return self._docker_ps()
        elif command == "stats":
            return self._docker_stats(container)
        elif command == "logs":
            if not container:
                return json.dumps({"error": "container parameter required for logs"})
            return self._docker_logs(container, tail)
        else:
            return json.dumps({"error": f"Unknown command: {command}"})

    def _docker_ps(self) -> str:
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{json .}}"],
                capture_output=True, text=True, check=True
            )
            containers = []
            for line in result.stdout.strip().splitlines():
                if line:
                    containers.append(json.loads(line))
            return json.dumps({"containers": containers}, default=str)
        except subprocess.CalledProcessError as e:
            return json.dumps({"error": f"docker ps failed: {e.stderr}"})

    def _docker_stats(self, container: str = None) -> str:
        cmd = ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
        if container:
            cmd.append(container)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            stats = []
            for line in result.stdout.strip().splitlines():
                if line:
                    stats.append(json.loads(line))
            return json.dumps({"stats": stats}, default=str)
        except subprocess.CalledProcessError as e:
            return json.dumps({"error": f"docker stats failed: {e.stderr}"})

    def _docker_logs(self, container: str, tail: int) -> str:
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container],
                capture_output=True, text=True, check=True
            )
            return json.dumps({"logs": result.stdout.splitlines(), "container": container})
        except subprocess.CalledProcessError as e:
            return json.dumps({"error": f"docker logs failed: {e.stderr}"})
