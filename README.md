# Cockpit API

FastAPI read-model service for the
[Autonomous Development Governance](https://github.com/sv-copilot/drake-governance)
cockpit UI.

Reads canonical planning data from a git-synced drake-governance checkout
(`PLANNING_CHECKOUT_PATH`). Never writes to Git directly — dispatch confirm
enqueues runner jobs via webhook.

## Quick Start

```bash
# Install deps
pip install -r requirements.txt

# Run locally (points at local drake-governance checkout)
PLANNING_CHECKOUT_PATH=/path/to/drake-governance uvicorn main:app --reload

# Or with Docker
docker build -t cockpit-api .
docker run -p 8080:8080 -e PLANNING_CHECKOUT_PATH=/data/planning/drake-governance cockpit-api
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/projects` | List registered projects |
| GET | `/projects/{id}/progress` | Slice progress summary |
| GET | `/dispatch/preview/{repo}/{slice}` | Dry-run dispatch preview |
| POST | `/dispatch/confirm` | Enqueue runner job |
| GET | `/runs` | List recent runs |
| GET | `/credentials/inventory` | Credential inventory |
| POST | `/crewai/dispatch` | Dispatch CrewAI workflow |
| GET | `/crewai/runs` | List CrewAI runs |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PLANNING_CHECKOUT_PATH` | `/data/planning/drake-governance` | Path to git-synced planning data |
| `COCKPIT_API_PORT` | `8080` | Listen port |
| `COCKPIT_CORS_ORIGINS` | `http://localhost:8081` | CORS allowed origins |
| `DATABASE_URL` | — | PostgreSQL connection for runs DB |
| `QUEUE_PATH` | `/data/cockpit/queue.json` | Dispatch queue file |
