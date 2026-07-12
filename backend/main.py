"""
Aurora — Claude-inspired AI assistant
- Multi-model auto routing + credit-aware failover
- Agent mode with tools and multi-step loops
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from router import (
    build_routes,
    detect_task,
    is_retriable_error,
    route_summary,
)
from tools import (
    AGENT_SYSTEM_EXTRA,
    TOOL_FALLBACK_INSTRUCTIONS,
    TOOL_SPECS,
    get_all_tool_specs,
    run_tool,
    run_tool_async,
)
from orchestration import Orchestrator, list_pipelines
from mcp_manager import mcp_manager
from security import (
    CORS_ORIGINS,
    SecurityHeadersMiddleware,
    audit,
    is_blocked_url,
    redact_secrets,
    sanitize_model_id,
    security_status,
    validate_chat_payload,
)

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR.parent / "static"
DATA_DIR = APP_DIR.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SYSTEM = (
    "You are Aurora, a helpful, honest, and thoughtful AI assistant inspired by "
    "Claude. You write clear, well-structured answers. Prefer concise prose when "
    "possible, use markdown for structure, and put substantial code or documents "
    "in fenced code blocks. When the user asks for a self-contained artifact "
    "(HTML page, component, document, diagram), wrap the full deliverable in a "
    "markdown code fence with an appropriate language tag so the UI can open it "
    "in the Artifacts panel. Be warm, careful, and precise."
)

DEFAULT_SETTINGS = {
    "api_base": "https://openrouter.ai/api/v1",
    "api_key": "",
    "default_model": "anthropic/claude-sonnet-4.6",
    "system_prompt": DEFAULT_SYSTEM,
    "temperature": 0.7,
    "max_tokens": 1024,
    "active_profile": "openrouter",
    "profiles": {},
    "auto_route": True,
    "prefer_free_on_fail": True,
    "agent_max_steps": 8,
    "infinite_token_mode": True,
    "infinite_token_threshold": 40000,
    "infinite_token_strategy": "summarize",
}

PROVIDERS = {
    "local": {
        "name": "Local AI Models (Offline)",
        "base": "local",
        "models": [
            {"id": "local/nano-banana-chat", "label": "Nano Banana Chat (Local LLM)"},
            {"id": "local/nano-banana-image", "label": "Nano Banana Image (Local T2I)"},
            {"id": "local/seadance-video", "label": "SeaDance Video (Local T2V)"},
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "base": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "auto", "label": "⚡ Auto (best + failover)"},
            {"id": "anthropic/claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
            {"id": "anthropic/claude-opus-4.6", "label": "Claude Opus 4.6"},
            {"id": "anthropic/claude-haiku-4.5", "label": "Claude Haiku 4.5"},
            {"id": "anthropic/claude-3-haiku", "label": "Claude 3 Haiku"},
            {"id": "openai/gpt-4o", "label": "GPT-4o"},
            {"id": "openai/gpt-4o-mini", "label": "GPT-4o Mini"},
            {"id": "google/gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
            {"id": "google/gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
            {"id": "deepseek/deepseek-r1", "label": "DeepSeek R1"},
            {"id": "deepseek/deepseek-chat", "label": "DeepSeek Chat"},
            {"id": "qwen/qwen3-235b-a22b", "label": "Qwen3 235B"},
            {"id": "meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B"},
            {"id": "openrouter/free", "label": "Free router"},
            {"id": "qwen/qwen3-coder:free", "label": "Qwen3 Coder free"},
            {"id": "qwen/qwen3-next-80b-a3b-instruct:free", "label": "Qwen3 Next free"},
        ],
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "base": "https://integrate.api.nvidia.com/v1",
        "models": [
            {"id": "minimaxai/minimax-m3", "label": "MiniMax M3"},
            {"id": "meta/llama-3.1-70b-instruct", "label": "Llama 3.1 70B"},
            {"id": "meta/llama-3.1-8b-instruct", "label": "Llama 3.1 8B"},
            {"id": "meta/llama-3.3-70b-instruct", "label": "Llama 3.3 70B"},
            {"id": "qwen/qwen2.5-72b-instruct", "label": "Qwen2.5 72B"},
            {"id": "qwen/qwen2.5-coder-32b-instruct", "label": "Qwen2.5 Coder 32B"},
        ],
    },
    "openai": {
        "name": "OpenAI",
        "base": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-4o", "label": "GPT-4o"},
            {"id": "gpt-4o-mini", "label": "GPT-4o Mini"},
        ],
    },
    "groq": {
        "name": "Groq",
        "base": "https://api.groq.com/openai/v1",
        "models": [
            {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B"},
            {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B Instant"},
        ],
    },
    "custom": {
        "name": "Custom OpenAI-compatible",
        "base": "http://localhost:11434/v1",
        "models": [{"id": "llama3.2", "label": "Local / Custom model"}],
    },
}

PROFILE_META = {
    "openrouter": {"label": "OpenRouter (Claude & more)", "provider": "openrouter"},
    "nvidia": {"label": "NVIDIA NIM (general)", "provider": "nvidia"},
    "nvidia_qwen": {"label": "NVIDIA NIM (Qwen key)", "provider": "nvidia"},
    "nvidia_minimax": {"label": "NVIDIA NIM (MiniMax key)", "provider": "nvidia"},
    "nvidia_cosmos": {"label": "NVIDIA NIM (Cosmos key)", "provider": "nvidia"},
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            merged = {**DEFAULT_SETTINGS, **data}
            if "profiles" not in merged or merged["profiles"] is None:
                merged["profiles"] = {}
            return merged
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> dict:
    merged = {**DEFAULT_SETTINGS, **settings}
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) > 12:
        return key[:6] + "…" + key[-4:]
    return "••••••••"


def resolve_manual(settings: dict, model: Optional[str] = None, profile: Optional[str] = None):
    profiles = settings.get("profiles") or {}
    active = profile or settings.get("active_profile") or ""
    prof = profiles.get(active) if active else None
    if prof:
        api_base = (prof.get("api_base") or settings.get("api_base") or "").rstrip("/")
        api_key = prof.get("api_key") or settings.get("api_key") or ""
        default_model = prof.get("default_model") or settings.get("default_model")
    else:
        api_base = (settings.get("api_base") or "").rstrip("/")
        api_key = settings.get("api_key") or ""
        default_model = settings.get("default_model")
    api_key = api_key or os.environ.get("AURORA_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    return api_base, api_key, model or default_model or "anthropic/claude-sonnet-4.6"


app = FastAPI(
    title="Aurora",
    version="2.6.0",
    description="Claude-inspired agentic assistant with debate + modular orchestration",
)
@app.on_event("startup")
async def _startup_mcp():
    try:
        await mcp_manager.connect_enabled()
    except Exception:
        pass


# DevSecOps middleware (headers, rate limit, body size, optional app token)
app.add_middleware(SecurityHeadersMiddleware)

_cors_origins = CORS_ORIGINS
_cors_creds = False if _cors_origins == ["*"] else True
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_creds,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*", "X-Aurora-Token", "X-API-Key", "Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)


class ChatMessage(BaseModel):
    role: str
    content: Any = ""
    attachments: Optional[list[dict]] = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system: Optional[str] = None
    stream: bool = True
    profile: Optional[str] = None
    agent_mode: bool = False
    debate_mode: bool = False
    debate_panel: int = 3
    orchestrate: bool = False
    pipeline: Optional[str] = None
    auto_route: Optional[bool] = None


class SettingsUpdate(BaseModel):
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    default_model: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    active_profile: Optional[str] = None
    auto_route: Optional[bool] = None
    prefer_free_on_fail: Optional[bool] = None
    agent_max_steps: Optional[int] = None
    infinite_token_mode: Optional[bool] = None
    infinite_token_threshold: Optional[int] = None
    infinite_token_strategy: Optional[str] = None
    require_tool_approvals: Optional[bool] = None


def sse(obj: dict | str) -> str:
    if isinstance(obj, str):
        return f"data: {obj}\n\n"
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def event_chunk(content: str, model: str = "aurora") -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:10]}",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }


def event_done(model: str = "aurora") -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:10]}",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }


def last_user_text(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            c = m.content
            if isinstance(c, str):
                return c
            return json.dumps(c)
    return ""


def normalize_messages(messages: list[ChatMessage], system: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if m.role not in ("user", "assistant", "system", "tool"):
            continue
        content = m.content if m.content is not None else ""
        
        # Check if the user message has attachments (multimodal context)
        if m.role == "user" and m.attachments:
            has_image = any(a.get("type") == "image" for a in m.attachments)
            if has_image:
                content_blocks = [{"type": "text", "text": str(content)}]
                for a in m.attachments:
                    if a.get("type") == "image":
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": a.get("data")}
                        })
                    elif a.get("type") == "text":
                        content_blocks.append({
                            "type": "text",
                            "text": f"\n\n[Attached File: {a.get('name')}]\n{a.get('text')}"
                        })
                item = {"role": m.role, "content": content_blocks}
            else:
                text_content = str(content)
                for a in m.attachments:
                    if a.get("type") == "text":
                        text_content += f"\n\n[Attached File: {a.get('name')}]\n{a.get('text')}"
                item = {"role": m.role, "content": text_content}
        else:
            item = {"role": m.role, "content": content}
            
        out.append(item)
    return out


async def post_chat(
    api_base: str,
    api_key: str,
    payload: dict,
    stream: bool = False,
    timeout: float = 180.0,
) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
        "HTTP-Referer": "https://aurora.local",
        "X-Title": "Aurora",
    }
    url = f"{api_base.rstrip('/')}/chat/completions"
    client = httpx.AsyncClient(timeout=timeout)
    try:
        if stream:
            # caller manages stream client differently
            pass
        r = await client.post(url, headers=headers, json=payload)
        return r
    finally:
        if not stream:
            await client.aclose()


async def try_completion(
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    tools: Optional[list] = None,
    tool_choice: Any = None,
) -> tuple[Optional[dict], Optional[int], str]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "HTTP-Referer": "https://aurora.local",
        "X-Title": "Aurora",
    }
    url = f"{api_base.rstrip('/')}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code >= 400:
                return None, r.status_code, r.text
            return r.json(), r.status_code, ""
    except Exception as e:
        return None, 502, str(e)


async def stream_completion_lines(
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[tuple[str, Optional[int], str], None]:
    """Yield ('delta', None, text) or ('error', status, body) or ('done', None, '')."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "HTTP-Referer": "https://aurora.local",
        "X-Title": "Aurora",
    }
    url = f"{api_base.rstrip('/')}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", errors="replace")
                    yield ("error", r.status_code, body)
                    return
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                    else:
                        data = line.strip()
                    if data == "[DONE]":
                        yield ("done", None, "")
                        return
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    if obj.get("error"):
                        yield ("error", 500, json.dumps(obj.get("error")))
                        return
                    delta = (
                        obj.get("choices", [{}])[0].get("delta", {}).get("content")
                        or obj.get("choices", [{}])[0].get("message", {}).get("content")
                        or ""
                    )
                    if delta:
                        yield ("delta", None, delta)
                yield ("done", None, "")
    except Exception as e:
        yield ("error", 502, str(e))


