"""Coverage and eligibility agent as a standalone A2A service.

    uvicorn app.services.coverage_a2a:app --port 8103

Implements the subset of the A2A protocol (v0.3 JSON-RPC binding) this POC needs:
  GET  /.well-known/agent-card.json   agent discovery
  POST /                              JSON-RPC 2.0 `message/send` -> completed Task with a data artifact
Simplified: no streaming, push notifications or task persistence (every task completes
synchronously). POST /admin/outage toggles a simulated outage for the demo.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import config
from app.agents.coverage import run_coverage
from app.agents.tools import McpTools
from app.agui import EventEmitter

AGENT_CARD = {
    "protocolVersion": "0.3.0",
    "name": "coverage-eligibility-agent",
    "description": "Checks synthetic member eligibility and prior authorization history for a service date.",
    "url": "http://localhost:8103/",
    "preferredTransport": "JSONRPC",
    "version": "0.1.0",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "defaultInputModes": ["application/json"],
    "defaultOutputModes": ["application/json"],
    "skills": [{
        "id": "check_coverage",
        "name": "Check coverage and authorization history",
        "description": "Input: {patient_id, service_code, service_date}. Output: eligibility, plan, prior authorizations, evidence.",
        "tags": ["eligibility", "prior-authorization"],
    }],
}


def _rpc_error(rid: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}, status_code=status)


def create_app(mcp_target: Any = None) -> FastAPI:
    app = FastAPI(title="Coverage A2A agent")
    app.state.down = os.getenv("COVERAGE_OUTAGE", "0") == "1"
    target = mcp_target or config.MCP_URL

    @app.get("/.well-known/agent-card.json")
    def card():
        return AGENT_CARD

    @app.get("/health")
    def health():
        return {"status": "down" if app.state.down else "ok"}

    @app.post("/admin/outage")
    def outage(body: dict):
        app.state.down = bool(body.get("down"))
        return {"down": app.state.down}

    @app.post("/")
    async def rpc(request: Request):
        if app.state.down:
            return JSONResponse({"error": "service unavailable (simulated outage)"}, status_code=503)
        body = await request.json()
        rid = body.get("id")
        if body.get("jsonrpc") != "2.0" or body.get("method") != "message/send":
            return _rpc_error(rid, -32601, "Method not found")
        parts = body.get("params", {}).get("message", {}).get("parts", [])
        data = next((p.get("data") for p in parts if p.get("kind") == "data"), None)
        if not data or not {"patient_id", "service_code", "service_date"} <= set(data):
            return _rpc_error(rid, -32602, "Invalid params: need a data part with patient_id, service_code, service_date")
        emitter = EventEmitter(thread_id=f"a2a-{rid}")
        async with McpTools(target, emitter, budget=6) as tools:
            result = await run_coverage(tools, data["patient_id"], data["service_code"], data["service_date"])
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "kind": "task", "id": task_id, "contextId": body["params"]["message"].get("contextId", task_id),
            "status": {"state": "completed"},
            "artifacts": [{"artifactId": f"{task_id}-coverage", "name": "coverage_result",
                           "parts": [{"kind": "data", "data": result}]}],
            "metadata": {"tool_calls": tools.calls},
        }}

    return app


app = create_app()
