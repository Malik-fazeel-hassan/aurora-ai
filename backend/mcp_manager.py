"""
Aurora MCP connector manager.

Supports Model Context Protocol servers over:
- stdio (local command)
- sse (HTTP+SSE)
- streamable_http

Tools are exposed to Agent / Orchestration as:
  mcp__<server_id>__<tool_name>
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MCP_FILE = DATA_DIR / "mcp_servers.json"

# Popular / useful presets (user still pastes command/url + enables)
def _catalog_presets() -> list[dict[str, Any]]:
    try:
        from mcp_catalog import catalog_public, catalog_by_id
        # keep full records available for add_from_preset
        return catalog_public()
    except Exception:
        return []


def _catalog_full() -> dict[str, dict[str, Any]]:
    try:
        from mcp_catalog import catalog_by_id
        return catalog_by_id()
    except Exception:
        return {}


# Back-compat name used across codebase
MCP_PRESETS: list[dict[str, Any]] = _catalog_presets()


def _default_store() -> dict:
    return {"servers": [], "updated_at": time.time()}


def load_store() -> dict:
    if MCP_FILE.exists():
        try:
            data = json.loads(MCP_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("servers"), list):
                return data
        except Exception:
            pass
    return _default_store()


def save_store(store: dict) -> dict:
    store = dict(store)
    store["updated_at"] = time.time()
    MCP_FILE.write_text(json.dumps(store, indent=2), encoding="utf-8")
    return store


def _safe_id(raw: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (raw or "").strip())
    s = s.strip("_-")[:48]
    return s or f"mcp_{uuid.uuid4().hex[:8]}"


def tool_qualified_name(server_id: str, tool_name: str) -> str:
    return f"mcp__{_safe_id(server_id)}__{tool_name}"


def parse_qualified_name(name: str) -> Optional[tuple[str, str]]:
    if not name or not name.startswith("mcp__"):
        return None
    parts = name.split("__", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


@dataclass
class LiveConnection:
    server_id: str
    transport: str
    stack: AsyncExitStack
    session: Any
    tools: list[dict[str, Any]] = field(default_factory=list)
    connected_at: float = field(default_factory=time.time)
    error: str = ""


class MCPManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._live: dict[str, LiveConnection] = {}

    def list_presets(self) -> list[dict[str, Any]]:
        # refresh from catalog each call
        try:
            from mcp_catalog import catalog_public
            return catalog_public()
        except Exception:
            return MCP_PRESETS

    def list_servers(self) -> list[dict[str, Any]]:
        store = load_store()
        out = []
        for s in store.get("servers") or []:
            sid = s.get("id")
            live = self._live.get(sid)
            item = {
                **s,
                "connected": bool(live and not live.error),
                "tool_count": len(live.tools) if live else 0,
                "tools": [t.get("name") for t in (live.tools if live else [])][:40],
                "live_error": live.error if live else "",
                "connected_at": live.connected_at if live else None,
            }
            # never echo secrets fully
            env = item.get("env") or {}
            if isinstance(env, dict):
                item["env"] = {k: ("••••" if v else "") for k, v in env.items()}
            headers = item.get("headers") or {}
            if isinstance(headers, dict):
                item["headers"] = {k: ("••••" if v else "") for k, v in headers.items()}
            out.append(item)
        return out

    def get_server(self, server_id: str) -> Optional[dict]:
        store = load_store()
        for s in store.get("servers") or []:
            if s.get("id") == server_id:
                return s
        return None

    def upsert_server(self, body: dict[str, Any]) -> dict:
        store = load_store()
        servers = list(store.get("servers") or [])
        sid = _safe_id(body.get("id") or body.get("name") or f"srv_{uuid.uuid4().hex[:6]}")
        existing_idx = next((i for i, s in enumerate(servers) if s.get("id") == sid), None)

        transport = (body.get("transport") or "stdio").lower().strip()
        if transport not in ("stdio", "sse", "streamable_http"):
            raise ValueError("transport must be stdio | sse | streamable_http")

        # merge env/headers with existing so blank masked values don't wipe secrets
        prev = servers[existing_idx] if existing_idx is not None else {}
        env = dict(prev.get("env") or {})
        for k, v in (body.get("env") or {}).items():
            if v and v != "••••":
                env[k] = v
            elif k not in env:
                env[k] = v or ""
        headers = dict(prev.get("headers") or {})
        for k, v in (body.get("headers") or {}).items():
            if v and v != "••••":
                headers[k] = v
            elif k not in headers:
                headers[k] = v or ""

        rec = {
            "id": sid,
            "name": body.get("name") or sid,
            "enabled": bool(body.get("enabled", True)),
            "transport": transport,
            "command": body.get("command") or prev.get("command") or "",
            "args": body.get("args") if body.get("args") is not None else (prev.get("args") or []),
            "cwd": body.get("cwd") if body.get("cwd") is not None else (prev.get("cwd") or ""),
            "url": body.get("url") if body.get("url") is not None else (prev.get("url") or ""),
            "env": env,
            "headers": headers,
            "description": body.get("description") or prev.get("description") or "",
            "auto_connect": bool(body.get("auto_connect", prev.get("auto_connect", False))),
        }
        if existing_idx is None:
            servers.append(rec)
        else:
            servers[existing_idx] = rec
        store["servers"] = servers
        save_store(store)
        return rec

    def delete_server(self, server_id: str) -> bool:
        store = load_store()
        before = len(store.get("servers") or [])
        store["servers"] = [s for s in (store.get("servers") or []) if s.get("id") != server_id]
        save_store(store)
        return len(store["servers"]) < before

    def add_from_preset(self, preset_id: str) -> dict:
        full = _catalog_full()
        preset = full.get(preset_id) or next((p for p in self.list_presets() if p["id"] == preset_id), None)
        if not preset:
            raise ValueError(f"unknown preset: {preset_id}")
        body = {
            "id": preset.get("id"),
            "name": preset.get("name"),
            "description": preset.get("description"),
            "transport": preset.get("transport") or "stdio",
            "command": preset.get("command") or "",
            "args": list(preset.get("args") or []),
            "cwd": preset.get("cwd") or "",
            "url": preset.get("url") or "",
            "env": dict(preset.get("env") or {}),
            "headers": dict(preset.get("headers") or {}),
            "enabled": True,
            "auto_connect": bool(preset.get("auto_connect", False)),
        }
        # unique id if already exists
        if self.get_server(body["id"]):
            body["id"] = f"{body['id']}_{uuid.uuid4().hex[:4]}"
        return self.upsert_server(body)

    def add_free_bundle(self, max_count: int = 50, priorities: list[int] | None = None) -> dict:
        """Install curated free local presets (idempotent by id)."""
        full = list(_catalog_full().values())
        # sort by priority then name
        full.sort(key=lambda x: (int(x.get("priority") or 9), x.get("name") or ""))
        if priorities:
            full = [x for x in full if int(x.get("priority") or 9) in priorities]
        added = []
        skipped = []
        for preset in full:
            if len(added) + len(self.list_servers()) >= max_count and False:
                pass
            if len(added) >= max_count:
                break
            # skip experimental markitdown awkward entry
            if preset.get("experimental"):
                skipped.append({"id": preset["id"], "reason": "experimental"})
                continue
            if self.get_server(preset["id"]):
                skipped.append({"id": preset["id"], "reason": "exists"})
                continue
            try:
                rec = self.add_from_preset(preset["id"])
                added.append(rec["id"])
            except Exception as e:
                skipped.append({"id": preset["id"], "reason": str(e)})
            if len(added) >= max_count:
                break
        return {"added": added, "skipped": skipped, "added_count": len(added)}

    async def connect(self, server_id: str) -> dict[str, Any]:
        async with self._lock:
            cfg = self.get_server(server_id)
            if not cfg:
                raise ValueError("server not found")
            if not cfg.get("enabled", True):
                raise ValueError("server disabled")
            # reconnect cleanly
            await self._disconnect_unlocked(server_id)
            live = await self._open_connection(cfg)
            self._live[server_id] = live
            return {
                "id": server_id,
                "connected": not bool(live.error),
                "tool_count": len(live.tools),
                "tools": [t.get("name") for t in live.tools],
                "error": live.error,
            }

    async def disconnect(self, server_id: str) -> dict[str, Any]:
        async with self._lock:
            await self._disconnect_unlocked(server_id)
            return {"id": server_id, "connected": False}

    async def disconnect_all(self) -> None:
        async with self._lock:
            for sid in list(self._live.keys()):
                await self._disconnect_unlocked(sid)

    async def _disconnect_unlocked(self, server_id: str) -> None:
        live = self._live.pop(server_id, None)
        if not live:
            return
        try:
            await live.stack.aclose()
        except Exception:
            pass

    async def _open_connection(self, cfg: dict) -> LiveConnection:
        sid = cfg["id"]
        transport = cfg.get("transport") or "stdio"
        stack = AsyncExitStack()
        await stack.__aenter__()
        session = None
        err = ""
        tools: list[dict[str, Any]] = []
        try:
            if transport == "stdio":
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client

                command = (cfg.get("command") or "").strip()
                if not command:
                    raise ValueError("stdio transport requires command")
                args = cfg.get("args") or []
                if isinstance(args, str):
                    args = [a for a in args.split(" ") if a]
                env = os.environ.copy()
                for k, v in (cfg.get("env") or {}).items():
                    if v is not None and str(v) != "":
                        env[str(k)] = str(v)
                params = StdioServerParameters(
                    command=command,
                    args=list(args),
                    env=env,
                    cwd=(cfg.get("cwd") or None) or None,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
            elif transport == "sse":
                from mcp import ClientSession
                from mcp.client.sse import sse_client

                url = (cfg.get("url") or "").strip()
                if not url:
                    raise ValueError("sse transport requires url")
                headers = {k: str(v) for k, v in (cfg.get("headers") or {}).items() if v}
                read, write = await stack.enter_async_context(sse_client(url, headers=headers or None))
                session = await stack.enter_async_context(ClientSession(read, write))
            elif transport == "streamable_http":
                from mcp import ClientSession
                from mcp.client.streamable_http import streamablehttp_client

                url = (cfg.get("url") or "").strip()
                if not url:
                    raise ValueError("streamable_http transport requires url")
                headers = {k: str(v) for k, v in (cfg.get("headers") or {}).items() if v}
                # streamablehttp_client returns (read, write, get_session_id)
                triple = await stack.enter_async_context(
                    streamablehttp_client(url, headers=headers or None)
                )
                read, write = triple[0], triple[1]
                session = await stack.enter_async_context(ClientSession(read, write))
            else:
                raise ValueError(f"unsupported transport: {transport}")

            await session.initialize()
            listed = await session.list_tools()
            for t in listed.tools or []:
                schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {}
                if hasattr(schema, "model_dump"):
                    schema = schema.model_dump()
                elif hasattr(schema, "dict"):
                    schema = schema.dict()
                tools.append(
                    {
                        "name": t.name,
                        "description": getattr(t, "description", "") or "",
                        "input_schema": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
                    }
                )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            try:
                await stack.aclose()
            except Exception:
                pass
            stack = AsyncExitStack()
            session = None
            tools = []

        return LiveConnection(
            server_id=sid,
            transport=transport,
            stack=stack,
            session=session,
            tools=tools,
            error=err,
        )

    async def connect_enabled(self) -> list[dict]:
        results = []
        for s in load_store().get("servers") or []:
            if s.get("enabled") and s.get("auto_connect"):
                try:
                    results.append(await self.connect(s["id"]))
                except Exception as e:
                    results.append({"id": s["id"], "connected": False, "error": str(e)})
        return results

    def connected_tool_specs(self) -> list[dict[str, Any]]:
        """OpenAI-style tool specs for all connected MCP tools."""
        specs = []
        for sid, live in self._live.items():
            if live.error or not live.session:
                continue
            for t in live.tools:
                qname = tool_qualified_name(sid, t["name"])
                schema = t.get("input_schema") or {"type": "object", "properties": {}}
                # Ensure object schema
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                if "type" not in schema:
                    schema = {**schema, "type": "object"}
                desc = t.get("description") or f"MCP tool {t['name']} from {sid}"
                specs.append(
                    {
                        "type": "function",
                        "function": {
                            "name": qname,
                            "description": f"[MCP:{sid}] {desc}",
                            "parameters": schema,
                        },
                    }
                )
        return specs

    async def call_tool(self, qualified_or_server: str, tool_name: Optional[str] = None, arguments: Any = None) -> dict[str, Any]:
        """
        Call MCP tool.
        - call_tool('mcp__demo__echo', arguments={...})
        - call_tool('demo', 'echo', {...})
        """
        if tool_name is None:
            parsed = parse_qualified_name(qualified_or_server)
            if not parsed:
                return {"ok": False, "error": "invalid MCP tool name"}
            server_id, tool_name = parsed
        else:
            server_id = qualified_or_server

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except Exception:
                arguments = {"input": arguments}
        if arguments is None:
            arguments = {}

        live = self._live.get(server_id)
        if not live or not live.session or live.error:
            # try connect on demand
            try:
                await self.connect(server_id)
                live = self._live.get(server_id)
            except Exception as e:
                return {"ok": False, "error": f"not connected: {e}"}
        if not live or not live.session:
            return {"ok": False, "error": f"MCP server not connected: {server_id}"}

        try:
            result = await live.session.call_tool(tool_name, arguments=arguments)
            # normalize content
            content_out = []
            for block in getattr(result, "content", None) or []:
                btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                if btype == "text" or hasattr(block, "text"):
                    content_out.append({"type": "text", "text": getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else str(block))})
                else:
                    content_out.append({"type": btype or "unknown", "data": str(block)[:2000]})
            is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
            text = "\n".join(c.get("text", "") for c in content_out if c.get("type") == "text")
            return {
                "ok": not is_error,
                "server": server_id,
                "tool": tool_name,
                "content": content_out,
                "text": text[:12000],
                "is_error": is_error,
            }
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "server": server_id, "tool": tool_name}

    def status(self) -> dict[str, Any]:
        servers = self.list_servers()
        return {
            "configured": len(servers),
            "connected": sum(1 for s in servers if s.get("connected")),
            "tool_count": sum(s.get("tool_count") or 0 for s in servers),
            "presets": len(self.list_presets()),
            "servers": servers,
        }


# singleton
mcp_manager = MCPManager()
