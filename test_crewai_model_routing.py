"""Tests for the cockpit-api model-routing resolver (COCKPIT-API-CREWAI-MODEL-ROUTING-1).

Covers canonical-config loading from PLANNING_CHECKOUT_PATH, vendored fallback,
crewai.* route resolution, and a sync assertion that the vendored copy matches
the canonical config when the canonical checkout is present.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent
ORCH_ROOT = REPO_ROOT / "crewai_orchestrator"
VENDORED_PATH = ORCH_ROOT / "model_routing.json"


def _load_resolver():
    name = "cockpit_model_routing"
    spec = importlib.util.spec_from_file_location(name, ORCH_ROOT / "model_routing.py")
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load model_routing resolver")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mr = _load_resolver()


def _canonical_path() -> Path | None:
    planning = os.getenv("PLANNING_CHECKOUT_PATH")
    candidates = []
    if planning:
        candidates.append(Path(planning) / ".docs" / "model_routing.json")
    candidates.append(REPO_ROOT.parent / ".docs" / "model_routing.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class TestConfigResolution:
    """The resolver reads the canonical config, falling back to the vendored copy."""

    def test_prefers_planning_checkout_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        planning = tmp_path / "planning"
        (planning / ".docs").mkdir(parents=True)
        cfg = {
            "version": 1,
            "peak_hours_utc": [[1, 4]],
            "tiers": {
                "reasoning": {"provider": "deepseek", "model": "deepseek-v4-pro", "temperature": 0.2, "max_tokens": 16000, "peak_policy": "off_peak_only"},
            },
            "routes": {"crewai.refinement": "reasoning"},
        }
        (planning / ".docs" / "model_routing.json").write_text(json.dumps(cfg), encoding="utf-8")
        monkeypatch.setenv("PLANNING_CHECKOUT_PATH", str(planning))
        assert mr.config_path() == planning / ".docs" / "model_routing.json"
        assert mr.load_config() == cfg

    def test_falls_back_to_vendored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLANNING_CHECKOUT_PATH", "/nonexistent/checkout")
        assert mr.config_path() == VENDORED_PATH
        config = mr.load_config()
        assert config["version"] == 1
        assert "crewai.refinement" in config["routes"]


class TestCrewaiRoutes:
    """The four crewai.* routes resolve to their expected tiers/models."""

    def test_crewai_route_models(self) -> None:
        config = mr.load_config()
        assert mr.model_for_route("crewai.refinement", config) == "deepseek-v4-pro"
        assert mr.model_for_route("crewai.audit", config) == "deepseek-v4-pro"
        assert mr.model_for_route("crewai.sync", config) == "deepseek-v4-flash"
        assert mr.model_for_route("crewai.handmaiden", config) == "deepseek-v4-flash"

    def test_crewai_route_tiers_and_peak_policy(self) -> None:
        config = mr.load_config()
        assert mr.route_tier("crewai.refinement", config) == "reasoning"
        assert mr.route_tier("crewai.audit", config) == "reasoning"
        assert mr.route_tier("crewai.sync", config) == "mechanical"
        assert mr.route_tier("crewai.handmaiden", config) == "mechanical"
        assert mr.resolve_route("crewai.refinement", config)["peak_policy"] == "off_peak_only"
        assert mr.resolve_route("crewai.sync", config)["peak_policy"] == "any"

    def test_every_route_resolves_to_declared_tier(self) -> None:
        config = mr.load_config()
        for route, tier_name in config["routes"].items():
            assert tier_name in config["tiers"], f"route {route!r} -> unknown tier {tier_name!r}"


class TestVendoredSync:
    """The vendored fallback must stay in sync with the canonical config."""

    def test_vendored_copy_has_crewai_routes(self) -> None:
        vendored = json.loads(VENDORED_PATH.read_text(encoding="utf-8"))
        assert "crewai.refinement" in vendored["routes"]
        assert "crewai.audit" in vendored["routes"]
        assert "crewai.sync" in vendored["routes"]
        assert "crewai.handmaiden" in vendored["routes"]

    def test_vendored_matches_canonical(self) -> None:
        canonical = _canonical_path()
        if canonical is None:
            pytest.skip("canonical .docs/model_routing.json not available in this checkout")
        vendored = json.loads(VENDORED_PATH.read_text(encoding="utf-8"))
        canonical_cfg = json.loads(canonical.read_text(encoding="utf-8"))
        assert vendored["tiers"] == canonical_cfg["tiers"]
        assert vendored["routes"] == canonical_cfg["routes"]
        assert vendored["version"] == canonical_cfg["version"]
        assert vendored["peak_hours_utc"] == canonical_cfg["peak_hours_utc"]


class TestMakeDeepseekLLM:
    """make_deepseek_llm must build a DeepSeek LLM from the routed tier."""

    def test_builds_pro_llm_from_reasoning_tier(self) -> None:
        config = mr.load_config()
        with patch.object(mr, "LLM") as mock_llm:
            mr.make_deepseek_llm("deepseek-v4-pro", config=config)
        mock_llm.assert_called_once()
        kwargs = mock_llm.call_args.kwargs
        assert kwargs["model"] == "deepseek-v4-pro"
        assert kwargs["base_url"] == os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 16000

    def test_builds_flash_llm_from_mechanical_tier(self) -> None:
        config = mr.load_config()
        with patch.object(mr, "LLM") as mock_llm:
            mr.make_deepseek_llm("deepseek-v4-flash", config=config)
        kwargs = mock_llm.call_args.kwargs
        assert kwargs["model"] == "deepseek-v4-flash"
        assert kwargs["temperature"] == 0.1
        assert kwargs["max_tokens"] == 8000
