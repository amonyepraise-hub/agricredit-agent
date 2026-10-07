"""MCP client connections used by the agent graph, with per-call audit logging and bounded retry.

Two MCP servers:
  agricredit   our own server (python -m agricredit.mcp_server), stdio
  filesystem   the official @modelcontextprotocol/server-filesystem, stdio, rooted at fs_mcp_root/
"""
from __future__ import annotations

import contextvars
import json
import os
import shutil
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from ..audit.logger import log_tool_call
from ..config import REPO_ROOT, get_settings

FS_SERVER_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.8.31"


def fs_server_params() -> StdioServerParameters:
    root = str(Path(get_settings().fs_mcp_root).resolve())
    custom = os.getenv("AGRICREDIT_FS_MCP_CMD")
    local_bin = REPO_ROOT / "node_modules" / ".bin" / "mcp-server-filesystem"
    if custom:
        return StdioServerParameters(command=custom, args=[root])
    if local_bin.exists():
        return StdioServerParameters(command=str(local_bin), args=[root])
    if shutil.which("mcp-server-filesystem"):
        return StdioServerParameters(command="mcp-server-filesystem", args=[root])
    return StdioServerParameters(command="npx", args=["-y", FS_SERVER_PACKAGE, root])


def agricredit_server_params() -> StdioServerParameters:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    return StdioServerParameters(command=sys.executable, args=["-m", "agricredit.mcp_server"], env=env)


@dataclass
class ToolOutcome:
    ok: bool
    data: Any = None
    error: str | None = None
    attempts: int = 1

    @property
    def code(self) -> str | None:
        if self.ok or not self.error:
            return None
        for c in ("NOT_FOUND", "UNAVAILABLE", "INVALID_INPUT", "REFUSED"):
            if c in self.error:
                return c
        return "ERROR"


class Toolbox:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.case_id: str | None = None
        self._stack = AsyncExitStack()
        self.clients: dict[str, Client] = {}
        self.tool_catalogue: dict[str, list[dict]] = {}

    async def __aenter__(self) -> "Toolbox":
        await self._stack.__aenter__()
        self.clients["agricredit"] = await self._stack.enter_async_context(Client(agricredit_server_params()))
        self.clients["filesystem"] = await self._stack.enter_async_context(Client(fs_server_params()))
        for name, c in self.clients.items():
            tools = (await c.list_tools()).tools
            self.tool_catalogue[name] = [{"name": t.name, "description": (t.description or "").split("\n")[0][:160],
                                          "read_only": bool(t.annotations and t.annotations.read_only_hint)} for t in tools]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.__aexit__(*exc)

    async def call(self, server: str, tool: str, args: dict, *, node: str, max_attempts: int = 1) -> ToolOutcome:
        s = get_settings()
        attempt = 0
        out = ToolOutcome(ok=False, error="not called")
        while attempt < max_attempts:
            attempt += 1
            t0 = time.perf_counter()
            try:
                r = await self.clients[server].call_tool(tool, args)
                if r.is_error:
                    out = ToolOutcome(ok=False, error=" ".join(getattr(c, "text", "") for c in r.content), attempts=attempt)
                else:
                    data = r.structured_content
                    if data is None:
                        data = "\n".join(getattr(c, "text", "") for c in r.content)
                    elif server == "filesystem" and isinstance(data, dict) and set(data) == {"content"}:
                        data = data["content"]
                    out = ToolOutcome(ok=True, data=data, attempts=attempt)
            except Exception as e:  # transport-level failure
                out = ToolOutcome(ok=False, error=f"UNAVAILABLE: transport error {type(e).__name__}: {e}", attempts=attempt)
            log_tool_call(run_id=self.run_id, case_id=self.case_id, agent_node=node, server=server, tool_name=tool,
                          tool_input=args, output=out.data, latency_ms=(time.perf_counter() - t0) * 1000,
                          success=out.ok, model_version=s.model_version, error=out.error, attempt=attempt)
            if out.ok or out.code != "UNAVAILABLE":
                break
        return out

    async def read_policy(self, node: str) -> ToolOutcome:
        path = str(Path(get_settings().fs_mcp_root).resolve() / "policy" / "agent_policy.json")
        r = await self.call("filesystem", "read_text_file", {"path": path}, node=node)
        if r.ok:
            try:
                r.data = json.loads(r.data if isinstance(r.data, str) else json.dumps(r.data))
            except json.JSONDecodeError as e:
                return ToolOutcome(ok=False, error=f"REFUSED: policy file is not valid JSON ({e})")
        return r

    async def write_audit_export(self, name: str, payload: dict, node: str) -> ToolOutcome:
        folder = Path(get_settings().fs_mcp_root).resolve() / "audit_exports"
        await self.call("filesystem", "create_directory", {"path": str(folder)}, node=node)
        path = str(folder / name)
        return await self.call("filesystem", "write_file",
                               {"path": path, "content": json.dumps(payload, indent=2, default=str)}, node=node)


CURRENT: contextvars.ContextVar[Toolbox] = contextvars.ContextVar("agricredit_toolbox")
