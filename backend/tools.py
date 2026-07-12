"""
Aurora agent tools — sandboxed utilities the model can call.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

try:
    from mcp_manager import mcp_manager, parse_qualified_name
except Exception:  # pragma: no cover
    mcp_manager = None
    parse_qualified_name = None

try:
    from security import is_blocked_url, redact_secrets
except Exception:  # pragma: no cover
    def is_blocked_url(url: str):
        return False, ""
    def redact_secrets(text: str):
        return text

WORKSPACE = Path("/home/user/aurora/workspace")
WORKSPACE.mkdir(parents=True, exist_ok=True)

# Safe math operators for calculator
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_FUNCS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    "pi": math.pi,
    "e": math.e,
}


def _safe_eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Num):  # pragma: no cover
        return node.n
    if isinstance(node, ast.BinOp):
        op = _OPS.get(type(node.op))
        if not op:
            raise ValueError("operator not allowed")
        return op(_safe_eval_node(node.left), _safe_eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _OPS.get(type(node.op))
        if not op:
            raise ValueError("unary operator not allowed")
        return op(_safe_eval_node(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS.get(node.func.id)
        if not callable(fn):
            raise ValueError(f"function not allowed: {node.func.id}")
        args = [_safe_eval_node(a) for a in node.args]
        return fn(*args)
    if isinstance(node, ast.Name):
        val = _FUNCS.get(node.id)
        if isinstance(val, (int, float)):
            return val
        raise ValueError(f"name not allowed: {node.id}")
    raise ValueError("expression not allowed")


def tool_calculator(expression: str) -> dict[str, Any]:
    expr = (expression or "").strip()
    if not expr or len(expr) > 200:
        return {"ok": False, "error": "expression empty or too long"}
    try:
        tree = ast.parse(expr, mode="eval")
        value = _safe_eval_node(tree)
        return {"ok": True, "expression": expr, "result": value}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_get_time(timezone_name: str = "UTC") -> dict[str, Any]:
    # Keep dependency-free: UTC + Asia/Karachi offset note
    now = datetime.now(timezone.utc)
    return {
        "ok": True,
        "utc": now.isoformat(),
        "unix": int(now.timestamp()),
        "note": "Server clock is UTC. User local timezone is typically Asia/Karachi (UTC+5).",
        "requested": timezone_name,
    }


def _safe_workspace_path(rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("invalid path")
    path = (WORKSPACE / rel).resolve()
    if not str(path).startswith(str(WORKSPACE.resolve())):
        raise ValueError("path escapes workspace")
    return path


def tool_list_files(directory: str = ".") -> dict[str, Any]:
    try:
        path = _safe_workspace_path(directory if directory not in (".", "") else ".")
        if directory in (".", ""):
            path = WORKSPACE
        if not path.exists():
            return {"ok": False, "error": f"not found: {directory}"}
        items = []
        for p in sorted(path.iterdir()):
            items.append(
                {
                    "name": p.name,
                    "type": "dir" if p.is_dir() else "file",
                    "size": p.stat().st_size if p.is_file() else None,
                }
            )
        return {"ok": True, "directory": str(path.relative_to(WORKSPACE)) if path != WORKSPACE else ".", "items": items}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_read_file(path: str, max_chars: int = 12000) -> dict[str, Any]:
    try:
        p = _safe_workspace_path(path)
        if not p.is_file():
            return {"ok": False, "error": "not a file"}
        data = p.read_text(encoding="utf-8", errors="replace")
        truncated = len(data) > max_chars
        return {
            "ok": True,
            "path": path,
            "content": data[:max_chars],
            "truncated": truncated,
            "total_chars": len(data),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_write_file(path: str, content: str) -> dict[str, Any]:
    try:
        p = _safe_workspace_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        text = content if content is not None else ""
        if len(text) > 400_000:
            return {"ok": False, "error": "content too large"}
        p.write_text(text, encoding="utf-8")
        return {"ok": True, "path": path, "bytes": len(text.encode("utf-8"))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """DuckDuckGo HTML-lite search (no API key)."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query"}
    max_results = max(1, min(int(max_results or 5), 8))
    try:
        # Use DuckDuckGo lite
        url = "https://lite.duckduckgo.com/lite/"
        with httpx.Client(timeout=25.0, follow_redirects=True) as client:
            r = client.post(url, data={"q": q})
            html = r.text
        # Parse simple links
        results = []
        # DDG lite uses result-link class sometimes; fallback regex for http links + nearby text
        for m in re.finditer(r'href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
            href = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if "duckduckgo.com" in href:
                continue
            if not title:
                continue
            results.append({"title": title[:200], "url": href})
            if len(results) >= max_results:
                break
        if not results:
            # Instant answer API fallback
            with httpx.Client(timeout=20.0) as client:
                r = client.get("https://api.duckduckgo.com/", params={"q": q, "format": "json", "no_redirect": 1})
                data = r.json()
            if data.get("AbstractText"):
                results.append(
                    {
                        "title": data.get("Heading") or q,
                        "url": data.get("AbstractURL") or "",
                        "snippet": data.get("AbstractText"),
                    }
                )
            for topic in (data.get("RelatedTopics") or [])[:max_results]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(
                        {
                            "title": topic.get("Text", "")[:160],
                            "url": topic.get("FirstURL") or "",
                        }
                    )
        return {"ok": True, "query": q, "results": results[:max_results]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_fetch_url(url: str, max_chars: int = 10000) -> dict[str, Any]:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return {"ok": False, "error": "only http/https URLs allowed"}
    blocked, reason = is_blocked_url(u)
    if blocked:
        return {"ok": False, "error": f"url blocked: {reason}"}
    parsed = urlparse(u)
    if parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0"} or (parsed.hostname or "").endswith(".local"):
        return {"ok": False, "error": "local URLs blocked"}
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(u, headers={"User-Agent": "AuroraAgent/1.0"})
            ctype = r.headers.get("content-type", "")
            text = r.text
        # crude HTML → text
        if "html" in ctype or text.lstrip().lower().startswith("<!doctype") or text.lstrip().lower().startswith("<html"):
            text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
            text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
            text = re.sub(r"(?is)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        truncated = len(text) > max_chars
        return {
            "ok": True,
            "url": u,
            "status": r.status_code,
            "content_type": ctype,
            "text": text[:max_chars],
            "truncated": truncated,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_python_eval(code: str) -> dict[str, Any]:
    """
    Very restricted Python eval for small computations.
    No imports, no file IO, no attribute access on dangerous objects.
    """
    src = (code or "").strip()
    if not src or len(src) > 4000:
        return {"ok": False, "error": "code empty or too long"}
    banned = ("import ", "__", "open(", "exec(", "eval(", "os.", "sys.", "subprocess", "Path", "socket")
    lower = src.lower()
    for b in banned:
        if b.lower() in lower:
            return {"ok": False, "error": f"banned token: {b}"}
    try:
        # Allow expression or simple assignments via exec in tiny sandbox
        safe_builtins = {
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
            "len": len,
            "range": range,
            "enumerate": enumerate,
            "sorted": sorted,
            "round": round,
            "int": int,
            "float": float,
            "str": str,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "bool": bool,
            "print": lambda *a, **k: None,
        }
        env: dict[str, Any] = {"__builtins__": safe_builtins, "math": math}
        lines = src.splitlines()
        if len(lines) == 1 and "=" not in src.split("#")[0]:
            result = eval(src, env, env)  # noqa: S307 — restricted env
            return {"ok": True, "result": repr(result)}
        exec(src, env, env)  # noqa: S102 — restricted env
        # Prefer explicit result variable
        if "result" in env:
            return {"ok": True, "result": repr(env["result"])}
        # last simple assignment name
        return {"ok": True, "result": "executed", "locals": {k: repr(v) for k, v in env.items() if k != "__builtins__" and k != "math"}}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc().splitlines()[-3:]}


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web for current information. Returns titles and URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "1-8 results", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a public http(s) URL and return extracted text content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 10000},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a mathematical expression precisely.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_eval",
            "description": "Run a small restricted Python snippet for calculations/data transforms. No imports or file IO.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the Aurora agent workspace.",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string", "default": "."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the Aurora agent workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 12000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a text file into the Aurora agent workspace (for deliverables).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current UTC time.",
            "parameters": {
                "type": "object",
                "properties": {"timezone_name": {"type": "string", "default": "UTC"}},
            },
        },
    },
]

