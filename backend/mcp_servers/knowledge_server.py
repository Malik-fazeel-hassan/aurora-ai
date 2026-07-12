#!/usr/bin/env python3
"""Free local knowledge MCP — notes, todos, key-value memory (SQLite, no cloud)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DB = Path(os.environ.get("AURORA_KB_DB", str(Path(__file__).resolve().parents[2] / "data" / "aurora_kb.sqlite3"))).resolve()
DB.parent.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("aurora-knowledge")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS notes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            tags TEXT DEFAULT '',
            created REAL,
            updated REAL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS todos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 1,
            created REAL,
            updated REAL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS kv(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated REAL
        )"""
    )
    c.commit()
    return c


@mcp.tool()
def note_add(title: str, body: str, tags: str = "") -> str:
    """Add a note. tags comma-separated."""
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO notes(title,body,tags,created,updated) VALUES(?,?,?,?,?)",
            (title, body, tags, now, now),
        )
        c.commit()
        return json.dumps({"ok": True, "id": cur.lastrowid})


@mcp.tool()
def note_search(query: str = "", limit: int = 20) -> str:
    """Search notes by title/body/tags substring."""
    q = f"%{(query or '').strip()}%"
    with _conn() as c:
        rows = c.execute(
            "SELECT id,title,tags,substr(body,1,200) AS preview,updated FROM notes "
            "WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? ORDER BY updated DESC LIMIT ?",
            (q, q, q, max(1, min(limit, 100))),
        ).fetchall()
        return json.dumps({"ok": True, "notes": [dict(r) for r in rows]}, ensure_ascii=False, indent=2)


@mcp.tool()
def note_get(note_id: int) -> str:
    """Get full note by id."""
    with _conn() as c:
        r = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if not r:
            return json.dumps({"ok": False, "error": "not found"})
        return json.dumps({"ok": True, "note": dict(r)}, ensure_ascii=False, indent=2)


@mcp.tool()
def note_delete(note_id: int) -> str:
    """Delete a note."""
    with _conn() as c:
        c.execute("DELETE FROM notes WHERE id=?", (note_id,))
        c.commit()
        return json.dumps({"ok": True, "deleted": note_id})


@mcp.tool()
def todo_add(title: str, priority: int = 1) -> str:
    """Add a todo item (priority 1-5)."""
    now = time.time()
    pr = max(1, min(int(priority or 1), 5))
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO todos(title,done,priority,created,updated) VALUES(?,?,?,?,?)",
            (title, 0, pr, now, now),
        )
        c.commit()
        return json.dumps({"ok": True, "id": cur.lastrowid})


@mcp.tool()
def todo_list(include_done: bool = False) -> str:
    """List todos."""
    with _conn() as c:
        if include_done:
            rows = c.execute("SELECT * FROM todos ORDER BY done ASC, priority DESC, id DESC").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM todos WHERE done=0 ORDER BY priority DESC, id DESC"
            ).fetchall()
        return json.dumps({"ok": True, "todos": [dict(r) for r in rows]}, ensure_ascii=False, indent=2)


@mcp.tool()
def todo_done(todo_id: int, done: bool = True) -> str:
    """Mark todo done/undone."""
    with _conn() as c:
        c.execute(
            "UPDATE todos SET done=?, updated=? WHERE id=?",
            (1 if done else 0, time.time(), todo_id),
        )
        c.commit()
        return json.dumps({"ok": True, "id": todo_id, "done": done})


@mcp.tool()
def memory_set(key: str, value: str) -> str:
    """Set a key/value memory entry."""
    with _conn() as c:
        c.execute(
            "INSERT INTO kv(key,value,updated) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (key, value, time.time()),
        )
        c.commit()
        return json.dumps({"ok": True, "key": key})


@mcp.tool()
def memory_get(key: str) -> str:
    """Get a key/value memory entry."""
    with _conn() as c:
        r = c.execute("SELECT key,value,updated FROM kv WHERE key=?", (key,)).fetchone()
        if not r:
            return json.dumps({"ok": False, "error": "not found"})
        return json.dumps({"ok": True, "item": dict(r)}, ensure_ascii=False)


@mcp.tool()
def memory_list(prefix: str = "") -> str:
    """List memory keys (optional prefix)."""
    with _conn() as c:
        if prefix:
            rows = c.execute(
                "SELECT key, substr(value,1,120) AS preview, updated FROM kv WHERE key LIKE ? ORDER BY key",
                (prefix + "%",),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT key, substr(value,1,120) AS preview, updated FROM kv ORDER BY key"
            ).fetchall()
        return json.dumps({"ok": True, "items": [dict(r) for r in rows]}, ensure_ascii=False, indent=2)


@mcp.tool()
def memory_delete(key: str) -> str:
    """Delete a memory key."""
    with _conn() as c:
        c.execute("DELETE FROM kv WHERE key=?", (key,))
        c.commit()
        return json.dumps({"ok": True, "deleted": key})


if __name__ == "__main__":
    mcp.run(transport="stdio")
