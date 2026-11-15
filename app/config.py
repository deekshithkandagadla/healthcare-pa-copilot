"""Central settings. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from .env (gitignored). Real environment variables win.
    Strips quotes and Windows line endings so a key saved from Notepad still works."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


_load_dotenv()
DATA_DIR = Path(os.getenv("PA_DATA_DIR", ROOT / "data"))
RUNTIME_DIR = Path(os.getenv("PA_RUNTIME_DIR", ROOT / ".runtime"))

# Service endpoints. When unset, components talk to each other in-process
# (used by the test suite); docker-compose / `make run` sets them.
FHIR_BASE_URL = os.getenv("FHIR_BASE_URL")              # e.g. http://localhost:8101/fhir
MCP_URL = os.getenv("MCP_URL")                          # e.g. http://localhost:8102/mcp
COVERAGE_A2A_URL = os.getenv("COVERAGE_A2A_URL")        # e.g. http://localhost:8103

# Reliability limits
MAX_TOOL_CALLS_PER_CASE = int(os.getenv("MAX_TOOL_CALLS_PER_CASE", "25"))
TOOL_TIMEOUT_SECONDS = float(os.getenv("TOOL_TIMEOUT_SECONDS", "5"))
A2A_TIMEOUT_SECONDS = float(os.getenv("A2A_TIMEOUT_SECONDS", "4"))
MAX_AGENT_RETRIES = int(os.getenv("MAX_AGENT_RETRIES", "1"))

# Clinical windows used by the rules
PT_LOOKBACK_DAYS = 183           # "within the 6 months before the request"
PRIOR_IMAGING_LOOKBACK_DAYS = 365
NOTE_CLAIM_TOLERANCE_WEEKS = 2   # note vs. encounter disagreement threshold

# Optional LLM (fact extraction + reviewer summary). Off unless a provider key is set.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none")        # none | anthropic | openai
LLM_MODEL = os.getenv("LLM_MODEL", "")
