"""Tests for openapi.json — validates the typed API contract for the cockpit API.

Red→Green: these tests fail until `openapi.json` exists and documents every
endpoint of the cockpit API. Run with `python -m pytest test_openapi.py`.
"""

import json
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent / "openapi.json"

# Every path the cockpit API serves (main.py + credentials/webhook/crewai routers).
EXPECTED_PATHS = {
    "/health": {"get"},
    "/cockpit/progress": {"get"},
    "/cockpit/projects/slices": {"get"},
    "/cockpit/projects/{repo_id}/slices": {"get"},
    "/cockpit/slices/{slice_id}": {"get"},
    "/api/v1/sources": {"get", "post"},
    "/api/v1/sources/{source_id}": {"delete", "patch"},
    "/dispatch/dry-run": {"post"},
    "/dispatch/confirm": {"post"},
    "/runs": {"get", "post"},
    "/runs/{run_id}": {"get"},
    "/admin/sync": {"get"},
    "/credentials/inventory": {"get"},
    "/credentials/inventory/repos/{repo_id}": {"get"},
    "/credentials/{name}": {"post", "delete"},
    "/credentials/{name}/test": {"post"},
    "/webhook/github": {"post"},
    "/webhook/trigger": {"post"},
    "/crewai/dispatch": {"post"},
    "/crewai/runs": {"get"},
    "/crewai/runs/{run_id}": {"get"},
}


def _load_spec() -> dict:
    if not SPEC_PATH.exists():
        raise AssertionError("openapi.json does not exist")
    with SPEC_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_openapi_version_and_metadata():
    spec = _load_spec()
    assert spec["openapi"] == "3.1.0", "must be OpenAPI 3.1"
    assert spec["info"]["version"] == "1.0.0", "spec version must be 1.0.0"
    assert spec["info"]["title"], "missing info.title"
    servers = [s["url"] for s in spec.get("servers", [])]
    assert "https://cockpit-api.spencervaradi.com" in servers, "missing server url"


def test_all_endpoints_documented():
    spec = _load_spec()
    paths = spec.get("paths", {})
    for path, methods in EXPECTED_PATHS.items():
        assert path in paths, f"missing path {path}"
        for method in methods:
            assert method in paths[path], f"missing {method.upper()} {path}"


def test_request_and_response_schemas_resolve():
    spec = _load_spec()
    schemas = spec.get("components", {}).get("schemas", {})

    def walk(node) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                assert ref.startswith("#/"), f"external ref not supported: {ref}"
                name = ref.split("/")[-1]
                assert name in schemas, f"unresolved schema ref: {ref}"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            walk(operation.get("requestBody", {}))
            walk(operation.get("responses", {}))


def test_every_response_has_a_schema():
    spec = _load_spec()
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            assert responses, f"{method.upper()} {path} has no responses"
            for status, response in responses.items():
                if status.startswith("2"):
                    assert "content" in response, (
                        f"{method.upper()} {path} {status} missing content schema"
                    )
