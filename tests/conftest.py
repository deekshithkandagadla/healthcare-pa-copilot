import json
import logging
import os
from pathlib import Path

import httpx
import pytest

# Tests never call a real model (no cost, deterministic), even if .env holds a key.
os.environ["LLM_PROVIDER"] = "none"
os.environ["EMBEDDINGS"] = "lsa"   # deterministic and offline; neural path tested with a stand-in model
os.environ.setdefault("PA_RUNTIME_DIR", str(Path(__file__).parent / ".runtime"))
logging.getLogger("httpx").setLevel(logging.WARNING)

from app.agents.a2a_client import A2AClient  # noqa: E402
from app.agents.supervisor import Supervisor  # noqa: E402
from app.services.coverage_a2a import create_app  # noqa: E402
from app.services.mcp_server import mcp  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
LABELS = {l["case_id"]: l for l in json.loads((DATA / "labels.json").read_text(encoding="utf-8"))}


def load_case(cid: str) -> dict:
    return json.loads((DATA / "cases" / f"{cid}.json").read_text(encoding="utf-8"))


@pytest.fixture
def a2a_app():
    return create_app(mcp_target=mcp)


@pytest.fixture
def supervisor(a2a_app):
    a2a = A2AClient("http://coverage", transport=httpx.ASGITransport(app=a2a_app))
    return Supervisor(mcp, a2a=a2a)
