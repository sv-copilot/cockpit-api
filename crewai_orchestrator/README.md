# CrewAI Orchestrator

CrewAI-based agent orchestration layer for the Drake portfolio. Replaces/augments
Cline skill-based workflows with structured, deterministic agent crews.

## Directory Layout

```
tools/crewai-orchestrator/
├── agents/          # Agent definitions (PO, EL, RM)
├── crews/           # Crew compositions (refinement, audit, sync)
├── tools/           # Shared CrewAI tools (git, gh, filesystem, cockpit API)
├── tasks/           # Reusable task templates
├── main.py          # Entry point and smoke test
├── runner.py        # Background runner (polls cockpit, dispatches crews)
├── requirements.txt # Python dependencies
└── README.md        # This file
```

## Quick Start

```bash
# Install dependencies
pip install -r tools/crewai-orchestrator/requirements.txt

# Smoke test
python3 tools/crewai-orchestrator/main.py
```

## Architecture

```
cockpit API (:8080)
    │
    ▼
runner.py (polls /cockpit/progress every 60s)
    │
    ▼
POST /crewai/dispatch → resolves crew_type → instantiates crew → runs tasks
    │
    ▼
evidence contract written to adapters/evidence-contract.schema.json
```

## Adding an Agent

1. Create `agents/<agent_name>.py` with a `crewai.Agent` instance
2. Assign tools from `tools/`
3. Register in `agents/__init__.py`

## Adding a Crew

1. Create `crews/<crew_name>.py` with a `crewai.Crew` instance
2. Add sequential `crewai.Task` definitions
3. Register in `crews/__init__.py`

## Development

- Run tests: `python3 -m pytest tools/crewai-orchestrator/ -q`
- Type check: `python3 -m mypy tools/crewai-orchestrator/` (when mypy added)
