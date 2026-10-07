"""Runs the agent graph. MCP connections are opened for each graph segment (start -> interrupt,
then resume -> end), so a paused case holds no open processes while it waits for an officer."""
from __future__ import annotations

import asyncio
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ..audit.logger import log_event, new_id
from ..config import get_settings
from .graph import build_graph
from .toolbox import CURRENT, Toolbox


class AgentRunner:
    def __init__(self):
        self.saver = InMemorySaver()
        self.graph = build_graph(self.saver)

    def _cfg(self, run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 50}

    async def _segment(self, run_id: str, case_id: str, payload: Any) -> dict:
        async with Toolbox(run_id) as tb:
            tb.case_id = case_id
            token = CURRENT.set(tb)
            try:
                await self.graph.ainvoke(payload, self._cfg(run_id))
            finally:
                CURRENT.reset(token)
        return self.state(run_id)

    async def astart(self, case_id: str) -> dict:
        run_id = new_id("run")
        s = get_settings()
        log_event(run_id=run_id, case_id=case_id, event_type="run_started",
                  details={"model_version": s.model_version, "open_weight_backend": s.is_open_weight_backend,
                           "faults": s.faults or None})
        return await self._segment(run_id, case_id, {"run_id": run_id, "case_id": case_id, "activity": []})

    async def aresume(self, run_id: str, officer_input: dict) -> dict:
        case_id = self.state(run_id)["case_id"]
        return await self._segment(run_id, case_id, Command(resume=officer_input))

    def start(self, case_id: str) -> dict:
        return asyncio.run(self.astart(case_id))

    def resume(self, run_id: str, officer_input: dict) -> dict:
        return asyncio.run(self.aresume(run_id, officer_input))

    def state(self, run_id: str) -> dict:
        snap = self.graph.get_state(self._cfg(run_id))
        out = dict(snap.values)
        out["_waiting_for_officer"] = bool(snap.next) and "human_approval" in snap.next
        return out
