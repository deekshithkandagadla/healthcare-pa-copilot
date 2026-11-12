"""MCP tool client used by the agents: budget, timeout, tracing and safe failure."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from mcp import Client

from app import config
from app.agui import EventEmitter


class ToolBudgetExceeded(RuntimeError):
    pass


class ToolError(RuntimeError):
    pass


class McpTools:
    """Wraps one MCP client session for the duration of a case run."""

    def __init__(self, target: Any, emitter: EventEmitter, budget: int = config.MAX_TOOL_CALLS_PER_CASE,
                 timeout: float = config.TOOL_TIMEOUT_SECONDS):
        self.target = target          # MCP URL, or an in-process MCPServer in tests
        self.emitter = emitter
        self.budget = budget
        self.timeout = timeout
        self.calls: list[dict] = []
        self._client: Client | None = None

    async def __aenter__(self):
        self._client = Client(self.target)
        await asyncio.wait_for(self._client.__aenter__(), timeout=self.timeout)
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.__aexit__(*exc)

    async def list_tools(self) -> list[str]:
        res = await self._client.list_tools()
        return [t.name for t in res.tools]

    async def call(self, name: str, args: dict, agent: str) -> dict:
        if len(self.calls) >= self.budget:
            raise ToolBudgetExceeded(f"tool budget of {self.budget} calls reached")
        tid = await self.emitter.tool_start(name, args, agent)
        t0 = time.perf_counter()
        record = {"tool": name, "agent": agent, "args": args, "ok": False}
        self.calls.append(record)
        try:
            res = await asyncio.wait_for(self._client.call_tool(name, args), timeout=self.timeout)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            text = res.content[0].text if res.content else "{}"
            if res.is_error:
                raise ToolError(text)
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise ToolError(f"invalid JSON from {name}: {e}") from e
            record.update(ok=True, ms=ms)
            await self.emitter.tool_result(tid, _preview(data), ok=True, ms=ms)
            return data
        except asyncio.TimeoutError:
            record.update(error="timeout")
            await self.emitter.tool_result(tid, f"timeout after {self.timeout}s", ok=False)
            raise ToolError(f"{name} timed out")
        except ToolError as e:
            record.update(error=str(e))
            await self.emitter.tool_result(tid, str(e), ok=False)
            raise


def _preview(data: dict) -> dict:
    """Keep traces small and free of note bodies."""
    out = {}
    for k, v in data.items():
        if k == "notes":
            out[k] = [{"note_id": n["note_id"], "date": n["date"], "chars": len(n["text"])} for n in v]
        elif k == "resources":
            out[k] = [f"{r['resourceType']}/{r['id']}" for r in v]
        elif k == "hits":
            out[k] = [h["chunk_id"] for h in v]
        else:
            out[k] = v
    return out