def extract_tool_calls_from_message(message: dict) -> list[dict]:
    calls = message.get("tool_calls") or []
    if calls:
        return calls
    # fallback XML protocol
    content = message.get("content") or ""
    if not isinstance(content, str):
        return []
    found = []
    for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, flags=re.S):
        try:
            obj = json.loads(m.group(1))
            name = obj.get("name")
            args = obj.get("arguments", {})
            found.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                    },
                }
            )
        except Exception:
            continue
    return found


def strip_tool_call_tags(content: str) -> str:
    if not content:
        return content
    return re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", content).strip()


@app.get("/api/health")
async def health():
    s = load_settings()
    routes = build_routes(s, task="chat")
    return {
        "ok": True,
        "name": "Aurora",
        "version": "2.6.0",
        "time": time.time(),
        "api_key_set": bool(routes),
        "active_profile": s.get("active_profile"),
        "auto_route": s.get("auto_route", True),
        "routes_available": len(routes),
        "agent_tools": [t["function"]["name"] for t in TOOL_SPECS],
        "mcp_tools": [t["function"]["name"] for t in mcp_manager.connected_tool_specs()][:30],
        "features": ["auto_route", "agent", "debate", "orchestration", "mcp", "failover", "devsecops"],
        "pipelines": [p["id"] for p in list_pipelines()],
        "mcp": mcp_manager.status(),
        "security": security_status(),
    }


@app.get("/api/security/status")
async def api_security_status():
    return security_status()


@app.get("/api/providers")
async def providers():
    return PROVIDERS


@app.get("/api/routes")
async def api_routes(task: str = "chat", prefer_free: bool = False):
    s = load_settings()
    routes = build_routes(s, task=task, prefer_free=prefer_free)
    return {"task": task, "count": len(routes), "routes": route_summary(routes, 20)}


@app.get("/api/pipelines")
async def api_pipelines():
    return {"pipelines": list_pipelines()}


