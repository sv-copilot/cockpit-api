"""
SystemStatsTool – wraps free, df, uptime.
Returns structured JSON.
"""
import json
import subprocess
from crewai_tools import BaseTool


class SystemStatsTool(BaseTool):
    name: str = "SystemStatsTool"
    description: str = (
        "Collect system resource statistics: memory (free), disk (df), and uptime. "
        "Returns JSON with memory, disk, and load information."
    )

    def _run(self) -> str:
        """
        Run all system commands and return JSON.
        """
        memory = self._get_memory()
        disk = self._get_disk()
        uptime = self._get_uptime()
        return json.dumps({
            "memory": memory,
            "disk": disk,
            "uptime": uptime,
        })

    def _get_memory(self) -> dict:
        try:
            result = subprocess.run(
                ["free", "-m"],
                capture_output=True, text=True, check=True
            )
            lines = result.stdout.strip().splitlines()
            if len(lines) < 2:
                return {"error": "unexpected free output"}
            header = lines[0].split()
            mem_line = lines[1].split()
            data = dict(zip(header, mem_line))
            return data
        except subprocess.CalledProcessError as e:
            return {"error": f"free failed: {e.stderr}"}

    def _get_disk(self) -> list:
        try:
            result = subprocess.run(
                ["df", "-h", "--output=target,size,used,avail,pcent"],
                capture_output=True, text=True, check=True
            )
            lines = result.stdout.strip().splitlines()
            if not lines:
                return []
            data = []
            for line in lines[1:]:  # skip header
                parts = line.split()
                if len(parts) >= 5:
                    data.append({
                        "mount": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "avail": parts[3],
                        "use%": parts[4],
                    })
            return data
        except subprocess.CalledProcessError as e:
            return [{"error": f"df failed: {e.stderr}"}]

    def _get_uptime(self) -> dict:
        try:
            result = subprocess.run(
                ["uptime", "-p"],
                capture_output=True, text=True, check=True
            )
            uptime_str = result.stdout.strip()
            # Also get load average from uptime
            result2 = subprocess.run(
                ["uptime"],
                capture_output=True, text=True, check=True
            )
            # Parse load from end: "load average: 0.01, 0.05, 0.10"
            last_part = result2.stdout.strip().split("load average:")[-1].strip()
            loads = last_part.split(", ") if last_part else []
            return {
                "uptime_pretty": uptime_str,
                "load_averages": loads,
            }
        except subprocess.CalledProcessError as e:
            return {"error": f"uptime failed: {e.stderr}"}
