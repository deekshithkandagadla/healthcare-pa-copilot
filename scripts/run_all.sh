#!/usr/bin/env bash
# Start every service as its own process: FHIR API, MCP server, A2A coverage agent, copilot API + UI.
set -euo pipefail
cd "$(dirname "$0")/.."
export FHIR_BASE_URL=http://127.0.0.1:8101/fhir MCP_URL=http://127.0.0.1:8102/mcp COVERAGE_A2A_URL=http://127.0.0.1:8103
mkdir -p .runtime
uvicorn app.services.fhir_api:app --port 8101 > .runtime/fhir.log 2>&1 &
python -m app.services.mcp_server > .runtime/mcp.log 2>&1 &
sleep 1
uvicorn app.services.coverage_a2a:app --port 8103 > .runtime/a2a.log 2>&1 &
uvicorn app.api:app --port 8100 > .runtime/api.log 2>&1 &
trap 'kill $(jobs -p) 2>/dev/null' EXIT
echo "UI: http://127.0.0.1:8100   (logs in .runtime/)"
wait
