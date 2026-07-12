"""
Curated FREE / open-source MCP catalog for Aurora.

Criteria for inclusion:
- Open source / publicly installable
- Useful for Claude-like agent workflows (code, files, git, browser, data, thinking)
- Prefer local stdio (no vendor cloud billing)
- No hard requirement for paid API keys

Honest limits:
- Local servers: no provider rate limits, but machine CPU/RAM still applies
- Optional community packages may still rate-limit if YOU point them at paid APIs
- Some packages need Node/npx or Python; first launch may download packages
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent
WS = str((BACKEND.parent / "workspace").resolve())
HOME = "/home/user"
PY = "python3"

# category helpers
CAT = {
    "core": "Core local (always free)",
    "files": "Files & workspace",
    "code": "Code & reasoning",
    "git": "Git & version control",
    "browser": "Browser & automation",
    "data": "Data & storage",
    "web": "Web (no API key)",
    "system": "System / shell / desktop",
    "docs": "Docs & content",
    "dev": "Developer utilities",
}


def _p(cmd: str, args: list[str], **extra) -> dict[str, Any]:
    d = {
        "transport": "stdio",
        "command": cmd,
        "args": args,
        "env": {},
        "headers": {},
        "auto_connect": False,
        "enabled": True,
        "free_local": True,
        "needs_api_key": False,
    }
    d.update(extra)
    return d


def build_catalog() -> list[dict[str, Any]]:
    """Return <= 50 extremely useful free MCP server presets."""
    py_servers = BACKEND / "mcp_servers"
    items: list[dict[str, Any]] = []

    def add(item: dict[str, Any]):
        if len(items) >= 50:
            return
        items.append(item)

    # ---- Aurora bundled free servers (no npm download) ----
    add(
        {
            "id": "aurora_demo",
            "name": "Aurora Demo",
            "category": CAT["core"],
            "description": "Built-in demo tools: echo, reverse, time, add (always free).",
            **_p(PY, [str(BACKEND / "mcp_demo_server.py")], auto_connect=True, priority=1),
        }
    )
    add(
        {
            "id": "aurora_utils",
            "name": "Aurora Utils",
            "category": CAT["core"],
            "description": "Hash, base64, JSON, regex, diff, slugify, safe calculator — offline.",
            **_p(PY, [str(py_servers / "utils_server.py")], auto_connect=True, priority=1),
        }
    )
    add(
        {
            "id": "aurora_workspace",
            "name": "Aurora Workspace FS",
            "category": CAT["files"],
            "description": "Sandboxed read/write/list/search/tree for Aurora workspace.",
            **_p(
                PY,
                [str(py_servers / "workspace_server.py")],
                env={"AURORA_WORKSPACE": WS},
                auto_connect=True,
                priority=1,
            ),
        }
    )
    add(
        {
            "id": "aurora_knowledge",
            "name": "Aurora Knowledge DB",
            "category": CAT["data"],
            "description": "Local SQLite notes, todos, key-value memory — no cloud.",
            **_p(PY, [str(py_servers / "knowledge_server.py")], auto_connect=True, priority=1),
        }
    )
    add(
        {
            "id": "aurora_web_free",
            "name": "Aurora Free Web",
            "category": CAT["web"],
            "description": "DuckDuckGo search + HTTP fetch without API keys.",
            **_p(PY, [str(py_servers / "web_free_server.py")], auto_connect=True, priority=1),
        }
    )
    add(
        {
            "id": "aurora_code",
            "name": "Aurora Code Tools",
            "category": CAT["code"],
            "description": "Python outline/syntax check, TODO scan, LOC, markdown TOC.",
            **_p(
                PY,
                [str(py_servers / "code_server.py")],
                env={"AURORA_WORKSPACE": WS},
                auto_connect=True,
                priority=1,
            ),
        }
    )

    # ---- Official / widely used free local MCP (npx / pip) ----
    add(
        {
            "id": "mcp_filesystem",
            "name": "Filesystem (official)",
            "category": CAT["files"],
            "description": "Official MCP filesystem server for sandboxed directories.",
            **_p("npx", ["-y", "@modelcontextprotocol/server-filesystem", WS, HOME], priority=2),
        }
    )
    add(
        {
            "id": "mcp_memory",
            "name": "Memory Graph (official)",
            "category": CAT["data"],
            "description": "Official knowledge-graph memory server (local).",
            **_p("npx", ["-y", "@modelcontextprotocol/server-memory"], priority=2),
        }
    )
    add(
        {
            "id": "mcp_sequential_thinking",
            "name": "Sequential Thinking (official)",
            "category": CAT["code"],
            "description": "Structured multi-step reasoning tool — great for hard problems.",
            **_p("npx", ["-y", "@modelcontextprotocol/server-sequential-thinking"], priority=2),
        }
    )
    add(
        {
            "id": "mcp_everything",
            "name": "Everything (protocol lab)",
            "category": CAT["dev"],
            "description": "Official protocol exercise server (tools/resources/prompts demos).",
            **_p("npx", ["-y", "@modelcontextprotocol/server-everything"], priority=3),
        }
    )
    add(
        {
            "id": "mcp_time_py",
            "name": "Time (Python official)",
            "category": CAT["core"],
            "description": "Timezone-aware time tools via official Python MCP server.",
            **_p(PY, ["-m", "mcp_server_time"], install_hint="pip install mcp-server-time", priority=2),
        }
    )
    add(
        {
            "id": "mcp_git_py",
            "name": "Git (Python official)",
            "category": CAT["git"],
            "description": "Read git status/log/diff and more for a local repo.",
            **_p(
                PY,
                ["-m", "mcp_server_git", "--repository", str(BACKEND.parent)],
                install_hint="pip install mcp-server-git",
                priority=2,
            ),
        }
    )
    add(
        {
            "id": "mcp_fetch_py",
            "name": "Fetch (Python official)",
            "category": CAT["web"],
            "description": "Fetch and extract web content (local server, free).",
            **_p(PY, ["-m", "mcp_server_fetch"], install_hint="pip install mcp-server-fetch", priority=2),
        }
    )
    add(
        {
            "id": "mcp_sqlite_py",
            "name": "SQLite (Python official)",
            "category": CAT["data"],
            "description": "Query/inspect a local SQLite database file.",
            **_p(
                PY,
                ["-m", "mcp_server_sqlite", "--db-path", str(BACKEND.parent / "data" / "aurora_kb.sqlite3")],
                install_hint="pip install mcp-server-sqlite",
                priority=2,
            ),
        }
    )

    # ---- High-value community free local servers ----
    add(
        {
            "id": "git_cyanheads",
            "name": "Git Advanced",
            "category": CAT["git"],
            "description": "Feature-rich local git MCP (branches, commits, diffs).",
            **_p("npx", ["-y", "@cyanheads/git-mcp-server"], priority=2),
        }
    )
    add(
        {
            "id": "fs_cyanheads",
            "name": "Filesystem Advanced",
            "category": CAT["files"],
            "description": "Extended filesystem operations MCP.",
            **_p("npx", ["-y", "@cyanheads/filesystem-mcp-server"], priority=3),
        }
    )
    add(
        {
            "id": "playwright_ms",
            "name": "Playwright Browser",
            "category": CAT["browser"],
            "description": "Microsoft Playwright MCP — browse, click, screenshot, scrape (local).",
            **_p("npx", ["-y", "@playwright/mcp@latest"], priority=1),
        }
    )
    add(
        {
            "id": "playwright_executeautomation",
            "name": "Playwright (ExecuteAutomation)",
            "category": CAT["browser"],
            "description": "Popular Playwright automation MCP for testing/scraping.",
            **_p("npx", ["-y", "@executeautomation/playwright-mcp-server"], priority=2),
        }
    )
    add(
        {
            "id": "playwright_automatalabs",
            "name": "Playwright (AutomataLabs)",
            "category": CAT["browser"],
            "description": "Alternative Playwright MCP server.",
            **_p("npx", ["-y", "@automatalabs/mcp-server-playwright"], priority=3),
        }
    )
    add(
        {
            "id": "puppeteer_official",
            "name": "Puppeteer",
            "category": CAT["browser"],
            "description": "Official Puppeteer browser automation MCP (local Chrome).",
            **_p("npx", ["-y", "@modelcontextprotocol/server-puppeteer"], priority=2),
        }
    )
    add(
        {
            "id": "browser_tools",
            "name": "Browser Tools",
            "category": CAT["browser"],
            "description": "Browser tools MCP for audits/console/network style workflows.",
            **_p("npx", ["-y", "@agentdeskai/browser-tools-mcp"], priority=3),
        }
    )
    add(
        {
            "id": "desktop_commander",
            "name": "Desktop Commander",
            "category": CAT["system"],
            "description": "Powerful local desktop/terminal/file orchestration MCP.",
            **_p("npx", ["-y", "@wonderwhy-er/desktop-commander@latest"], priority=2),
        }
    )
    add(
        {
            "id": "mcp_commands",
            "name": "Shell Commands",
            "category": CAT["system"],
            "description": "Run allowlisted shell commands via MCP.",
            **_p("npx", ["-y", "mcp-server-commands"], priority=2),
        }
    )
    add(
        {
            "id": "shell_mcp",
            "name": "Shell MCP",
            "category": CAT["system"],
            "description": "Lightweight shell execution MCP.",
            **_p("npx", ["-y", "shell-mcp"], priority=3),
        }
    )
    add(
        {
            "id": "mcp_server_shell_py",
            "name": "Shell (Python)",
            "category": CAT["system"],
            "description": "Python shell MCP server.",
            **_p(PY, ["-m", "mcp_server_shell"], install_hint="pip install mcp-server-shell", priority=3),
        }
    )
    add(
        {
            "id": "docker_mcp_py",
            "name": "Docker (Python)",
            "category": CAT["system"],
            "description": "Manage local Docker via MCP (needs Docker daemon).",
            **_p(PY, ["-m", "mcp_server_docker"], install_hint="pip install mcp-server-docker", priority=2),
        }
    )
    add(
        {
            "id": "docker_mcp_npm",
            "name": "Docker (npm)",
            "category": CAT["system"],
            "description": "Docker MCP via npm package.",
            **_p("npx", ["-y", "mcp-server-docker"], priority=3),
        }
    )
    add(
        {
            "id": "sqlite_npm",
            "name": "SQLite (npm)",
            "category": CAT["data"],
            "description": "SQLite MCP server (Node).",
            **_p(
                "npx",
                ["-y", "mcp-sqlite", str(BACKEND.parent / "data" / "aurora_kb.sqlite3")],
                priority=2,
            ),
        }
    )
    add(
        {
            "id": "calculator_mcp",
            "name": "Calculator",
            "category": CAT["dev"],
            "description": "Precise calculator MCP.",
            **_p("npx", ["-y", "calculator-mcp"], priority=2),
        }
    )
    add(
        {
            "id": "calculator_wrtnlabs",
            "name": "Calculator (WrtnLabs)",
            "category": CAT["dev"],
            "description": "Alternative calculator MCP.",
            **_p("npx", ["-y", "@wrtnlabs/calculator-mcp"], priority=3),
        }
    )
    add(
        {
            "id": "mcp_installer",
            "name": "MCP Installer",
            "category": CAT["dev"],
            "description": "Install/manage other MCP servers from chat (Anais Betts).",
            **_p("npx", ["-y", "@anaisbetts/mcp-installer"], priority=2),
        }
    )
    add(
        {
            "id": "youtube_transcript",
            "name": "YouTube Transcript",
            "category": CAT["docs"],
            "description": "Fetch YouTube transcripts locally (no paid API).",
            **_p("npx", ["-y", "@anaisbetts/mcp-youtube"], priority=2),
        }
    )
    add(
        {
            "id": "youtube_data",
            "name": "YouTube Data",
            "category": CAT["docs"],
            "description": "YouTube data helpers MCP.",
            **_p("npx", ["-y", "youtube-data-mcp-server"], priority=3),
        }
    )
    add(
        {
            "id": "screenshot_mcp",
            "name": "Screenshot",
            "category": CAT["browser"],
            "description": "Capture screenshots via MCP.",
            **_p("npx", ["-y", "@kazuph/mcp-screenshot"], priority=3),
        }
    )
    add(
        {
            "id": "mcp_inspector",
            "name": "MCP Inspector",
            "category": CAT["dev"],
            "description": "Official inspector/debug companion for MCP servers.",
            **_p("npx", ["-y", "@modelcontextprotocol/inspector"], priority=3),
        }
    )
    add(
        {
            "id": "context7",
            "name": "Context7 Docs",
            "category": CAT["docs"],
            "description": "Up-to-date library docs lookup for coding agents (free tier community MCP).",
            **_p("npx", ["-y", "@upstash/context7-mcp@latest"], priority=2),
        }
    )
    add(
        {
            "id": "git_npm_simple",
            "name": "Git (npm simple)",
            "category": CAT["git"],
            "description": "Simple git MCP package.",
            **_p("npx", ["-y", "mcp-server-git"], priority=3),
        }
    )
    add(
        {
            "id": "fetch_npm_simple",
            "name": "Fetch (npm simple)",
            "category": CAT["web"],
            "description": "Simple fetch MCP package.",
            **_p("npx", ["-y", "mcp-server-fetch"], priority=3),
        }
    )
    add(
        {
            "id": "smithery_cli",
            "name": "Smithery CLI",
            "category": CAT["dev"],
            "description": "Discover/run community MCP servers via Smithery CLI.",
            **_p("npx", ["-y", "@smithery/cli", "list"], priority=3),
        }
    )
    # Redis local (free if you run local Redis — no cloud fee)
    add(
        {
            "id": "redis_local",
            "name": "Redis (local)",
            "category": CAT["data"],
            "description": "Official Redis MCP — free with local Redis (no Upstash required).",
            **_p(
                "npx",
                ["-y", "@modelcontextprotocol/server-redis", "redis://127.0.0.1:6379"],
                priority=3,
            ),
        }
    )
    # Postgres local
    add(
        {
            "id": "postgres_local",
            "name": "Postgres (local)",
            "category": CAT["data"],
            "description": "Official Postgres MCP — free with local Postgres URL.",
            **_p(
                "npx",
                ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/postgres"],
                priority=3,
            ),
        }
    )
    # Markitdown via python module if installed
    add(
        {
            "id": "markitdown_cli",
            "name": "MarkItDown",
            "category": CAT["docs"],
            "description": "Convert PDF/Office/HTML to markdown via Microsoft markitdown (local).",
            **_p(
                PY,
                ["-c", "from markitdown import MarkItDown; import sys; print(MarkItDown().convert(sys.argv[1]).text_content)"],
                install_hint="pip install markitdown",
                priority=3,
                # This one is awkward as MCP; still list for catalog completeness — prefer code tools
                experimental=True,
            ),
        }
    )
    # HF spaces optional free inference playground (may hit public rate limits — marked)
    add(
        {
            "id": "hfspace",
            "name": "HuggingFace Spaces",
            "category": CAT["dev"],
            "description": "Call public HF Spaces tools (free public endpoints; community limits possible).",
            **_p("npx", ["-y", "@llmindset/mcp-hfspace"], priority=3, needs_api_key=False, note="public free endpoints"),
        }
    )
    add(
        {
            "id": "webcam_mcp",
            "name": "Webcam",
            "category": CAT["system"],
            "description": "Local webcam capture MCP (if hardware available).",
            **_p("npx", ["-y", "@llmindset/mcp-webcam"], priority=3),
        }
    )
    add(
        {
            "id": "macos_automator",
            "name": "macOS Automator",
            "category": CAT["system"],
            "description": "macOS automation MCP (only useful on macOS hosts).",
            **_p("npx", ["-y", "@steipete/macos-automator-mcp"], priority=3, platform="darwin"),
        }
    )
    # Official memory alternative path already included.
    # Extra filesystem scoped to home projects
    add(
        {
            "id": "mcp_filesystem_home",
            "name": "Filesystem (home)",
            "category": CAT["files"],
            "description": "Official filesystem MCP scoped to /home/user.",
            **_p("npx", ["-y", "@modelcontextprotocol/server-filesystem", HOME], priority=2),
        }
    )
    add(
        {
            "id": "mcp_filesystem_workspace",
            "name": "Filesystem (workspace only)",
            "category": CAT["files"],
            "description": "Official filesystem MCP scoped only to Aurora workspace.",
            **_p("npx", ["-y", "@modelcontextprotocol/server-filesystem", WS], priority=1),
        }
    )
    # Sequential thinking docker-less already listed.
    # Additional free code helper: everything + inspector already.
    add(
        {
            "id": "docker_mcp_alt",
            "name": "Docker MCP alt",
            "category": CAT["system"],
            "description": "Alternate docker MCP package name.",
            **_p("npx", ["-y", "docker-mcp"], priority=3),
        }
    )
    add(
        {
            "id": "sqlite_mcp_server_pkg",
            "name": "SQLite server pkg",
            "category": CAT["data"],
            "description": "mcp-server-sqlite npm package.",
            **_p("npx", ["-y", "mcp-server-sqlite"], priority=3),
        }
    )
    add(
        {
            "id": "git_mcp_server_pkg",
            "name": "Git server pkg",
            "category": CAT["git"],
            "description": "mcp-server-git npm package (local).",
            **_p("npx", ["-y", "mcp-server-git"], priority=3),
        }
    )

    # Ensure unique ids and hard cap 50
    seen = set()
    unique = []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        unique.append(it)
        if len(unique) >= 50:
            break
    return unique


def catalog_by_id() -> dict[str, dict[str, Any]]:
    return {x["id"]: x for x in build_catalog()}


def catalog_public() -> list[dict[str, Any]]:
    """UI-safe catalog (no huge env)."""
    out = []
    for x in build_catalog():
        out.append(
            {
                "id": x["id"],
                "name": x["name"],
                "category": x.get("category"),
                "description": x.get("description"),
                "transport": x.get("transport"),
                "command": x.get("command"),
                "args": x.get("args"),
                "free_local": x.get("free_local", True),
                "needs_api_key": x.get("needs_api_key", False),
                "install_hint": x.get("install_hint"),
                "priority": x.get("priority", 3),
                "auto_connect": x.get("auto_connect", False),
                "platform": x.get("platform"),
                "note": x.get("note"),
            }
        )
    return out
