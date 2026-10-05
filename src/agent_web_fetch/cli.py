"""Command line interface: ``awf fetch <url>``."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import __version__
from .adapters import ADAPTERS
from .models import BLOCK_REASONS, PageResult
from .pipeline import extract_from_html, fetch
from .profiles import PROFILES


def _front_matter(r: PageResult) -> str:
    def q(v):
        return json.dumps(v, ensure_ascii=False) if v is not None else "null"

    lines = ["---", f"title: {q(r.title)}", f"url: {q(r.final_url or r.url)}", f"author: {q(r.author)}",
             f"published: {q(r.published)}", f"site_name: {q(r.site_name)}", f"extraction_method: {q(r.extraction_method)}"]
    if r.blocked_reason:
        lines.append(f"blocked_reason: {q(r.blocked_reason)}")
    lines.append("---")
    return "\n".join(lines)


def render(r: PageResult, fmt: str, max_chars: Optional[int] = None) -> str:
    if fmt == "json":
        d = r.to_dict()
        if max_chars:
            for k in ("content_markdown", "content_text"):
                if len(d[k]) > max_chars:
                    d[k] = d[k][:max_chars]
                    d.setdefault("truncated", True)
        return json.dumps(d, ensure_ascii=False, indent=2)
    body = r.content_text if fmt == "text" else r.content_markdown
    if max_chars and len(body) > max_chars:
        body = body[:max_chars] + "\n\n[...truncated]"
    if fmt == "text":
        head = (r.title + "\n\n") if r.title else ""
        return head + body
    title = f"# {r.title}\n\n" if r.title and not body.lstrip().startswith("# ") else ""
    return f"{_front_matter(r)}\n\n{title}{body}".rstrip() + "\n"


def _report_failure(r: PageResult) -> None:
    print(f"\n[awf] could not read {r.url}", file=sys.stderr)
    print(f"[awf] blocked_reason: {r.blocked_reason} - {BLOCK_REASONS.get(r.blocked_reason or '', '')}", file=sys.stderr)
    if r.blocked_detail:
        print(f"[awf] detail: {r.blocked_detail}", file=sys.stderr)
    for a in r.fetch_attempts:
        print(f"[awf]   {a.strategy:<32} {a.result:<8} status={a.status} {a.blocked_reason or ''} {a.detail or ''}".rstrip(), file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="awf", description="agent-web-fetch: read web pages for AI agents (cheapest first, escalate only when blocked)")
    p.add_argument("--version", action="version", version=f"agent-web-fetch {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="fetch and extract a URL")
    f.add_argument("url")
    f.add_argument("--format", "-f", choices=["md", "json", "text"], default="md")
    g = f.add_mutually_exclusive_group()
    g.add_argument("--browser", dest="browser", action="store_const", const="always", help="go straight to the headless browser")
    g.add_argument("--no-browser", dest="browser", action="store_const", const="never", help="never use the headless browser")
    f.set_defaults(browser="auto")
    f.add_argument("--adapter", default="auto", help=f"auto | none | {' | '.join(a.name for a in ADAPTERS)}")
    f.add_argument("--profile", action="append", choices=sorted(PROFILES), help="client profile(s) to try, in order (repeatable)")
    f.add_argument("--robots", choices=["warn", "strict", "ignore"], default="warn")
    f.add_argument("--timeout", type=float, default=20.0)
    f.add_argument("--min-chars", type=int, default=200, help="content length that counts as success (default 200)")
    f.add_argument("--accept-language")
    f.add_argument("--proxy", help="HTTP(S) proxy URL")
    f.add_argument("--max-chars", type=int, help="truncate content in output")
    f.add_argument("-o", "--output", help="write output to a file")

    e = sub.add_parser("extract", help="extract from a local HTML file (no network)")
    e.add_argument("file", help="HTML file path, or - for stdin")
    e.add_argument("--url", help="original URL (for adapters and absolute links)")
    e.add_argument("--format", "-f", choices=["md", "json", "text"], default="md")
    e.add_argument("--adapter", default="auto")
    e.add_argument("--max-chars", type=int)

    sub.add_parser("adapters", help="list site adapters")
    sub.add_parser("reasons", help="list blocked_reason codes")
    sub.add_parser("mcp", help="run the MCP server on stdio")

    a = p.parse_args(argv)
    import logging

    for name in ("httpx", "trafilatura", "readability"):
        logging.getLogger(name).setLevel(logging.ERROR)
    if a.cmd == "adapters":
        for ad in ADAPTERS:
            print(f"{ad.name:<8} {ad.description}")
        return 0
    if a.cmd == "reasons":
        for k, v in BLOCK_REASONS.items():
            print(f"{k:<26} {v}")
        return 0
    if a.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0
    if a.cmd == "extract":
        html = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8", errors="replace").read()
        r = extract_from_html(html, a.url, adapter=a.adapter)
        out = render(r, a.format, a.max_chars)
    else:
        r = fetch(a.url, adapter=a.adapter, browser=a.browser, profiles=a.profile, timeout=a.timeout, min_chars=a.min_chars,
                  robots=a.robots, accept_language=a.accept_language, proxy=a.proxy)
        out = render(r, a.format, a.max_chars)
    if getattr(a, "output", None):
        with open(a.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if not r.ok:
        if a.format != "json":
            _report_failure(r)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