def get_all_tool_specs() -> list[dict[str, Any]]:
    """Built-in tools + connected MCP tools."""
    specs = list(TOOL_SPECS)
    if mcp_manager is not None:
        try:
            specs.extend(mcp_manager.connected_tool_specs())
        except Exception:
            pass
    return specs


TOOL_IMPL: dict[str, Callable[..., dict[str, Any]]] = {
    "web_search": lambda **kw: tool_web_search(kw.get("query", ""), kw.get("max_results", 5)),
    "fetch_url": lambda **kw: tool_fetch_url(kw.get("url", ""), kw.get("max_chars", 10000)),
    "calculator": lambda **kw: tool_calculator(kw.get("expression", "")),
    "python_eval": lambda **kw: tool_python_eval(kw.get("code", "")),
    "list_files": lambda **kw: tool_list_files(kw.get("directory", ".")),
    "read_file": lambda **kw: tool_read_file(kw.get("path", ""), kw.get("max_chars", 12000)),
    "write_file": lambda **kw: tool_write_file(kw.get("path", ""), kw.get("content", "")),
    "get_time": lambda **kw: tool_get_time(kw.get("timezone_name", "UTC")),
}


def run_tool(name: str, arguments: dict[str, Any] | str | None) -> dict[str, Any]:
    args = arguments or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {"input": args}
    if not isinstance(args, dict):
        args = {}

    # MCP tools: mcp__server__tool
    if name.startswith("mcp__") and mcp_manager is not None and parse_qualified_name is not None:
        parsed = parse_qualified_name(name)
        if not parsed:
            return {"ok": False, "error": f"invalid MCP tool name: {name}"}
        server_id, tool_name = parsed
        try:
            # call_tool is async; run safely from sync context
            import asyncio

            async def _call():
                return await mcp_manager.call_tool(server_id, tool_name, args)

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # schedule in running loop via thread-less nest pattern
                    import concurrent.futures

                    fut: concurrent.futures.Future = concurrent.futures.Future()

                    def _done(task):
                        try:
                            fut.set_result(task.result())
                        except Exception as e:
                            fut.set_exception(e)

                    task = asyncio.create_task(_call())
                    task.add_done_callback(_done)
                    # cannot block running loop here — use a short cooperative wait via asyncio is unsafe.
                    # Prefer dedicated async path; fall back to nest_asyncio-less bridge:
                    # Use background task result via temporary loop in a new thread.
                    import threading

                    box = {}

                    def runner():
                        box["r"] = asyncio.run(_call())

                    th = threading.Thread(target=runner, daemon=True)
                    th.start()
                    th.join(timeout=120)
                    if "r" not in box:
                        return {"ok": False, "error": "MCP tool timed out"}
                    return box["r"]
                return loop.run_until_complete(_call())
            except RuntimeError:
                return asyncio.run(_call())
        except Exception as e:
            return {"ok": False, "error": f"MCP call failed: {e}"}

    fn = TOOL_IMPL.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return fn(**args)
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def run_tool_async(name: str, arguments: dict[str, Any] | str | None) -> dict[str, Any]:
    """Async tool runner (preferred for MCP)."""
    args = arguments or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {"input": args}
    if not isinstance(args, dict):
        args = {}
    if name.startswith("mcp__") and mcp_manager is not None and parse_qualified_name is not None:
        parsed = parse_qualified_name(name)
        if not parsed:
            return {"ok": False, "error": f"invalid MCP tool name: {name}"}
        server_id, tool_name = parsed
        return await mcp_manager.call_tool(server_id, tool_name, args)
    return run_tool(name, args)


AGENT_SYSTEM_EXTRA = """
You are Aurora in **Agent Mode**.
You can use tools to search the web, fetch pages, calculate, run small Python, read/write files in a sandbox workspace, and call connected MCP tools (names start with mcp__).

Rules:
1. Prefer tools when facts may be outdated, when math must be exact, or when producing multi-file deliverables.
2. Call tools via the provided function-calling interface (or via the XML fallback format if needed).
3. After tools return, synthesize a clear final answer for the user.
4. Do not claim you did something unless a tool result confirms it.
5. For substantial code/HTML, put the final deliverable in a markdown code fence and/or write it with write_file.
6. Be efficient: use the minimum tools needed. Stop when you can answer well.
""".strip()


# Fallback protocol for models without native tool calling
TOOL_FALLBACK_INSTRUCTIONS = """
If you need a tool and native function calling is unavailable, output ONLY:
<tool_call>
{"name": "TOOL_NAME", "arguments": {..}}
</tool_call>
Wait for the tool result before the final answer.
When finished, answer normally without tool_call tags.
""".strip()
