#!/usr/bin/env python3
"""Free local workspace MCP — files under Aurora workspace only."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ.get("AURORA_WORKSPACE", str(Path(__file__).resolve().parents[2] / "workspace"))).resolve()
ROOT.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("aurora-workspace")


def _safe(rel: str) -> Path:
    rel = (rel or ".").strip().lstrip("/")
    if ".." in Path(rel).parts:
        raise ValueError("path escapes workspace")
    p = (ROOT / rel).resolve()
    if not str(p).startswith(str(ROOT)):
        raise ValueError("path escapes workspace")
    return p


@mcp.tool()
def ws_pwd() -> str:
    """Return workspace root path."""
    return str(ROOT)


@mcp.tool()
def ws_list(path: str = ".", recursive: bool = False, max_entries: int = 200) -> str:
    """List files/dirs in workspace path."""
    p = _safe(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "not found"})
    items = []
    if recursive:
        for i, fp in enumerate(p.rglob("*")):
            if i >= max_entries:
                items.append({"name": "…", "note": "truncated"})
                break
            items.append(
                {
                    "path": str(fp.relative_to(ROOT)),
                    "type": "dir" if fp.is_dir() else "file",
                    "size": fp.stat().st_size if fp.is_file() else None,
                }
            )
    else:
        for fp in sorted(p.iterdir()):
            items.append(
                {
                    "path": str(fp.relative_to(ROOT)),
                    "type": "dir" if fp.is_dir() else "file",
                    "size": fp.stat().st_size if fp.is_file() else None,
                }
            )
            if len(items) >= max_entries:
                break
    return json.dumps({"ok": True, "path": path, "items": items}, indent=2)


@mcp.tool()
def ws_read(path: str, max_chars: int = 50000) -> str:
    """Read a UTF-8 text file from workspace."""
    p = _safe(path)
    if not p.is_file():
        return json.dumps({"ok": False, "error": "not a file"})
    data = p.read_text(encoding="utf-8", errors="replace")
    return json.dumps(
        {
            "ok": True,
            "path": path,
            "truncated": len(data) > max_chars,
            "content": data[:max_chars],
        },
        ensure_ascii=False,
    )


@mcp.tool()
def ws_write(path: str, content: str, append: bool = False) -> str:
    """Write/append a text file in workspace."""
    p = _safe(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with p.open(mode, encoding="utf-8") as f:
        f.write(content or "")
    return json.dumps({"ok": True, "path": path, "bytes": p.stat().st_size})


@mcp.tool()
def ws_mkdir(path: str) -> str:
    """Create directory in workspace."""
    p = _safe(path)
    p.mkdir(parents=True, exist_ok=True)
    return json.dumps({"ok": True, "path": path})


@mcp.tool()
def ws_delete(path: str) -> str:
    """Delete file or empty-safe tree in workspace."""
    p = _safe(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "not found"})
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return json.dumps({"ok": True, "deleted": path})


@mcp.tool()
def ws_search(query: str, path: str = ".", max_hits: int = 50) -> str:
    """Case-insensitive substring search across text files."""
    root = _safe(path)
    hits = []
    q = (query or "").lower()
    if not q:
        return json.dumps({"ok": False, "error": "empty query"})
    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        if fp.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".gz", ".exe", ".bin"}:
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if q in text.lower():
            # first line context
            for i, line in enumerate(text.splitlines(), 1):
                if q in line.lower():
                    hits.append({"path": str(fp.relative_to(ROOT)), "line": i, "text": line[:200]})
                    break
        if len(hits) >= max_hits:
            break
    return json.dumps({"ok": True, "hits": hits}, ensure_ascii=False, indent=2)


@mcp.tool()
def ws_tree(path: str = ".", max_depth: int = 3) -> str:
    """ASCII tree of workspace directory."""
    root = _safe(path)
    lines = [str(root.relative_to(ROOT)) if root != ROOT else "."]

    def walk(d: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception:
            return
        for i, e in enumerate(entries[:80]):
            last = i == len(entries[:80]) - 1
            branch = "└── " if last else "├── "
            lines.append(prefix + branch + e.name + ("/" if e.is_dir() else ""))
            if e.is_dir():
                walk(e, prefix + ("    " if last else "│   "), depth + 1)

    if root.is_dir():
        walk(root, "", 1)
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
