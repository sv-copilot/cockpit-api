"""Config-driven model routing for CrewAI agents in the cockpit API.

Reads the canonical `.docs/model_routing.json` from `PLANNING_CHECKOUT_PATH`
(the git-synced drake-governance checkout already used by `crewai_routes.py`)
with a vendored fallback copy bundled next to this module. Reuses the same
tier/routes model introduced by DRAKE-MODEL-ROUTING-1 (#217).

Every agent factory binds `llm=` through `make_deepseek_llm`, so CrewAI can no
longer silently fall back to its OpenAI default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from crewai import LLM

_MODULE_DIR = Path(__file__).resolve().parent
VENDORED_CONFIG_PATH = _MODULE_DIR / "model_routing.json"

DEFAULT_BASE_URL = "https://api.deepseek.com"

# Tier defaults used when a model is not declared in the config.
_DEFAULT_TEMPERATURE = 0.2
_DEFAULT_MAX_TOKENS = 16000


def canonical_config_path() -> Path | None:
    """Return the canonical config path when PLANNING_CHECKOUT_PATH has one."""
    planning = os.getenv("PLANNING_CHECKOUT_PATH")
    if planning:
        candidate = Path(planning) / ".docs" / "model_routing.json"
        if candidate.exists():
            return candidate
    return None


def config_path() -> Path:
    """Return the config to use: the canonical checkout copy, else the vendored fallback."""
    return canonical_config_path() or VENDORED_CONFIG_PATH


def load_config() -> dict[str, Any]:
    return json.loads(config_path().read_text(encoding="utf-8"))


def tier(tier_name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if config is not None else load_config()
    try:
        return cfg["tiers"][tier_name]
    except KeyError:
        raise KeyError(f"tier {tier_name!r} not declared in model_routing config") from None


def route_tier(route_key: str, config: dict[str, Any] | None = None) -> str:
    cfg = config if config is not None else load_config()
    return cfg["routes"][route_key]


def resolve_route(route_key: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if config is not None else load_config()
    return cfg["tiers"][cfg["routes"][route_key]]


def model_for_route(route_key: str, config: dict[str, Any] | None = None) -> str:
    return resolve_route(route_key, config)["model"]


def _tier_for_model(model: str, config: dict[str, Any]) -> dict[str, Any]:
    for tier_def in config.get("tiers", {}).values():
        if tier_def.get("model") == model:
            return tier_def
    return {}


def make_deepseek_llm(model: str, config: dict[str, Any] | None = None) -> Any:
    """Build a DeepSeek CrewAI LLM for `model` using its tier's temperature/max_tokens.

    `DEEPSEEK_API_KEY` is read at call time. A missing key degrades gracefully:
    CrewAI raises at kickoff, which the dispatch path already captures as a
    failed run — preserving the existing fail-closed behavior.
    """
    cfg = config if config is not None else load_config()
    tier_def = _tier_for_model(model, cfg)
    return LLM(
        model=model,
        base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        temperature=tier_def.get("temperature", _DEFAULT_TEMPERATURE),
        max_tokens=tier_def.get("max_tokens", _DEFAULT_MAX_TOKENS),
    )
