#!/usr/bin/env python3
"""Minimal MCP demo server for Aurora (stdio)."""

from __future__ import annotations

import datetime as dt

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("aurora-demo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo text back."""
    return text


@mcp.tool()
def reverse_text(text: str) -> str:
    """Reverse a string."""
    return text[::-1]


@mcp.tool()
def server_time() -> str:
    """Return current UTC time ISO string."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


@mcp.tool()
def add_numbers(a: float, b: float) -> str:
    """Add two numbers and return the sum as text."""
    return str(a + b)


if __name__ == "__main__":
    mcp.run(transport="stdio")
