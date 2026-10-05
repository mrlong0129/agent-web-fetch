import asyncio
import json
from pathlib import Path

from agent_web_fetch.cli import main

FIX = Path(__file__).parent / "fixtures"


def test_cli_extract_json(capsys):
    rc = main(["extract", str(FIX / "jsonld_article.html"), "--url", "https://news.example.com/a", "-f", "json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    for k in ("url", "final_url", "status", "title", "author", "published", "site_name", "description", "content_markdown",
              "content_text", "links", "images", "extraction_method", "fetch_attempts", "blocked_reason"):
        assert k in out
    assert out["fetch_attempts"][0]["strategy"] == "offline:html"


def test_cli_blocked_exit_code(capsys):
    rc = main(["extract", str(FIX / "login_wall.html"), "--url", "https://c.example.com/t", "-f", "md"])
    assert rc == 2
    assert "login_required" in capsys.readouterr().err


def test_mcp_tools_registered():
    from agent_web_fetch.mcp_server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {"fetch_page", "extract_from_html"} <= names


def test_mcp_extract_tool():
    from agent_web_fetch.mcp_server import extract_from_html

    html = (FIX / "nextjs_page.html").read_text()
    d = asyncio.run(extract_from_html(html, "https://dev.example.com/blog/edge-functions", max_chars=100))
    assert d["ok"] and d["extraction_method"] == "js_state" and d["truncated"] is True
    assert len(d["content_markdown"]) == 100
