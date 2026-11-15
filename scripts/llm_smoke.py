"""Live check of the Anthropic-backed LLM layer on three cases.

    Put ANTHROPIC_API_KEY in .env (copied from .env.example; .env is gitignored), then:
    python scripts/llm_smoke.py [PA-001 PA-003 PA-004]

Prints the recommendation (must match the label), the AI summary, token usage, and any model
output that verification rejected.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LLM_PROVIDER", "anthropic")
logging.basicConfig(level=logging.WARNING)
for name in ("httpx", "httpx2", "anthropic"):
    logging.getLogger(name).setLevel(logging.WARNING)

from app import config  # noqa: E402  (loads .env)
from app.agents.supervisor import Supervisor  # noqa: E402
from app.llm import get_llm  # noqa: E402
from app.services.mcp_server import mcp  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"


async def main(ids):
    llm = get_llm()
    if llm is None:
        sys.exit("No API key found. Put ANTHROPIC_API_KEY=... in the .env file in the project folder.")
    labels = {l["case_id"]: l for l in json.loads((DATA / "labels.json").read_text(encoding="utf-8"))}
    for cid in ids:
        case = json.loads((DATA / "cases" / f"{cid}.json").read_text(encoding="utf-8"))
        r, _, _ = await Supervisor(mcp, a2a=None, llm=llm).run(case)
        ok = r.recommendation == labels[cid]["expected_recommendation"]
        print(f"\n{'PASS' if ok else 'FAIL'} {cid}: {r.recommendation}  (summary: {r.summary_source})")
        print("  ", r.summary)
        print("   usage:", {k: v for k, v in r.llm_usage.items() if k in ("calls", "input_tokens", "output_tokens", "cost_usd")})
        for x in r.llm_usage["rejected_items"] + r.llm_usage["failures"]:
            print("   rejected/failed:", x)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["PA-001", "PA-003", "PA-004"]))
