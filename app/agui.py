"""AG-UI event emitter.

Events follow the AG-UI protocol event types (RUN_STARTED, STEP_STARTED, TOOL_CALL_START,
TOOL_CALL_ARGS, TOOL_CALL_END, TOOL_CALL_RESULT, STATE_SNAPSHOT, STATE_DELTA,
TEXT_MESSAGE_*, CUSTOM, RUN_ERROR, RUN_FINISHED) with camelCase fields. The API layer streams
them to the browser as Server-Sent Events; the same list is kept as the run trace.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Awaitable, Callable

Sink = Callable[[dict], Awaitable[None]]


class EventEmitter:
    def __init__(self, thread_id: str, sink: Sink | None = None):
        self.thread_id = thread_id
        self.run_id = f"run-{uuid.uuid4().hex[:8]}"
        self.events: list[dict] = []
        self._sink = sink

    async def emit(self, type_: str, **fields: Any) -> dict:
        ev = {"type": type_, "timestamp": int(time.time() * 1000), **fields}
        self.events.append(ev)
        if self._sink:
            await self._sink(ev)
        return ev

    # convenience wrappers --------------------------------------------------------------
    async def run_started(self):
        await self.emit("RUN_STARTED", threadId=self.thread_id, runId=self.run_id)

    async def run_finished(self, result: Any = None):
        await self.emit("RUN_FINISHED", threadId=self.thread_id, runId=self.run_id, result=result)

    async def run_error(self, message: str, code: str = "AGENT_ERROR"):
        await self.emit("RUN_ERROR", message=message, code=code)

    async def step(self, name: str, started: bool = True):
        await self.emit("STEP_STARTED" if started else "STEP_FINISHED", stepName=name)

    async def tool_start(self, name: str, args: dict, agent: str) -> str:
        tid = f"call-{uuid.uuid4().hex[:8]}"
        await self.emit("TOOL_CALL_START", toolCallId=tid, toolCallName=name, agent=agent)
        await self.emit("TOOL_CALL_ARGS", toolCallId=tid, delta=json.dumps(args))
        await self.emit("TOOL_CALL_END", toolCallId=tid)
        return tid

    async def tool_result(self, tid: str, content: Any, ok: bool = True, ms: float | None = None):
        text = content if isinstance(content, str) else json.dumps(content)[:600]
        await self.emit("TOOL_CALL_RESULT", messageId=f"msg-{tid}", toolCallId=tid, role="tool",
                        content=text, ok=ok, latencyMs=ms)

    async def state_delta(self, ops: list[dict]):
        await self.emit("STATE_DELTA", delta=ops)

    async def state_snapshot(self, snapshot: dict):
        await self.emit("STATE_SNAPSHOT", snapshot=snapshot)

    async def message(self, text: str, role: str = "assistant"):
        mid = f"msg-{uuid.uuid4().hex[:8]}"
        await self.emit("TEXT_MESSAGE_START", messageId=mid, role=role)
        await self.emit("TEXT_MESSAGE_CONTENT", messageId=mid, delta=text)
        await self.emit("TEXT_MESSAGE_END", messageId=mid)

    async def custom(self, name: str, value: Any):
        await self.emit("CUSTOM", name=name, value=value)


def queue_sink(q: asyncio.Queue) -> Sink:
    async def _sink(ev: dict):
        await q.put(ev)
    return _sink
