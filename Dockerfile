# Cockpit API — Docker image
#
# Standalone FastAPI service. Reads planning data from PLANNING_CHECKOUT_PATH
# (git-synced drake-governance checkout). No Cline SDK, no Node.
#
# Build:
#   docker build -t cockpit-api .
#
# Run:
#   docker run -p 8080:8080 -e PLANNING_CHECKOUT_PATH=/data/planning/drake-governance cockpit-api

FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy API source
COPY *.py /app/
COPY credential_broker/ /app/credential_broker/

# Copy CrewAI orchestrator (needed by crewai_routes.py at import time)
COPY crewai_orchestrator/ /app/crewai_orchestrator/

# Copy schemas for validation
COPY schemas/ /app/schemas/

# Copy scripts (projects_registry helpers)
COPY scripts/ /app/scripts/

# Make modules discoverable
ENV PYTHONPATH=/app

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
