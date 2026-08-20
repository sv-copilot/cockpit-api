"""Planning context resolver for CrewAI dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TreeNotFoundError(LookupError):
    """Raised when a repo has no slice dependency tree."""


class SliceNotFoundError(LookupError):
    """Raised when slice_id is absent from the planning tree."""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry_entry(registry: dict[str, Any], repo_id: str) -> dict[str, Any] | None:
    for project in registry.get("projects", []):
        if project.get("id") == repo_id:
            return project
    return None


def _open_questions_for_slice(
    questions_doc: dict[str, Any],
    *,
    repo_id: str,
    slice_id: str,
    slice_number: int | None,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for question in questions_doc.get("questions", []):
        if question.get("status") != "open":
            continue
        blocks_ids = question.get("blocks_slice_ids") or []
        blocks_numbers = question.get("blocks_slice_numbers") or []
        related_repos = question.get("related_repo_ids") or []
        source_owners = {
            ref.get("owner")
            for ref in question.get("source_refs") or []
            if isinstance(ref, dict)
        }
        if slice_id in blocks_ids:
            matched.append(question)
            continue
        if slice_number is not None and slice_number in blocks_numbers:
            matched.append(question)
            continue
        if repo_id in related_repos or repo_id in source_owners:
            matched.append(question)
    return matched


def resolve_planning_context(
    planning_path: Path,
    repo_id: str,
    slice_id: str,
) -> dict[str, Any]:
    """Load tree, slice detail, open questions, and registry for dispatch."""
    tree_path = planning_path / f".docs/planning/projects/{repo_id}/slice_dependency_tree.json"
    if not tree_path.exists():
        raise TreeNotFoundError(f"No namespace tree for {repo_id}")

    tree = _load_json(tree_path)
    slice_record: dict[str, Any] | None = None
    for entry in tree.get("slices", []):
        if entry.get("slice_id") == slice_id:
            slice_record = entry
            break
    if slice_record is None:
        raise SliceNotFoundError(f"Slice {slice_id} not found in {repo_id}")

    registry_path = planning_path / ".docs/projects-registry.json"
    registry = _load_json(registry_path) if registry_path.exists() else {"projects": []}
    registry_project = _registry_entry(registry, repo_id)

    detail_rel = f".docs/planning/projects/{repo_id}/slices/{slice_id}.md"
    detail_path = planning_path / detail_rel
    slice_detail = detail_path.read_text(encoding="utf-8") if detail_path.exists() else ""

    questions_path = planning_path / ".docs/operator_questions.json"
    questions_doc = _load_json(questions_path) if questions_path.exists() else {"questions": []}
    open_questions = _open_questions_for_slice(
        questions_doc,
        repo_id=repo_id,
        slice_id=slice_id,
        slice_number=slice_record.get("slice_number"),
    )

    # PR identity for crews whose task templates interpolate {pr_number}/{pr_title}.
    # Source pr_number from branch_posture.last_known_pr, falling back to the
    # top-level last_known_pr field, and coerce to a string (fail-closed to "").
    posture = slice_record.get("branch_posture") or {}
    raw_pr_number = posture.get("last_known_pr") or slice_record.get("last_known_pr")
    pr_number = str(raw_pr_number).strip() if raw_pr_number else ""
    pr_title = slice_record.get("title") or ""

    return {
        "repo_id": repo_id,
        "slice_id": slice_id,
        "slice": slice_record,
        "slice_detail_path": detail_rel,
        "slice_detail": slice_detail,
        "tree_path": str(tree_path.relative_to(planning_path)),
        "registry_project": registry_project,
        "open_questions": open_questions,
        "pr_number": pr_number,
        "pr_title": pr_title,
        "operator_input": (
            f"Refine and analyze slice {slice_id} ({slice_record.get('title', '')}). "
            f"Open questions count: {len(open_questions)}."
        ),
    }
