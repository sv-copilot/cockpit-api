"""Pin consolidation test (COCKPIT-API-CREWAI-MODEL-ROUTING-1).

Asserts the cockpit-api image installs crewai from a single consolidated pin of
`crewai>=1.0.0,<2.0.0` and that the redundant orchestrator requirements file no
longer carries a conflicting pin.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ORCH_ROOT = REPO_ROOT / "crewai_orchestrator"


class TestCrewaiPin:
    """The root requirements file must carry the single consolidated crewai pin."""

    def test_root_requirements_has_consolidated_pin(self) -> None:
        lines = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        crewai_lines = [line.strip() for line in lines if line.strip().lower().startswith("crewai")]
        assert crewai_lines == ["crewai>=1.0.0,<2.0.0"], (
            f"expected a single consolidated crewai pin, got {crewai_lines!r}"
        )

    def test_orchestrator_requirements_carries_no_conflicting_pin(self) -> None:
        orch_req = ORCH_ROOT / "requirements.txt"
        if orch_req.exists():
            text = orch_req.read_text(encoding="utf-8").lower()
            assert "crewai" not in text, (
                "crewai_orchestrator/requirements.txt must not carry a conflicting crewai pin"
            )

    def test_pin_satisfies_installed_crewai_when_available(self) -> None:
        """If crewai is installed, its version must satisfy >=1.0.0,<2.0.0."""
        try:
            import importlib.metadata as metadata

            version = metadata.version("crewai")
        except Exception:
            import pytest

            pytest.skip("crewai is not installed in this environment")
        major = int(version.split(".")[0])
        assert 1 <= major < 2, f"installed crewai {version} must satisfy >=1.0.0,<2.0.0"
