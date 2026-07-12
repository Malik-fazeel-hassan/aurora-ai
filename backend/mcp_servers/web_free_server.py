#!/usr/bin/env python3
"""Free web MCP — DuckDuckGo search + plain HTTP fetch (no API keys / no paid rate limits)."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("aurora-web-free")


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo (no API key)."""
    q = (query or "").strip()
    if not q:
        return json.dumps({"ok": False, "error": "empty query"})
    max_results = max(1, min(int(max_results or 5), 10))
    results = []
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True) as client:
            r = client.post("https://lite.duckduckgo.com/lite/", data={"q": q})
            html = r.text
        for m in re.finditer(r'href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
            href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if "duckduckgo.com" in href or not title:
                continue
            results.append({"title": unescape(title)[:200], "url": href})
            if len(results) >= max_results:
                break
        if not results:
            with httpx.Client(timeout=20.0) as client:
                r = client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": q, "format": "json", "no_redirect": 1},
                )
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
                    results.append({"title": topic.get("Text", "")[:160], "url": topic.get("FirstURL") or ""})
        return json.dumps({"ok": True, "query": q, "results": results[:max_results]}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def http_fetch(url: str, max_chars: int = 12000) -> str:
    """Fetch a public http(s) URL and return text (HTML stripped). No API key."""
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return json.dumps({"ok": False, "error": "only http/https"})
    host = urlparse(u).hostname or ""
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
        return json.dumps({"ok": False, "error": "local URLs blocked"})
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(u, headers={"User-Agent": "AuroraMCP/1.0"})
            ctype = r.headers.get("content-type", "")
            text = r.text
        if "html" in ctype or text.lstrip().lower().startswith(("<!doctype", "<html")):
            text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
            text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
            text = re.sub(r"(?is)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        return json.dumps(
            {
                "ok": True,
                "url": u,
                "status": r.status_code,
                "content_type": ctype,
                "truncated": len(text) > max_chars,
                "text": text[:max_chars],
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def http_head(url: str) -> str:
    """HTTP HEAD metadata for a public URL."""
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return json.dumps({"ok": False, "error": "only http/https"})
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            r = client.head(u, headers={"User-Agent": "AuroraMCP/1.0"})
            headers = {k: v for k, v in list(r.headers.items())[:30]}
            return json.dumps({"ok": True, "status": r.status_code, "headers": headers}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


if __name__ == "__main__":
    mcp.run(transport="stdio")
