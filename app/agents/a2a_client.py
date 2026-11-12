"""Minimal A2A client for calling the coverage agent service."""
from __future__ import annotations

import uuid

import httpx

from app import config


class A2AUnavailable(RuntimeError):
    pass


class A2AClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = config.A2A_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, transport=self.transport, timeout=self.timeout)

    async def agent_card(self) -> dict:
        try:
            async with self._client() as c:
                r = await c.get("/.well-known/agent-card.json")
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise A2AUnavailable(f"agent card unavailable: {e}") from e

    async def send(self, data: dict, context_id: str) -> dict:
        req = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": "message/send",
               "params": {"message": {"kind": "message", "role": "user", "messageId": uuid.uuid4().hex,
                                      "contextId": context_id, "parts": [{"kind": "data", "data": data}]}}}
        try:
            async with self._client() as c:
                r = await c.post("/", json=req)
                r.raise_for_status()
                body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise A2AUnavailable(str(e) or type(e).__name__) from e
        if "error" in body:
            raise A2AUnavailable(f"A2A error {body['error']}")
        task = body["result"]
        if task.get("status", {}).get("state") != "completed":
            raise A2AUnavailable(f"task not completed: {task.get('status')}")
        return task["artifacts"][0]["parts"][0]["data"] | {"_a2a_tool_calls": task.get("metadata", {}).get("tool_calls", [])}
