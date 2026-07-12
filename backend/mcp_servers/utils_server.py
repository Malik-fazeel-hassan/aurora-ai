#!/usr/bin/env python3
"""Free local utility MCP — text, math, json, hashing, encoding (no API keys)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path
from urllib.parse import quote, unquote

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("aurora-utils")


@mcp.tool()
def now_utc() -> str:
    """Current UTC time ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


@mcp.tool()
def uuid4() -> str:
    """Generate a random UUID4."""
    return str(uuid.uuid4())


@mcp.tool()
def hash_text(text: str, algorithm: str = "sha256") -> str:
    """Hash text with md5|sha1|sha256|sha512."""
    algo = (algorithm or "sha256").lower()
    if algo not in hashlib.algorithms_available:
        return f"error: unsupported algorithm {algo}"
    return hashlib.new(algo, text.encode("utf-8")).hexdigest()


@mcp.tool()
def base64_encode(text: str) -> str:
    """Base64-encode UTF-8 text."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


@mcp.tool()
def base64_decode(data: str) -> str:
    """Base64-decode to UTF-8 text."""
    try:
        return base64.b64decode(data.encode("ascii")).decode("utf-8")
    except Exception as e:
        return f"error: {e}"


@mcp.tool()
def url_encode(text: str) -> str:
    """Percent-encode text for URLs."""
    return quote(text, safe="")


@mcp.tool()
def url_decode(text: str) -> str:
    """Percent-decode URL text."""
    return unquote(text)


@mcp.tool()
def regex_find(pattern: str, text: str, flags: str = "") -> str:
    """Find all regex matches. flags: i m s."""
    f = 0
    if "i" in flags:
        f |= re.I
    if "m" in flags:
        f |= re.M
    if "s" in flags:
        f |= re.S
    try:
        matches = re.findall(pattern, text, flags=f)
        return json.dumps(matches, ensure_ascii=False, indent=2)
    except re.error as e:
        return f"error: {e}"


@mcp.tool()
def regex_replace(pattern: str, repl: str, text: str, count: int = 0) -> str:
    """Regex replace. count=0 means all."""
    try:
        return re.sub(pattern, repl, text, count=count)
    except re.error as e:
        return f"error: {e}"


@mcp.tool()
def json_format(text: str, indent: int = 2) -> str:
    """Pretty-print or validate JSON text."""
    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False, indent=indent)
    except Exception as e:
        return f"error: {e}"


@mcp.tool()
def json_query(text: str, path: str) -> str:
    """Simple dotted JSON path, e.g. user.name or items.0.id"""
    try:
        obj = json.loads(text)
        cur = obj
        for part in path.split("."):
            if part == "":
                continue
            if isinstance(cur, list) and part.isdigit():
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                cur = cur[part]
            else:
                return "error: cannot traverse path"
        return json.dumps(cur, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"error: {e}"


@mcp.tool()
def text_stats(text: str) -> str:
    """Count characters, words, lines."""
    lines = text.splitlines()
    words = re.findall(r"\S+", text)
    return json.dumps(
        {
            "chars": len(text),
            "chars_no_ws": len(re.sub(r"\s+", "", text)),
            "words": len(words),
            "lines": len(lines),
        },
        indent=2,
    )


@mcp.tool()
def text_diff(a: str, b: str, fromfile: str = "a", tofile: str = "b") -> str:
    """Unified diff between two texts."""
    return "".join(
        unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    ) or "(no differences)"


@mcp.tool()
def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@mcp.tool()
def calc_eval(expression: str) -> str:
    """Safe arithmetic expression evaluator (+ - * / // % ** parentheses, numbers)."""
    import ast
    import operator as op

    ops = {
        ast.Add: op.add,
        ast.Sub: op.sub,
        ast.Mult: op.mul,
        ast.Div: op.truediv,
        ast.FloorDiv: op.floordiv,
        ast.Mod: op.mod,
        ast.Pow: op.pow,
        ast.USub: op.neg,
        ast.UAdd: op.pos,
    }

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    try:
        return str(_eval(ast.parse(expression, mode="eval")))
    except Exception as e:
        return f"error: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