class MCPServerBody(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    enabled: Optional[bool] = True
    transport: Optional[str] = "stdio"
    command: Optional[str] = None
    args: Optional[list[str] | str] = None
    cwd: Optional[str] = None
    url: Optional[str] = None
    env: Optional[dict[str, str]] = None
    headers: Optional[dict[str, str]] = None
    description: Optional[str] = None
    auto_connect: Optional[bool] = False



@app.get("/api/mcp/catalog")
async def mcp_catalog():
    try:
        from mcp_catalog import catalog_public
        items = catalog_public()
    except Exception as e:
        raise HTTPException(500, str(e))
    cats: dict[str, int] = {}
    for it in items:
        c = it.get("category") or "Other"
        cats[c] = cats.get(c, 0) + 1
    return {"count": len(items), "categories": cats, "items": items}


@app.post("/api/mcp/install-free")
async def mcp_install_free(
    max_count: int = Query(50, ge=1, le=50),
    connect_core: bool = Query(True),
):
    """Install curated free open-source MCP presets (no paid APIs)."""
    result = mcp_manager.add_free_bundle(max_count=max_count)
    connected = []
    if connect_core:
        for s in mcp_manager.list_servers():
            if s.get("auto_connect") or str(s.get("id", "")).startswith("aurora_"):
                try:
                    connected.append(await mcp_manager.connect(s["id"]))
                except Exception as e:
                    connected.append({"id": s["id"], "connected": False, "error": str(e)})
    return {"ok": True, **result, "connected": connected, "configured_total": len(mcp_manager.list_servers())}

@app.get("/api/mcp/presets")
async def mcp_presets():
    return {"presets": mcp_manager.list_presets()}


@app.get("/api/mcp/servers")
async def mcp_servers():
    return {"servers": mcp_manager.list_servers(), "status": mcp_manager.status()}


@app.post("/api/mcp/servers")
async def mcp_upsert(body: MCPServerBody):
    data = body.model_dump(exclude_none=True)
    if isinstance(data.get("args"), str):
        # allow JSON array or shell-like split
        raw = data["args"].strip()
        if raw.startswith("["):
            try:
                data["args"] = json.loads(raw)
            except Exception:
                data["args"] = [a for a in raw.split(" ") if a]
        else:
            data["args"] = [a for a in raw.split(" ") if a]
    try:
        rec = mcp_manager.upsert_server(data)
        return {"ok": True, "server": rec}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/mcp/presets/{preset_id}")
async def mcp_add_preset(preset_id: str):
    try:
        rec = mcp_manager.add_from_preset(preset_id)
        return {"ok": True, "server": rec}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/mcp/servers/{server_id}")
async def mcp_delete(server_id: str):
    # disconnect first
    try:
        await mcp_manager.disconnect(server_id)
    except Exception:
        pass
    ok = mcp_manager.delete_server(server_id)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.post("/api/mcp/servers/{server_id}/connect")
async def mcp_connect(server_id: str):
    try:
        return await mcp_manager.connect(server_id)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/mcp/servers/{server_id}/disconnect")
async def mcp_disconnect(server_id: str):
    return await mcp_manager.disconnect(server_id)


@app.post("/api/mcp/connect-enabled")
async def mcp_connect_enabled():
    return {"results": await mcp_manager.connect_enabled()}


class MCPCallBody(BaseModel):
    server_id: Optional[str] = None
    tool: Optional[str] = None
    name: Optional[str] = None  # qualified mcp__x__y
    arguments: Optional[dict[str, Any]] = None


@app.post("/api/mcp/call")
async def mcp_call(body: MCPCallBody):
    if body.name:
        return await mcp_manager.call_tool(body.name, None, body.arguments or {})
    if not body.server_id or not body.tool:
        raise HTTPException(400, "provide name or server_id+tool")
    return await mcp_manager.call_tool(body.server_id, body.tool, body.arguments or {})


@app.get("/api/mcp/tools")
async def mcp_tools():
    return {"tools": mcp_manager.connected_tool_specs()}



@app.get("/api/profiles")
async def list_profiles():
    s = load_settings()
    profiles = s.get("profiles") or {}
    out = []
    for pid, meta in PROFILE_META.items():
        p = profiles.get(pid) or {}
        out.append(
            {
                "id": pid,
                "label": meta["label"],
                "provider": meta["provider"],
                "configured": bool(p.get("api_key")),
                "api_base": p.get("api_base"),
                "default_model": p.get("default_model"),
                "api_key_masked": mask_key(p.get("api_key") or ""),
            }
        )
    return {"active_profile": s.get("active_profile"), "profiles": out}


@app.get("/api/settings")
async def get_settings():
    s = load_settings()
    routes = build_routes(s, "chat")
    key = ""
    if routes:
        key = routes[0].api_key
    elif s.get("api_key"):
        key = s["api_key"]
    return {
        "api_base": s.get("api_base"),
        "api_key_set": bool(key),
        "api_key_masked": mask_key(key),
        "default_model": s.get("default_model"),
        "system_prompt": s.get("system_prompt"),
        "temperature": s.get("temperature"),
        "max_tokens": s.get("max_tokens"),
        "active_profile": s.get("active_profile"),
        "auto_route": s.get("auto_route", True),
        "prefer_free_on_fail": s.get("prefer_free_on_fail", True),
        "agent_max_steps": s.get("agent_max_steps", 8),
        "profiles_configured": list((s.get("profiles") or {}).keys()),
        "routes_available": len(routes),
        "infinite_token_mode": s.get("infinite_token_mode", True),
        "infinite_token_threshold": s.get("infinite_token_threshold", 40000),
        "infinite_token_strategy": s.get("infinite_token_strategy", "summarize"),
    }


@app.post("/api/settings")
async def update_settings(body: SettingsUpdate):
    current = load_settings()
    updates = body.model_dump(exclude_none=True)
    if body.api_key is not None:
        updates["api_key"] = body.api_key

    if body.active_profile is not None:
        pid = body.active_profile
        profiles = current.get("profiles") or {}
        if pid in profiles:
            p = profiles[pid]
            updates.setdefault("api_base", p.get("api_base"))
            updates.setdefault("api_key", p.get("api_key"))
            updates.setdefault("default_model", p.get("default_model"))

    active = updates.get("active_profile", current.get("active_profile"))
    if active:
        profiles = dict(updates.get("profiles") or current.get("profiles") or {})
        p = dict(profiles.get(active) or {})
        if "api_base" in updates:
            p["api_base"] = updates["api_base"]
        if "api_key" in updates:
            p["api_key"] = updates["api_key"]
        if "default_model" in updates:
            p["default_model"] = updates["default_model"]
        if p:
            profiles[active] = p
            updates["profiles"] = profiles

    current.update(updates)
    save_settings(current)
    return await get_settings()


# Global tool approval queue
pending_approvals: dict[str, asyncio.Event] = {}
approval_results: dict[str, bool] = {}


@app.post("/api/approve/{approval_id}")
async def approve_tool(approval_id: str, approved: bool = True):
    if approval_id in pending_approvals:
        approval_results[approval_id] = approved
        pending_approvals[approval_id].set()
        return {"status": "ok", "approval_id": approval_id, "approved": approved}
    raise HTTPException(status_code=404, detail="Approval request not found or already processed.")


async def compress_conversation(messages: list[dict], threshold: int, strategy: str, routes: list, settings: dict) -> tuple[list[dict], bool, int, int, str]:
    """
    Compresses conversation history if total character length exceeds threshold.
    Returns (compressed_messages, did_compress, original_len, compressed_len, summary_text)
    """
    total_len = sum(len(str(m.get("content") or "")) for m in messages)
    if total_len <= threshold or len(messages) <= 10:
        return messages, False, total_len, total_len, ""

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    
    if len(non_system) <= 6:
        return messages, False, total_len, total_len, ""
        
    first_user = non_system[0]
    keep_n = 8
    last_turns = non_system[-keep_n:]
    middle_turns = non_system[1:-keep_n]
    
    if not middle_turns:
        return messages, False, total_len, total_len, ""
        
    middle_len = sum(len(str(m.get("content") or "")) for m in middle_turns)
    if middle_len < 1000:
        return messages, False, total_len, total_len, ""

    summary = ""
    compressed_strategy = strategy
    
    if strategy == "summarize" and routes:
        transcript_lines = []
        for m in middle_turns:
            role = "User" if m.get("role") == "user" else "Assistant"
            content = str(m.get("content") or "")
            transcript_lines.append(f"{role}: {content[:1000]}")
            
        transcript = "\n".join(transcript_lines)
        sum_prompt = (
            "Summarize the following conversation history between User and Assistant concisely. "
            "Highlight key facts, preferences, code developed, and decisions made. Keep the summary "
            "under 400 words. Do not repeat instructions. Return ONLY the summary.\n\n"
            f"Transcript to summarize:\n{transcript}"
        )
        
        sum_messages = [{"role": "user", "content": sum_prompt}]
        try:
            content, route, err = await complete_with_failover(routes, sum_messages, 0.3, 1024)
            if content and content.strip():
                summary = content.strip()
        except Exception:
            pass
            
    if not summary:
        compressed_strategy = "prune"
        summary = f"Pruned {len(middle_turns)} turns of older discussion to save token space."

    compressed_messages = []
    compressed_messages.extend(system_msgs)
    compressed_messages.append(first_user)
    marker_content = (
        f"[System Note: The preceding conversation history has been auto-compressed using '{compressed_strategy}' "
        f"to fit in the model context. Summary of past discussion:\n{summary}]"
    )
    compressed_messages.append({"role": "system", "content": marker_content})
    compressed_messages.extend(last_turns)
    
    new_len = sum(len(str(m.get("content") or "")) for m in compressed_messages)
    return compressed_messages, True, total_len, new_len, summary


@app.post("/api/chat")
async def chat(req: ChatRequest):
    # DevSecOps input guards
    err = validate_chat_payload(req.messages, req.max_tokens)
    if err:
        audit("chat_rejected", reason=err)
        raise HTTPException(status_code=400, detail=err)
    if req.model:
        cleaned = sanitize_model_id(req.model)
        if cleaned is None:
            raise HTTPException(status_code=400, detail="invalid model id")
        req.model = cleaned
    settings = load_settings()
    if req.profile:
        settings = {**settings, "active_profile": req.profile}

    auto = settings.get("auto_route", True) if req.auto_route is None else req.auto_route
    model_req = (req.model or settings.get("default_model") or "auto").strip()
    if model_req.lower() in ("auto", "router", "best"):
        auto = True
        model_req = "auto"

    temperature = req.temperature if req.temperature is not None else float(settings.get("temperature", 0.7))
    max_tokens = req.max_tokens if req.max_tokens is not None else int(settings.get("max_tokens", 8192))
    system = req.system if req.system is not None else (settings.get("system_prompt") or DEFAULT_SYSTEM)

    user_text = last_user_text(req.messages)
    task = detect_task(user_text, agent_mode=req.agent_mode)

    if req.agent_mode:
        system = system + "\n\n" + AGENT_SYSTEM_EXTRA + "\n\n" + TOOL_FALLBACK_INSTRUCTIONS

    # Intercept Local Offline Models
    if model_req.startswith("local/"):
        import local_models
        if model_req == "local/nano-banana-chat":
            if req.stream:
                async def local_chat_streamer():
                    yield sse({"aurora_event": "route_selected", "profile": "local", "model": model_req, "label": "Nano Banana Chat (Local LLM)", "tier": "local"})
                    async for token in local_models.generate_local_text_stream(normalize_messages(req.messages, system), settings):
                        yield sse(event_chunk(token, model=model_req))
                    yield sse(event_done(model=model_req))
                    yield sse("[DONE]")
                return StreamingResponse(local_chat_streamer(), media_type="text/event-stream")
            else:
                content_list = []
                async for token in local_models.generate_local_text_stream(normalize_messages(req.messages, system), settings):
                    content_list.append(token)
                full_text = "".join(content_list)
                return {
                    "id": f"local-{uuid.uuid4().hex[:8]}",
                    "model": model_req,
                    "choices": [{"message": {"role": "assistant", "content": full_text}}],
                }
        elif model_req == "local/nano-banana-image":
            prompt = last_user_text(req.messages)
            url = local_models.generate_local_image(prompt)
            content = f"Here is your generated image:\n\n![Generated Image]({url})\n\n*(Prompt: {prompt})*"
            if req.stream:
                async def local_image_streamer():
                    yield sse({"aurora_event": "route_selected", "profile": "local", "model": model_req, "label": "Nano Banana Image (Local T2I)", "tier": "local"})
                    for i in range(0, len(content), 15):
                        yield sse(event_chunk(content[i:i+15], model=model_req))
                        await asyncio.sleep(0.005)
                    yield sse(event_done(model=model_req))
                    yield sse("[DONE]")
                return StreamingResponse(local_image_streamer(), media_type="text/event-stream")
            else:
                return {
                    "id": f"local-{uuid.uuid4().hex[:8]}",
                    "model": model_req,
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                }
        elif model_req == "local/seadance-video":
            prompt = last_user_text(req.messages)
            url = local_models.generate_local_video(prompt)
            content = f"Here is your generated video:\n\n<video src=\"{url}\" controls autoplay loop style=\"max-width:100%; width: 500px; border-radius: 12px; border: 1px solid var(--border);\"></video>\n\n*(Prompt: {prompt})*"
            if req.stream:
                async def local_video_streamer():
                    yield sse({"aurora_event": "route_selected", "profile": "local", "model": model_req, "label": "SeaDance Video (Local T2V)", "tier": "local"})
                    for i in range(0, len(content), 15):
                        yield sse(event_chunk(content[i:i+15], model=model_req))
                        await asyncio.sleep(0.005)
                    yield sse(event_done(model=model_req))
                    yield sse("[DONE]")
                return StreamingResponse(local_video_streamer(), media_type="text/event-stream")
            else:
                return {
                    "id": f"local-{uuid.uuid4().hex[:8]}",
                    "model": model_req,
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                }

    # Build route list
    if auto or model_req == "auto":
        routes = build_routes(settings, task=task, prefer_free=False)
        if not routes and settings.get("prefer_free_on_fail", True):
            routes = build_routes(settings, task=task, prefer_free=True)
    else:
        # Manual model: still attach failover chain after primary
        api_base, api_key, model = resolve_manual(settings, model_req, req.profile)
        from router import Route

        primary = []
        if api_key and api_base:
            primary = [
                Route(
                    profile=settings.get("active_profile") or "manual",
                    api_base=api_base,
                    api_key=api_key,
                    model=model,
                    label=model,
                    tier="manual",
                    capabilities=set(),
                )
            ]
        failover = build_routes(settings, task=task, prefer_free=False)
        if settings.get("prefer_free_on_fail", True):
            failover = failover + [r for r in build_routes(settings, task=task, prefer_free=True) if r.tier == "free"]
        seen = set()
        routes = []
        for r in primary + failover:
            k = (r.profile, r.model)
            if k in seen:
                continue
            seen.add(k)
            routes.append(r)

    if not routes:
        if req.stream:
            return StreamingResponse(demo_stream(req.messages), media_type="text/event-stream")
        return {
            "id": f"demo-{uuid.uuid4().hex[:8]}",
            "model": "aurora-demo",
            "choices": [{"message": {"role": "assistant", "content": "No API routes configured."}}],
        }

    # 2. Compress conversation if infinite token mode is enabled
    inf_mode = settings.get("infinite_token_mode", True)
    inf_threshold = settings.get("infinite_token_threshold", 40000)
    inf_strategy = settings.get("infinite_token_strategy", "summarize")
    
    did_compress = False
    original_len = 0
    compressed_len = 0
    summary_text = ""
    
    msg_dicts = [{"role": m.role, "content": m.content} for m in req.messages]
    
    if inf_mode:
        compressed_msgs, did_compress, original_len, compressed_len, summary_text = await compress_conversation(
            msg_dicts, inf_threshold, inf_strategy, routes, settings
        )
        if did_compress:
            req.messages = [ChatMessage(role=m["role"], content=m["content"]) for m in compressed_msgs]

    messages = normalize_messages(req.messages, system)

    # Helper to return StreamingResponse with injected compression event
    def make_stream_response(generator):
        if did_compress:
            async def compression_stream_wrapper():
                yield sse({
                    "aurora_event": "context_compressed",
                    "original_len": original_len,
                    "compressed_len": compressed_len,
                    "strategy": inf_strategy,
                    "summary": summary_text
                })
                async for chunk in generator:
                    yield chunk
            return StreamingResponse(
                compression_stream_wrapper(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            )
        else:
            return StreamingResponse(
                generator,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            )

    # Orchestration (multi-module pipelines)
    if req.orchestrate:
        pipeline_id = (req.pipeline or "auto").strip().lower()
        if req.stream:
            return make_stream_response(
                orchestrate_stream(routes, messages, temperature, max_tokens, settings, task, pipeline_id)
            )
        final_text, used, meta = await orchestrate_run(
            routes, messages, temperature, max_tokens, settings, pipeline_id
        )
        res = {
            "id": f"orch-{uuid.uuid4().hex[:8]}",
            "model": used,
            "choices": [{"message": {"role": "assistant", "content": final_text}}],
            "aurora_orchestration": meta,
        }
        if did_compress:
            res["aurora_context"] = {
                "compressed": True,
                "original_len": original_len,
                "compressed_len": compressed_len,
                "summary": summary_text
            }
        return res

    # Debate
    if req.debate_mode:
        panel_n = max(2, min(int(req.debate_panel or settings.get("debate_panel") or 3), 5))
        if req.stream:
            return make_stream_response(
                debate_stream(routes, messages, temperature, max_tokens, settings, task, panel_n)
            )
        final_text, used, panel = await debate_run(routes, messages, temperature, max_tokens, settings, panel_n)
        res = {
            "id": f"debate-{uuid.uuid4().hex[:8]}",
            "model": used,
            "choices": [{"message": {"role": "assistant", "content": final_text}}],
            "aurora_debate": panel,
        }
        if did_compress:
            res["aurora_context"] = {
                "compressed": True,
                "original_len": original_len,
                "compressed_len": compressed_len,
                "summary": summary_text
            }
        return res

    # Agent
    if req.agent_mode:
        if req.stream:
            return make_stream_response(
                agent_stream(routes, messages, temperature, max_tokens, settings, task)
            )
        final_text, used = await agent_run(routes, messages, temperature, max_tokens, settings)
        res = {
            "id": f"agent-{uuid.uuid4().hex[:8]}",
            "model": used,
            "choices": [{"message": {"role": "assistant", "content": final_text}}],
        }
        if did_compress:
            res["aurora_context"] = {
                "compressed": True,
                "original_len": original_len,
                "compressed_len": compressed_len,
                "summary": summary_text
            }
        return res

    # Normal chat
    if req.stream:
        return make_stream_response(
            chat_stream_with_failover(routes, messages, temperature, max_tokens, task, settings)
        )

    # non-stream failover
    errors = []
    for r in routes:
        data, status, err = await try_completion(
            r.api_base, r.api_key, r.model, messages, temperature, max_tokens
        )
        if data:
            if "model" not in data:
                data["model"] = r.model
            data["aurora_route"] = {"profile": r.profile, "model": r.model, "label": r.label, "task": task}
            if did_compress:
                data["aurora_context"] = {
                    "compressed": True,
                    "original_len": original_len,
                    "compressed_len": compressed_len,
                    "summary": summary_text
                }
            return data
        errors.append(f"{r.label}: HTTP {status} {err[:180]}")
        if not is_retriable_error(status, err):
            continue
    raise HTTPException(status_code=502, detail={"message": "All routes failed", "errors": errors[:8]})


async def chat_stream_with_failover(
    routes,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    task: str,
    settings: dict,
) -> AsyncGenerator[str, None]:
    yield sse(
        {
            "aurora_event": "route_plan",
            "task": task,
            "auto_route": True,
            "candidates": route_summary(routes, 6),
        }
    )

    errors: list[str] = []
    for idx, r in enumerate(routes):
        yield sse(
            {
                "aurora_event": "trying_route",
                "index": idx,
                "profile": r.profile,
                "model": r.model,
                "label": r.label,
                "tier": r.tier,
            }
        )

        got_any = False
        failed = False
        fail_status = None
        fail_body = ""

        async for kind, status, text in stream_completion_lines(
            r.api_base, r.api_key, r.model, messages, temperature, max_tokens
        ):
            if kind == "error":
                failed = True
                fail_status = status
                fail_body = text
                break
            if kind == "delta":
                if not got_any:
                    got_any = True
                    yield sse(
                        {
                            "aurora_event": "route_selected",
                            "profile": r.profile,
                            "model": r.model,
                            "label": r.label,
                            "tier": r.tier,
                        }
                    )
                yield sse(event_chunk(text, model=r.model))
            if kind == "done":
                break

        if got_any and not failed:
            yield sse(event_done(model=r.model))
            yield sse("[DONE]")
            return

        # If stream failed before content, failover
        msg = f"{r.label} ({r.model}): {fail_status} {fail_body[:200]}"
        errors.append(msg)
        retriable = is_retriable_error(fail_status, fail_body) or not got_any
        yield sse({"aurora_event": "route_failed", "error": msg[:300], "retriable": retriable})
        if not retriable and got_any:
            # partial? stop
            yield sse(event_done(model=r.model))
            yield sse("[DONE]")
            return
        continue

    # All failed — try free preference reshuffle once more if not already
    err_text = (
        "**All model routes failed** (credits, rate limits, or availability).\n\n"
        + "\n".join(f"- {e}" for e in errors[:6])
        + "\n\nAurora tried premium → standard → NVIDIA → free routes. "
        "Add balance on OpenRouter/NVIDIA or wait for rate limits to reset."
    )
    yield sse(event_chunk(err_text, model="aurora-router"))
    yield sse(event_done(model="aurora-router"))
    yield sse("[DONE]")


def pick_debate_panel(routes, n: int = 3):
    """
    Prefer diverse (profile, family) models for parallel debate.
    Falls back to next best routes if diversity is limited.
    """
    n = max(2, min(int(n or 3), 5))
    selected = []
    used_models: set[str] = set()
    used_families: set[str] = set()
    used_profiles: set[str] = set()

    def family(model: str) -> str:
        m = (model or "").lower()
        if "claude" in m or "anthropic" in m:
            return "claude"
        if "gpt" in m or "openai" in m:
            return "openai"
        if "gemini" in m or "google" in m:
            return "gemini"
        if "deepseek" in m:
            return "deepseek"
        if "qwen" in m:
            return "qwen"
        if "llama" in m or "meta" in m:
            return "llama"
        if "minimax" in m:
            return "minimax"
        if "nemotron" in m:
            return "nemotron"
        return m.split("/")[0] if "/" in m else m[:12]

    # Pass 1: unique family
    for r in routes:
        if len(selected) >= n:
            break
        fam = family(r.model)
        if r.model in used_models or fam in used_families:
            continue
        selected.append(r)
        used_models.add(r.model)
        used_families.add(fam)
        used_profiles.add(r.profile)

    # Pass 2: unique profile if still short
    if len(selected) < n:
        for r in routes:
            if len(selected) >= n:
                break
            if r.model in used_models:
                continue
            if r.profile in used_profiles and len(selected) >= 2:
                # allow same profile later
                continue
            selected.append(r)
            used_models.add(r.model)
            used_profiles.add(r.profile)

    # Pass 3: fill remaining
    if len(selected) < n:
        for r in routes:
            if len(selected) >= n:
                break
            if r.model in used_models:
                continue
            selected.append(r)
            used_models.add(r.model)

    return selected[:n]


async def complete_with_failover(routes, messages, temperature, max_tokens) -> tuple[Optional[str], Optional[Any], str]:
    """Try routes until one returns text. Returns (text, route, error)."""
    errors = []
    for r in routes:
        data, status, err = await try_completion(
            r.api_base, r.api_key, r.model, messages, temperature, max_tokens
        )
        if data:
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if isinstance(content, list):
                # multimodal content parts
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("text"):
                        parts.append(p["text"])
                    elif isinstance(p, str):
                        parts.append(p)
                content = "\n".join(parts)
            if content.strip():
                return content, r, ""
            errors.append(f"{r.label}: empty content")
            continue
        errors.append(f"{r.label}: HTTP {status} {(err or '')[:140]}")
        if not is_retriable_error(status, err):
            continue
    return None, None, "; ".join(errors[:4]) or "all routes failed"


async def orchestrate_run(routes, messages, temperature, max_tokens, settings, pipeline_id: str = "auto"):
    goal = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            goal = c if isinstance(c, str) else json.dumps(c)
            break
    system = next((m.get("content") for m in messages if m.get("role") == "system"), DEFAULT_SYSTEM)
    hist = [m for m in messages if m.get("role") in ("user", "assistant")]

    orch = Orchestrator(
        routes=routes,
        settings=settings,
        complete=complete_with_failover,
        try_completion_fn=try_completion,
        is_retriable_fn=is_retriable_error,
    )
    final = ""
    steps = []
    used = "aurora-orchestrator"
    async for ev in orch.stream(pipeline_id, goal, hist, system or DEFAULT_SYSTEM, temperature, max_tokens):
        if ev.get("aurora_event") == "orch_step_done" and ev.get("ok"):
            used = ev.get("model") or used
            steps.append(
                {
                    "id": ev.get("step_id"),
                    "module": ev.get("module"),
                    "title": ev.get("title"),
                    "label": ev.get("label"),
                    "ok": True,
                }
            )
        if ev.get("aurora_event") in ("orch_final", "orch_done"):
            final = ev.get("content") or ev.get("final") or final
    meta = {"pipeline": pipeline_id, "steps": steps}
    return final or "Orchestration produced no content.", used, meta


async def orchestrate_stream(
    routes,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    settings: dict,
    task: str,
    pipeline_id: str = "auto",
) -> AsyncGenerator[str, None]:
    goal = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            goal = c if isinstance(c, str) else json.dumps(c)
            break
    system = next((m.get("content") for m in messages if m.get("role") == "system"), DEFAULT_SYSTEM)
    hist = [m for m in messages if m.get("role") in ("user", "assistant")]

    yield sse(
        {
            "aurora_event": "orch_boot",
            "task": task,
            "pipeline": pipeline_id,
            "routes": route_summary(routes, 5),
        }
    )

    orch = Orchestrator(
        routes=routes,
        settings=settings,
        complete=complete_with_failover,
        try_completion_fn=try_completion,
        is_retriable_fn=is_retriable_error,
    )

    final_text = ""
    used_model = "aurora-orchestrator"
    transcript_parts: list[str] = []

    async for ev in orch.stream(
        pipeline_id, goal, hist, system or DEFAULT_SYSTEM, temperature, max_tokens
    ):
        et = ev.get("aurora_event")
        # Forward orchestration events to UI
        yield sse(ev)

        if et == "orch_step_done":
            title = ev.get("title") or ev.get("module")
            if ev.get("ok"):
                used_model = ev.get("model") or used_model
                preview = ev.get("preview") or ""
                transcript_parts.append(f"### {title}\n\n{ev.get('content') or preview}")
                # Surface tool use briefly in the main stream
                for t in ev.get("tool_trace") or []:
                    yield sse(
                        {
                            "aurora_event": "tool_call",
                            "name": t.get("name"),
                            "arguments": "",
                            "step": ev.get("step_id"),
                        }
                    )
                    yield sse(
                        {
                            "aurora_event": "tool_result",
                            "name": t.get("name"),
                            "ok": t.get("ok", True),
                            "preview": t.get("preview") or "",
                            "step": ev.get("step_id"),
                        }
                    )
            else:
                transcript_parts.append(f"### {title} (failed)\n\n{ev.get('error') or 'error'}")

        if et == "orch_final":
            final_text = ev.get("content") or final_text
        if et == "orch_done":
            final_text = ev.get("final") or final_text

    body = final_text or "\n\n".join(transcript_parts[-3:]) or "No orchestration output."
    header = f"_Orchestrated pipeline `{pipeline_id}`_\n\n"
    out = header + body
    i = 0
    while i < len(out):
        piece = out[i : i + 28]
        i += 28
        yield sse(event_chunk(piece, model=used_model))
        await asyncio.sleep(0.004)
    yield sse(event_done(used_model))
    yield sse({"aurora_event": "orch_stream_done", "pipeline": pipeline_id, "model": used_model})
    yield sse("[DONE]")


async def debate_run(routes, messages, temperature, max_tokens, settings, panel_n: int = 3):
    """Non-stream debate: parallel panel → synthesis."""
    panel = pick_debate_panel(routes, panel_n)
    if not panel:
        return "No models available for debate.", "aurora-debate", []

    # Keep history light for panelists: system + last few turns + user
    hist = [m for m in messages if m.get("role") != "system"][-6:]
    system = next((m["content"] for m in messages if m.get("role") == "system"), DEFAULT_SYSTEM)

    panelist_system = (
        system
        + "\n\nYou are one expert on a multi-model debate panel. "
        "Give your best independent answer. Be concrete and useful. "
        "Do not mention other models or that you are in a debate."
    )

    async def one(r):
        msgs = [{"role": "system", "content": panelist_system}] + hist
        # slightly lower tokens for panelists
        panel_tokens = min(int(max_tokens), 2048)
        text, used, err = await complete_with_failover([r] + [x for x in routes if x.model != r.model][:4], msgs, temperature, panel_tokens)
        return {
            "label": r.label,
            "model": r.model,
            "profile": r.profile,
            "tier": r.tier,
            "ok": bool(text),
            "content": text or "",
            "error": err,
            "used_model": used.model if used else r.model,
            "used_label": used.label if used else r.label,
        }

    results = await asyncio.gather(*[one(r) for r in panel])
    successes = [x for x in results if x["ok"] and x["content"].strip()]
    if not successes:
        detail = "\n".join(f"- {x['label']}: {x.get('error') or 'failed'}" for x in results)
        return f"**Debate failed** — no panelist answered.\n\n{detail}", "aurora-debate", results

    # Synthesis
    briefs = []
    for i, x in enumerate(successes, 1):
        body = x["content"].strip()
        if len(body) > 3500:
            body = body[:3500] + "…"
        briefs.append(f"### Panelist {i}: {x['used_label']} (`{x['used_model']}`)\n\n{body}")

    user_q = ""
    for m in reversed(hist):
        if m.get("role") == "user":
            user_q = m.get("content") or ""
            break

    synth_system = (
        "You are Aurora's Debate Synthesizer. Multiple AI models answered the same user question. "
        "Produce the single best final answer for the user.\n\n"
        "Rules:\n"
        "1. Merge strengths; remove duplication.\n"
        "2. Prefer accurate, specific, actionable content over vague agreement.\n"
        "3. If panelists disagree, note the disagreement briefly and pick the best-supported view.\n"
        "4. Do not mention 'panelists' or model names unless useful for uncertainty.\n"
        "5. Write as Aurora — clear, structured markdown."
    )
    synth_user = (
        f"## Original question\n\n{user_q}\n\n"
        f"## Independent answers\n\n" + "\n\n---\n\n".join(briefs) + "\n\n"
        "## Task\nWrite the final merged answer now."
    )
    synth_msgs = [
        {"role": "system", "content": synth_system},
        {"role": "user", "content": synth_user},
    ]
    # Prefer a strong synthesizer from full route list
    synth_text, synth_route, synth_err = await complete_with_failover(
        routes, synth_msgs, min(temperature, 0.6), min(int(max_tokens), 4096)
    )
    if not synth_text:
        # fallback: concatenate
        joined = "\n\n".join(
            f"## {x['used_label']}\n\n{x['content']}" for x in successes
        )
        return (
            f"**Debate synthesis failed** ({synth_err}). Showing panel answers:\n\n{joined}",
            "aurora-debate",
            results,
        )

    header = (
        f"_Debate complete — {len(successes)}/{len(panel)} models · "
        f"synthesized by {synth_route.label if synth_route else 'Aurora'}_\n\n"
    )
    return header + synth_text, (synth_route.model if synth_route else "aurora-debate"), results


async def debate_stream(
    routes,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    settings: dict,
    task: str,
    panel_n: int = 3,
) -> AsyncGenerator[str, None]:
    panel = pick_debate_panel(routes, panel_n)
    yield sse(
        {
            "aurora_event": "debate_start",
            "task": task,
            "panel": [{"label": r.label, "model": r.model, "profile": r.profile, "tier": r.tier} for r in panel],
        }
    )

    if not panel:
        yield sse(event_chunk("No models available for debate.", "aurora-debate"))
        yield sse(event_done("aurora-debate"))
        yield sse("[DONE]")
        return

    hist = [m for m in messages if m.get("role") != "system"][-6:]
    system = next((m["content"] for m in messages if m.get("role") == "system"), DEFAULT_SYSTEM)
    panelist_system = (
        system
        + "\n\nYou are one expert on a multi-model debate panel. "
        "Give your best independent answer. Be concrete and useful. "
        "Do not mention other models or that you are in a debate."
    )

    async def one(r):
        msgs = [{"role": "system", "content": panelist_system}] + hist
        panel_tokens = min(int(max_tokens), 2048)
        chain = [r] + [x for x in routes if x.model != r.model][:4]
        text, used, err = await complete_with_failover(chain, msgs, temperature, panel_tokens)
        return {
            "label": r.label,
            "model": r.model,
            "profile": r.profile,
            "tier": r.tier,
            "ok": bool(text),
            "content": text or "",
            "error": err,
            "used_model": used.model if used else r.model,
            "used_label": used.label if used else r.label,
        }

    # Parallel panel
    tasks = [asyncio.create_task(one(r)) for r in panel]
    results = []
    for coro in asyncio.as_completed(tasks):
        item = await coro
        results.append(item)
        if item["ok"]:
            preview = item["content"].strip().replace("\n", " ")
            if len(preview) > 160:
                preview = preview[:160] + "…"
            yield sse(
                {
                    "aurora_event": "debate_panelist",
                    "label": item["used_label"],
                    "model": item["used_model"],
                    "profile": item["profile"],
                    "ok": True,
                    "preview": preview,
                    "chars": len(item["content"]),
                }
            )
        else:
            yield sse(
                {
                    "aurora_event": "debate_panelist",
                    "label": item["label"],
                    "model": item["model"],
                    "profile": item["profile"],
                    "ok": False,
                    "error": (item.get("error") or "failed")[:220],
                }
            )

    successes = [x for x in results if x["ok"] and x["content"].strip()]
    if not successes:
        detail = "\n".join(f"- {x['label']}: {x.get('error') or 'failed'}" for x in results)
        yield sse(event_chunk(f"**Debate failed** — no panelist answered.\n\n{detail}", "aurora-debate"))
        yield sse(event_done("aurora-debate"))
        yield sse("[DONE]")
        return

    yield sse(
        {
            "aurora_event": "debate_synthesize",
            "successes": len(successes),
            "panel_size": len(panel),
            "models": [x["used_label"] for x in successes],
        }
    )

    briefs = []
    for i, x in enumerate(successes, 1):
        body = x["content"].strip()
        if len(body) > 3500:
            body = body[:3500] + "…"
        briefs.append(f"### Panelist {i}: {x['used_label']} (`{x['used_model']}`)\n\n{body}")

    user_q = ""
    for m in reversed(hist):
        if m.get("role") == "user":
            user_q = m.get("content") or ""
            break

    synth_system = (
        "You are Aurora's Debate Synthesizer. Multiple AI models answered the same user question. "
        "Produce the single best final answer for the user.\n\n"
        "Rules:\n"
        "1. Merge strengths; remove duplication.\n"
        "2. Prefer accurate, specific, actionable content over vague agreement.\n"
        "3. If panelists disagree, note the disagreement briefly and pick the best-supported view.\n"
        "4. Do not mention 'panelists' or model names unless useful for uncertainty.\n"
        "5. Write as Aurora — clear, structured markdown."
    )
    synth_user = (
        f"## Original question\n\n{user_q}\n\n"
        f"## Independent answers\n\n" + "\n\n---\n\n".join(briefs) + "\n\n"
        "## Task\nWrite the final merged answer now."
    )
    synth_msgs = [
        {"role": "system", "content": synth_system},
        {"role": "user", "content": synth_user},
    ]

    # Stream synthesis with failover if needed
    synth_routes = routes
    got = False
    used_model = "aurora-debate"
    for r in synth_routes:
        yield sse(
            {
                "aurora_event": "trying_route",
                "index": 0,
                "profile": r.profile,
                "model": r.model,
                "label": r.label,
                "tier": r.tier,
            }
        )
        failed = False
        async for kind, status, text in stream_completion_lines(
            r.api_base, r.api_key, r.model, synth_msgs, min(temperature, 0.6), min(int(max_tokens), 4096)
        ):
            if kind == "error":
                failed = True
                yield sse({"aurora_event": "route_failed", "error": f"{r.label}: {status} {text[:160]}", "retriable": True})
                break
            if kind == "delta":
                if not got:
                    got = True
                    used_model = r.model
                    header = (
                        f"_Debate · {len(successes)} models"
                        f" ({', '.join(x['used_label'] for x in successes[:4])})"
                        f" · synthesized by {r.label}_\n\n"
                    )
                    yield sse(
                        {
                            "aurora_event": "route_selected",
                            "profile": r.profile,
                            "model": r.model,
                            "label": r.label,
                            "tier": r.tier,
                        }
                    )
                    yield sse(event_chunk(header, model=r.model))
                yield sse(event_chunk(text, model=r.model))
            if kind == "done":
                break
        if got and not failed:
            yield sse(event_done(used_model))
            yield sse(
                {
                    "aurora_event": "debate_done",
                    "successes": len(successes),
                    "panel_size": len(panel),
                    "synthesizer": used_model,
                }
            )
            yield sse("[DONE]")
            return

    # Fallback non-stream concat
    joined = "\n\n".join(f"## {x['used_label']}\n\n{x['content']}" for x in successes)
    yield sse(event_chunk(f"**Could not stream synthesis.** Panel answers:\n\n{joined}", "aurora-debate"))
    yield sse(event_done("aurora-debate"))
    yield sse({"aurora_event": "debate_done", "successes": len(successes), "panel_size": len(panel), "synthesizer": "fallback"})
    yield sse("[DONE]")


async def agent_run(routes, messages, temperature, max_tokens, settings) -> tuple[str, str]:
    """Non-streaming agent loop; returns (final_text, model_used)."""
    max_steps = int(settings.get("agent_max_steps") or 8)
    working = list(messages)
    used_model = routes[0].model if routes else "unknown"

    for step in range(max_steps):
        data = None
        last_err = ""
        for r in routes:
            # First try with tools; on failure, without tools
            data, status, err = await try_completion(
                r.api_base,
                r.api_key,
                r.model,
                working,
                temperature,
                max_tokens,
                tools=get_all_tool_specs(),
                tool_choice="auto",
            )
            if not data and is_retriable_error(status, err):
                # retry without tools (some models reject tools)
                data, status, err = await try_completion(
                    r.api_base, r.api_key, r.model, working, temperature, max_tokens
                )
            if data:
                used_model = r.model
                break
            last_err = f"{r.model}: {status} {err[:160]}"
            if not is_retriable_error(status, err):
                continue
        if not data:
            return f"Agent failed to get a model response. Last error: {last_err}", used_model

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = extract_tool_calls_from_message(message)
        content = message.get("content") or ""

        if tool_calls:
            # append assistant message
            working.append(
                {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                
                dangerous_tools = {"run_command", "write_file", "replace_file_content"}
                require_approvals = settings.get("require_tool_approvals", True)
                if name in dangerous_tools and require_approvals:
                    result = {"ok": False, "error": "Tool execution blocked. Approvals are required, but this request was sent in non-streaming mode."}
                else:
                    result = await run_tool_async(name, raw_args)
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False)[:12000],
                    }
                )
            continue

        # Maybe XML tool call only in content
        xml_calls = extract_tool_calls_from_message({"content": content})
        if xml_calls:
            working.append({"role": "assistant", "content": content})
            for call in xml_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                
                dangerous_tools = {"run_command", "write_file", "replace_file_content"}
                require_approvals = settings.get("require_tool_approvals", True)
                if name in dangerous_tools and require_approvals:
                    result = {"ok": False, "error": "Tool execution blocked. Approvals are required, but this request was sent in non-streaming mode."}
                else:
                    result = await run_tool_async(name, fn.get("arguments"))
                working.append(
                    {
                        "role": "user",
                        "content": f"Tool `{name}` result:\n```json\n{json.dumps(result, ensure_ascii=False)[:12000]}\n```",
                    }
                )
            continue

        return strip_tool_call_tags(content) or "(empty agent response)", used_model

    return "Agent stopped: max steps reached without a final answer.", used_model


async def agent_stream(
    routes,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    settings: dict,
    task: str,
) -> AsyncGenerator[str, None]:
    max_steps = int(settings.get("agent_max_steps") or 8)
    working = list(messages)

    yield sse(
        {
            "aurora_event": "agent_start",
            "task": task,
            "max_steps": max_steps,
            "tools": [t["function"]["name"] for t in get_all_tool_specs()][:40],
            "candidates": route_summary(routes, 6),
        }
    )

    used_model = routes[0].model if routes else "aurora"

    for step in range(max_steps):
        yield sse({"aurora_event": "agent_step", "step": step + 1, "max_steps": max_steps})

        data = None
        errors = []
        selected = None
        for r in routes:
            data, status, err = await try_completion(
                r.api_base,
                r.api_key,
                r.model,
                working,
                temperature,
                max_tokens,
                tools=get_all_tool_specs(),
                tool_choice="auto",
            )
            if not data:
                # models that reject tools
                if is_retriable_error(status, err) or "tool" in (err or "").lower():
                    data, status, err = await try_completion(
                        r.api_base, r.api_key, r.model, working, temperature, max_tokens
                    )
            if data:
                selected = r
                used_model = r.model
                break
            errors.append(f"{r.label}: {status} {(err or '')[:120]}")
            yield sse({"aurora_event": "route_failed", "error": errors[-1], "retriable": True})

        if not data or not selected:
            text = "**Agent error:** all routes failed.\n\n" + "\n".join(f"- {e}" for e in errors[:5])
            yield sse(event_chunk(text, model="aurora-agent"))
            yield sse(event_done("aurora-agent"))
            yield sse("[DONE]")
            return

        yield sse(
            {
                "aurora_event": "route_selected",
                "profile": selected.profile,
                "model": selected.model,
                "label": selected.label,
                "tier": selected.tier,
                "step": step + 1,
            }
        )

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        tool_calls = extract_tool_calls_from_message(message)

        if not tool_calls:
            # final answer — stream it out in chunks for UX
            final = strip_tool_call_tags(content) or "(empty)"
            # chunk
            i = 0
            while i < len(final):
                piece = final[i : i + 24]
                i += 24
                yield sse(event_chunk(piece, model=used_model))
                await asyncio.sleep(0.005)
            yield sse(event_done(used_model))
            yield sse({"aurora_event": "agent_done", "steps": step + 1, "model": used_model})
            yield sse("[DONE]")
            return

        # Show brief assistant thought if any
        if content and not content.strip().startswith("<tool_call>"):
            thought = strip_tool_call_tags(content)
            if thought:
                yield sse(event_chunk(f"_{thought[:500]}_\n\n", model=used_model))

        working.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})

        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or "unknown"
            raw_args = fn.get("arguments") or "{}"
            yield sse(
                {
                    "aurora_event": "tool_call",
                    "name": name,
                    "arguments": raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
                    "step": step + 1,
                }
            )
            dangerous_tools = {"run_command", "write_file", "replace_file_content"}
            require_approvals = settings.get("require_tool_approvals", True)
            
            if name in dangerous_tools and require_approvals:
                approval_id = str(uuid.uuid4())
                event = asyncio.Event()
                pending_approvals[approval_id] = event
                
                yield sse({
                    "aurora_event": "tool_approval_required",
                    "approval_id": approval_id,
                    "name": name,
                    "arguments": raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
                    "step": step + 1
                })
                
                try:
                    await asyncio.wait_for(event.wait(), timeout=300.0)
                except asyncio.TimeoutExpired:
                    pending_approvals.pop(approval_id, None)
                    result = {"ok": False, "error": "Tool execution timed out waiting for approval."}
                else:
                    pending_approvals.pop(approval_id, None)
                    approved = approval_results.pop(approval_id, False)
                    if not approved:
                        result = {"ok": False, "error": "Tool execution rejected by user."}
                    else:
                        result = await run_tool_async(name, raw_args)
            else:
                result = await run_tool_async(name, raw_args)
            # compact preview for UI
            preview = json.dumps(result, ensure_ascii=False)
            if len(preview) > 500:
                preview = preview[:500] + "…"
            yield sse(
                {
                    "aurora_event": "tool_result",
                    "name": name,
                    "ok": bool(result.get("ok", True)),
                    "preview": preview,
                    "step": step + 1,
                }
            )
            working.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False)[:12000],
                }
            )

        # continue loop

    yield sse(event_chunk("Agent stopped: max steps reached.", model=used_model))
    yield sse(event_done(used_model))
    yield sse("[DONE]")


def demo_reply(messages: list[ChatMessage]) -> str:
    last = last_user_text(messages)
    return (
        f"**(Demo mode)** No API routes available.\n\n> {last[:500]}\n\n"
        "Configure OpenRouter/NVIDIA keys in Settings."
    )


async def demo_stream(messages: list[ChatMessage]) -> AsyncGenerator[str, None]:
    text = demo_reply(messages)
    for i in range(0, len(text), 12):
        yield sse(event_chunk(text[i : i + 12], "aurora-demo"))
        await asyncio.sleep(0.01)
    yield sse(event_done("aurora-demo"))
    yield sse("[DONE]")


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return {"error": "Frontend missing"}
    return FileResponse(index_path)



@app.get("/sw.js")
async def service_worker():
    path = STATIC_DIR / "sw.js"
    if not path.exists():
        raise HTTPException(404, "sw missing")
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
async def web_manifest():
    path = STATIC_DIR / "manifest.webmanifest"
    if not path.exists():
        raise HTTPException(404, "manifest missing")
    return FileResponse(path, media_type="application/manifest+json")

@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    file_path = STATIC_DIR / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    raise HTTPException(404, "Not found")
