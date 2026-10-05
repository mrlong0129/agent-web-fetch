"""MCP server (stdio) exposing agent-web-fetch to Claude Code, Cursor and other agents.

Run:  awf-mcp      (or: awf mcp, or: python -m agent_web_fetch.mcp_server)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Optional

try:  # official MCP Python SDK v2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - SDK v1.x
    from mcp.server.fastmcp import FastMCP as _Server

from . import __version__
from .models import PageResult
from .pipeline import extract_from_html as _extract_from_html
from .pipeline import fetch as _fetch

mcp = _Server(
    "agent-web-fetch",
    instructions=(
        "Read public web pages for an AI agent. fetch_page escalates from a plain HTTP request to "
        "client rotation, alternate versions (AMP/RSS) and an optional headless browser, only when needed. "
        "If a page cannot be read, the result has ok=false and blocked_reason (cloudflare_challenge, captcha, "
        "verification_wall, login_required, paywall, rate_limited, not_found, ...). Do not try to bypass logins, "
        "paywalls or CAPTCHAs: tell the user and ask them to open the page themselves or provide the content."
    ),
)


def _shape(r: PageResult, fmt: str, max_chars: int) -> dict[str, Any]:
    d = r.to_dict()
    truncated = False
    for k in ("content_markdown", "content_text"):
        if max_chars and len(d[k]) > max_chars:
            d[k] = d[k][:max_chars]
            truncated = True
    if fmt == "markdown":
        d.pop("content_text", None)
    elif fmt == "text":
        d.pop("content_markdown", None)
    d["links"] = d["links"][:50]
    d["images"] = d["images"][:20]
    d["truncated"] = truncated
    return d


@mcp.tool()
async def fetch_page(
    url: str,
    format: Literal["markdown", "text", "both"] = "markdown",
    use_browser: Literal["auto", "never", "always"] = "auto",
    adapter: str = "auto",
    max_chars: int = 40000,
    min_chars: int = 200,
) -> dict[str, Any]:
    """Fetch a public web page and return clean content plus metadata.

    Escalation: site adapter API -> HTTP with realistic headers -> client/UA rotation ->
    AMP/RSS alternates -> headless browser (if installed). Every rung is listed in
    fetch_attempts. On failure ok=false and blocked_reason explains why (e.g.
    cloudflare_challenge, captcha, verification_wall, login_required, paywall,
    rate_limited, not_found, soft_404, js_required).

    Args:
        url: page URL (http/https).
        format: which content field(s) to return.
        use_browser: auto (only when cheaper rungs fail), never, or always.
        adapter: auto, none, wechat, github, x.
        max_chars: truncate content to this many characters (0 = no limit).
        min_chars: extracted length that counts as success.
    """
    r = await asyncio.to_thread(_fetch, url, adapter=adapter, browser=use_browser, min_chars=min_chars)
    return _shape(r, format, max_chars)


@mcp.tool()
async def extract_from_html(
    html: str,
    url: Optional[str] = None,
    format: Literal["markdown", "text", "both"] = "markdown",
    adapter: str = "auto",
    max_chars: int = 40000,
) -> dict[str, Any]:
    """Extract main content + metadata from HTML you already have (no network).

    Useful when the user pasted HTML, or another tool (a browser) already loaded the page.
    Runs the same block detection and multi-strategy extraction as fetch_page.
    """
    r = await asyncio.to_thread(_extract_from_html, html, url, adapter=adapter)
    return _shape(r, format, max_chars)


def main() -> None:
    for name in ("httpx", "trafilatura", "readability"):
        logging.getLogger(name).setLevel(logging.ERROR)  # keep stderr quiet
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
