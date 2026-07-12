#!/usr/bin/env python3
"""Free local code MCP — inspect/format/search code in workspace (no cloud)."""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ.get("AURORA_WORKSPACE", str(Path(__file__).resolve().parents[2] / "workspace"))).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
mcp = FastMCP("aurora-code")


def _safe(rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/")
    if ".." in Path(rel).parts:
        raise ValueError("path escapes workspace")
    p = (ROOT / rel).resolve()
    if not str(p).startswith(str(ROOT)):
        raise ValueError("path escapes workspace")
    return p


@mcp.tool()
def py_outline(path: str) -> str:
    """Outline Python classes/functions in a workspace file."""
    p = _safe(path)
    src = p.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return json.dumps({"ok": False, "error": f"syntax: {e}"})
    items = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append({"type": "function", "name": node.name, "line": node.lineno})
        elif isinstance(node, ast.ClassDef):
            methods = [
                m.name
                for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            items.append({"type": "class", "name": node.name, "line": node.lineno, "methods": methods})
    return json.dumps({"ok": True, "path": path, "items": items}, indent=2)


@mcp.tool()
def py_ast_check(path: str) -> str:
    """Parse-check a Python file for syntax errors."""
    p = _safe(path)
    src = p.read_text(encoding="utf-8", errors="replace")
    try:
        ast.parse(src)
        return json.dumps({"ok": True, "path": path, "valid": True})
    except SyntaxError as e:
        return json.dumps(
            {
                "ok": True,
                "path": path,
                "valid": False,
                "error": e.msg,
                "line": e.lineno,
                "offset": e.offset,
            }
        )


@mcp.tool()
def extract_todos(path: str = ".", exts: str = ".py,.js,.ts,.tsx,.md,.go,.rs") -> str:
    """Find TODO/FIXME/XXX comments under a workspace path."""
    root = _safe(path)
    allow = {e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}" for e in exts.split(",") if e.strip()}
    hits = []
    paths = [root] if root.is_file() else list(root.rglob("*"))
    for fp in paths:
        if not fp.is_file():
            continue
        if allow and fp.suffix.lower() not in allow:
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"\b(TODO|FIXME|XXX|HACK)\b", line):
                hits.append(
                    {
                        "path": str(fp.relative_to(ROOT)) if str(fp).startswith(str(ROOT)) else str(fp),
                        "line": i,
                        "text": line.strip()[:200],
                    }
                )
                if len(hits) >= 100:
                    return json.dumps({"ok": True, "hits": hits, "truncated": True}, indent=2)
    return json.dumps({"ok": True, "hits": hits}, indent=2)


@mcp.tool()
def count_loc(path: str = ".", exts: str = ".py,.js,.ts,.tsx,.go,.rs,.java") -> str:
    """Count lines of code by extension under a path."""
    root = _safe(path)
    allow = {e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}" for e in exts.split(",") if e.strip()}
    stats = {}
    total = 0
    files = 0
    paths = [root] if root.is_file() else root.rglob("*")
    for fp in paths:
        if not fp.is_file():
            continue
        if allow and fp.suffix.lower() not in allow:
            continue
        try:
            n = sum(1 for _ in fp.open("r", encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        stats[fp.suffix.lower() or "(none)"] = stats.get(fp.suffix.lower() or "(none)", 0) + n
        total += n
        files += 1
    return json.dumps({"ok": True, "files": files, "total_lines": total, "by_ext": stats}, indent=2)


@mcp.tool()
def strip_comments_js_like(code: str) -> str:
    """Best-effort strip // and /* */ comments from JS/TS-like code."""
    # remove block comments
    out = re.sub(r"/\*[\s\S]*?\*/", "", code)
    # remove line comments
    out = re.sub(r"(?m)//.*?$", "", out)
    return out


@mcp.tool()
def markdown_toc(markdown: str) -> str:
    """Generate a markdown table of contents from headings."""
    toc = []
    for line in (markdown or "").splitlines():
        m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        anchor = re.sub(r"[^a-z0-9\s-]", "", title.lower())
        anchor = re.sub(r"\s+", "-", anchor.strip())
        toc.append(f'{"  " * (level - 1)}- [{title}](#{anchor})')
    return "\n".join(toc) if toc else "(no headings)"


if __name__ == "__main__":
    mcp.run(transport="stdio")
