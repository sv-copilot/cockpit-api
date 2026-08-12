# Cockpit API — Agent Operating Contract

This repository is the **cockpit API** for the Autonomous Development Governance
system. It is a read-only FastAPI service that aggregates planning data from a
git-synced drake-governance checkout.

## Product Context

The cockpit API serves as the backend for the Cockpit UI. It reads canonical
planning trees, project registries, and operator questions from the
`PLANNING_CHECKOUT_PATH` (a git checkout of drake-governance). It never writes
to Git directly — dispatch operations enqueue runner jobs via webhooks.

## Branch Policy

- `main` — production branch
- `dev` — integration branch; all feature branches target `dev`
- Feature branches: `agent/*` prefix

## TDD Policy

Test-driven development is mandatory. Red→Green→Refactor→Prove loop required.

Test files must match: `test_*.py`, `*_test.py`, `tests/*`

## Validation

```bash
# Run tests
python -m pytest

# Lint
python -m py_compile main.py crewai_routes.py credentials_routes.py webhook_routes.py
```
