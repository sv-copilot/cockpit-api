"""
HandmaidenCrew – single-agent crew for infrastructure health checks.
Runs a 60-second poll loop.
"""
import os
import json
import logging
import signal
import time
import sys
from datetime import datetime, timezone
from typing import Dict, Any

from crewai import Crew, Process, Task
from tools.crewai_orchestrator.agents.infrastructure_steward import create_steward_agent

logger = logging.getLogger(__name__)

# Defaults
POLL_INTERVAL_S = int(os.getenv("HANDMAIDEN_POLL_INTERVAL_S", "60"))
HEARTBEAT_PATH = os.getenv("HANDMAIDEN_HEARTBEAT_PATH", "/data/cockpit/handmaiden_heartbeat.json")
_shutdown_requested = False


class HealthSweep:
    """
    Represents the result of a full health sweep.
    """
    def __init__(self, timestamp: str, sweep_id: str,
                 containers: Dict, resources: Dict, api_health: Dict,
                 logs_errors: Dict, runner_status: Dict, restarts: Dict,
                 disk_usage: Dict):
        self.timestamp = timestamp
        self.sweep_id = sweep_id
        self.containers = containers
        self.resources = resources
        self.api_health = api_health
        self.logs_errors = logs_errors
        self.runner_status = runner_status
        self.restarts = restarts
        self.disk_usage = disk_usage

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "sweep_id": self.sweep_id,
            "containers": self.containers,
            "resources": self.resources,
            "api_health": self.api_health,
            "logs_errors": self.logs_errors,
            "runner_status": self.runner_status,
            "restarts": self.restarts,
            "disk_usage": self.disk_usage,
        }


class HandmaidenCrew:
    """
    Crew that runs a single health sweep task using the Infrastructure Steward agent.
    """
    def __init__(self):
        self.agent = create_steward_agent()

    def health_sweep_task(self) -> Task:
        """
        Creates the health sweep task that the agent will execute.
        The task description asks the agent to run all tools and compile a HealthSweep.
        """
        return Task(
            description=(
                "Perform a full health sweep of the infrastructure:\n"
                "1. Run DockerTool to list containers and their stats.\n"
                "2. Run SystemStatsTool to get memory, disk, and load.\n"
                "3. Run CockpitHealthTool to check API endpoints.\n"
                "4. Run LogScannerTool to find recent ERROR/CRITICAL/Traceback.\n"
                "5. Check runner status (via CockpitHealthTool or systemctl).\n"
                "6. Check for container restarts (docker ps --format shows status).\n"
                "7. Check disk usage (included in SystemStatsTool).\n\n"
                "Compile all results into a structured HealthSweep report. "
                "Return the JSON representation of the HealthSweep."
            ),
            expected_output="JSON string with keys: timestamp, sweep_id, containers, resources, api_health, logs_errors, runner_status, restarts, disk_usage",
            agent=self.agent,
        )

    def run_single_sweep(self) -> HealthSweep:
        """
        Execute one health sweep and return the result.
        """
        crew = Crew(
            agents=[self.agent],
            tasks=[self.health_sweep_task()],
            process=Process.sequential,
            verbose=True,
        )
        result = crew.kickoff()

        # Parse the result into a HealthSweep
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            # If parsing fails, build a fallback
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sweep_id": f"sweep-{int(time.time())}",
                "containers": {},
                "resources": {},
                "api_health": {},
                "logs_errors": {},
                "runner_status": {},
                "restarts": {},
                "disk_usage": {},
                "error": f"could not parse agent result: {result[:200]}"
            }
        sweep = HealthSweep(
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            sweep_id=data.get("sweep_id", f"sweep-{int(time.time())}"),
            containers=data.get("containers", {}),
            resources=data.get("resources", {}),
            api_health=data.get("api_health", {}),
            logs_errors=data.get("logs_errors", {}),
            runner_status=data.get("runner_status", {}),
            restarts=data.get("restarts", {}),
            disk_usage=data.get("disk_usage", {}),
        )
        return sweep

    def write_heartbeat(self, sweep: HealthSweep):
        """
        Write heartbeat file with the latest sweep timestamp and status.
        """
        heartbeat = {
            "last_sweep_timestamp": sweep.timestamp,
            "last_sweep_id": sweep.sweep_id,
            "status": "ok",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        os.makedirs(os.path.dirname(HEARTBEAT_PATH), exist_ok=True)
        with open(HEARTBEAT_PATH, "w") as f:
            json.dump(heartbeat, f, indent=2)

    def run_loop(self):
        """
        Run the health sweep loop indefinitely, respecting SIGTERM.
        """
        global _shutdown_requested
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info("Handmaiden poll loop started with interval %d seconds", POLL_INTERVAL_S)
        while not _shutdown_requested:
            try:
                sweep = self.run_single_sweep()
                self.write_heartbeat(sweep)
                logger.info("Health sweep completed: %s", sweep.sweep_id)
                # Check for critical issues (optional logging)
                if any(sweep.api_health.get(ep, {}).get("ok") == False for ep in sweep.api_health):
                    logger.warning("API degradation detected in sweep %s", sweep.sweep_id)
                if sweep.logs_errors:
                    logger.warning("Log errors found in sweep %s", sweep.sweep_id)
            except Exception as e:
                logger.error("Health sweep failed: %s", str(e))
                # Still write heartbeat with error status
                try:
                    heartbeat = {
                        "last_sweep_timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "error",
                        "error": str(e)[:500],
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    os.makedirs(os.path.dirname(HEARTBEAT_PATH), exist_ok=True)
                    with open(HEARTBEAT_PATH, "w") as f:
                        json.dump(heartbeat, f, indent=2)
                except Exception:
                    pass

            # Wait for interval or until shutdown
            for _ in range(POLL_INTERVAL_S):
                if _shutdown_requested:
                    break
                time.sleep(1)

        logger.info("Handmaiden poll loop exiting gracefully.")

    def _handle_signal(self, signum, frame):
        global _shutdown_requested
        _shutdown_requested = True
        logger.info("Shutdown signal received, finishing current sweep...")
