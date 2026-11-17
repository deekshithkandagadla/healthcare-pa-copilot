#!/usr/bin/env bash
# Start every service as its own process: FHIR API, MCP server, A2A coverage agent, copilot API + UI.
# Stop with Ctrl+C. If a port is still busy afterwards, run scripts/stop_all.sh.
set -uo pipefail
cd "$(dirname "$0")/.."
PORTS="8100 8101 8102 8103"

busy=$(python - $PORTS <<'PY'
import socket, sys
for p in sys.argv[1:]:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", int(p)))
    except OSError:
        print(p, end=" ")
    finally:
        s.close()
PY
)
if [ -n "$busy" ]; then
  echo "Port(s) already in use: $busy"
  echo "An earlier run is probably still running. Stop it with:  scripts/stop_all.sh"
  exit 1
fi

export FHIR_BASE_URL=http://127.0.0.1:8101/fhir MCP_URL=http://127.0.0.1:8102/mcp COVERAGE_A2A_URL=http://127.0.0.1:8103
mkdir -p .runtime
uvicorn app.services.fhir_api:app --port 8101 > .runtime/fhir.log 2>&1 &
python -m app.services.mcp_server > .runtime/mcp.log 2>&1 &
sleep 1
uvicorn app.services.coverage_a2a:app --port 8103 > .runtime/a2a.log 2>&1 &
uvicorn app.api:app --port 8100 > .runtime/api.log 2>&1 &
trap 'kill $(jobs -p) 2>/dev/null' EXIT

echo -n "Starting (loading the embedding model)"
for i in $(seq 1 60); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/api/health', timeout=1)" 2>/dev/null; then
    echo; echo "UI: http://127.0.0.1:8100   (logs in .runtime/; stop with Ctrl+C)"
    wait
    exit 0
  fi
  if [ -z "$(jobs -pr)" ]; then break; fi
  echo -n "."; sleep 1
done
echo; echo "The services did not start. Last lines of each log:"
for f in api mcp a2a fhir; do echo "--- .runtime/$f.log"; tail -n 8 ".runtime/$f.log"; done
exit 1
