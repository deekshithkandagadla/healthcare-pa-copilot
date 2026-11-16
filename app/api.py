"""Copilot API: case list, AG-UI streamed investigations, A2UI surfaces, reviewer actions.

    uvicorn app.api:app --port 8100

POST /api/cases/{id}/investigate returns an AG-UI event stream (text/event-stream).
When MCP_URL / COVERAGE_A2A_URL are unset, the MCP server and the A2A coverage agent run
in-process (handy for a quick look); `make run` / docker-compose run them as separate services.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config
from app.a2ui import build_surface
from app.agents.a2a_client import A2AClient
from app.agents.supervisor import Supervisor
from app.agui import EventEmitter, queue_sink
from app.retrieval.index import get_index
from app.store import CaseStore, Disposition

logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("pa.api")

app = FastAPI(title="Prior Authorization Copilot")
store = CaseStore()

if config.MCP_URL:
    MCP_TARGET = config.MCP_URL
else:
    from app.services.mcp_server import mcp as MCP_TARGET  # in-process

if config.COVERAGE_A2A_URL:
    _a2a_local = None
    A2A = A2AClient(config.COVERAGE_A2A_URL)
else:
    from app.services.coverage_a2a import create_app
    _a2a_local = create_app(mcp_target=MCP_TARGET)
    A2A = A2AClient("http://coverage-agent.local", transport=httpx.ASGITransport(app=_a2a_local))


def _case_summary(st) -> dict:
    r = st.latest
    return {**st.case, "status": st.status, "recommendation": r.recommendation if r else None,
            "runs": len(st.runs), "disposition": st.disposition}


@app.get("/api/health")
def health():
    return {"status": "ok", "mcp": "remote" if config.MCP_URL else "in-process",
            "a2a": "remote" if config.COVERAGE_A2A_URL else "in-process"}


SUPPORTED_SERVICES = {"72148": "MRI lumbar spine without contrast"}


def _patients() -> dict[str, dict]:
    """Synthetic patients available for new cases: ID, sex, age only (no names or birth dates)."""
    out = {}
    for f in sorted((config.DATA_DIR / "fhir").glob("PT-*.json")):
        pt = next(e["resource"] for e in json.loads(f.read_text(encoding="utf-8"))["entry"]
                  if e["resource"]["resourceType"] == "Patient")
        b = date.fromisoformat(pt["birthDate"])
        t = date.today()
        out[f.stem] = {"patient_id": f.stem, "sex": pt.get("gender"),
                       "age": t.year - b.year - ((t.month, t.day) < (b.month, b.day))}
    return out


@app.get("/api/patients")
def list_patients():
    return {"patients": list(_patients().values()), "services": SUPPORTED_SERVICES}


class NewCase(BaseModel):
    patient_id: str
    service_code: str = "72148"
    service_date: str
    summary: str = ""


@app.post("/api/cases", status_code=201)
def submit_case(body: NewCase):
    if body.patient_id not in _patients():
        raise HTTPException(422, f"Unknown synthetic patient {body.patient_id}")
    if body.service_code not in SUPPORTED_SERVICES:
        raise HTTPException(422, f"Service {body.service_code} is not supported; use one of {list(SUPPORTED_SERVICES)}")
    try:
        sd = date.fromisoformat(body.service_date)
    except ValueError:
        raise HTTPException(422, "service_date must be YYYY-MM-DD")
    if not date(2024, 1, 1) <= sd <= date(2027, 12, 31):
        raise HTTPException(422, "service_date must be between 2024-01-01 and 2027-12-31")
    return store.submit_case(body.patient_id, body.service_code, sd.isoformat(), body.summary.strip()[:200])


@app.get("/api/cases")
def list_cases():
    return [_case_summary(st) for st in store.cases.values()]


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    try:
        st = store.get(case_id)
    except KeyError:
        raise HTTPException(404, "Unknown case")
    run = st.runs[-1] if st.runs else None
    return {"case": _case_summary(st), "result": run["result"].model_dump() if run else None,
            "a2ui": run["a2ui"] if run else None, "trace": run["trace"] if run else None,
            "tool_calls": run["tool_calls"] if run else None, "reviewer_inputs": st.reviewer_inputs,
            "clarifications": st.clarifications}


@app.post("/api/cases/{case_id}/investigate")
async def investigate(case_id: str):
    """AG-UI: POST starts a run; the response body is the live event stream."""
    try:
        st = store.get(case_id)
    except KeyError:
        raise HTTPException(404, "Unknown case")
    q: asyncio.Queue = asyncio.Queue()
    emitter = EventEmitter(thread_id=case_id, sink=queue_sink(q))
    st.status = "investigating"

    async def worker():
        try:
            surface_box: list = []

            async def send_surface(result, em):
                policy = get_index().policies.get(result.applicable_policy_id or "")
                surface_box.append(build_surface(result, policy))
                await em.custom("a2ui", surface_box[0])

            sup = Supervisor(MCP_TARGET, a2a=A2A)
            result, em, calls = await sup.run(st.case, st.reviewer_inputs, emitter=emitter, before_finish=send_surface)
            surface = surface_box[0]
            store.record_run(case_id, {"run_id": em.run_id, "result": result, "trace": em.events,
                                       "tool_calls": calls, "a2ui": surface})
        except Exception as e:  # never leave the stream hanging
            log.exception("run failed")
            await emitter.run_error(f"{type(e).__name__}: {e}", code="UNHANDLED")
        finally:
            await q.put(None)

    async def stream():
        task = asyncio.create_task(worker())
        while True:
            ev = await q.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev, default=str)}\n\n"
        await task

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ReviewAction(BaseModel):
    action: str                      # request_clarification | supply_data | record_disposition
    reviewer: str = "reviewer-demo"
    message: str = ""
    fact: str | None = None
    value: float | bool | int | str | None = None
    note: str = ""
    disposition: Disposition | None = None


@app.post("/api/cases/{case_id}/review")
def review(case_id: str, body: ReviewAction):
    try:
        if body.action == "request_clarification":
            return store.request_clarification(case_id, body.reviewer, body.message)
        if body.action == "supply_data":
            return store.supply_data(case_id, body.reviewer, body.fact, body.value, body.note)
        if body.action == "record_disposition":
            return store.record_disposition(case_id, body.reviewer, body.disposition, body.message)
    except KeyError:
        raise HTTPException(404, "Unknown case")
    except ValueError as e:
        raise HTTPException(422, str(e))
    raise HTTPException(400, "Unknown action")


@app.get("/api/cases/{case_id}/audit")
def audit(case_id: str):
    return [a for a in store.audit if a["case_id"] == case_id]


COVERAGE_TOOLS = ["check_eligibility", "get_authorization_history"]
_outage_mode = "none"


@app.get("/api/admin/outage")
def outage_status():
    return {"mode": _outage_mode}


@app.post("/api/admin/outage")
async def outage(body: dict):
    """Demo control.
    mode=none: normal. mode=a2a: remote coverage agent down -> in-process fallback.
    mode=full: A2A agent down AND the MCP coverage tools failing -> RUN_ERROR + safe result.
    (`coverage_down: true` is accepted as mode=a2a for older clients.)"""
    global _outage_mode
    mode = body.get("mode") or ("a2a" if body.get("coverage_down") else "none")
    if mode not in ("none", "a2a", "full"):
        raise HTTPException(400, "mode must be none, a2a or full")
    a2a_down, fail_tools = mode != "none", COVERAGE_TOOLS if mode == "full" else []
    if _a2a_local is not None:
        _a2a_local.state.down = a2a_down
        from app.services import mcp_server as _mcp_mod
        _mcp_mod.FAULTS.clear()
        _mcp_mod.FAULTS.update(fail_tools)
    else:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(f"{config.COVERAGE_A2A_URL}/admin/outage", json={"down": a2a_down})
            mcp_base = config.MCP_URL.rsplit("/mcp", 1)[0]
            await c.post(f"{mcp_base}/admin/faults", json={"fail_tools": fail_tools})
    _outage_mode = mode
    return {"mode": mode, "coverage_down": a2a_down, "mcp_failing_tools": fail_tools}


_FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(_FRONTEND / "index.html")
