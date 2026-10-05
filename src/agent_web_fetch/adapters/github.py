"""GitHub repositories and files via public endpoints (no login).

- ``github.com/{owner}/{repo}``            -> README (raw markdown) + repo metadata
- ``github.com/{owner}/{repo}/blob/{ref}/{path}`` -> raw file from raw.githubusercontent.com

Unauthenticated GitHub API calls are limited (60/hour per IP). If ``GITHUB_TOKEN``
is set in the environment it is sent, which raises the limit; it is never required.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional
from urllib.parse import urlsplit

from .base import Adapter

_RESERVED = {"orgs", "settings", "marketplace", "topics", "explore", "features", "pricing", "about", "login", "join",
             "sponsors", "collections", "trending", "notifications", "search", "enterprise", "apps", "site", "security"}


class GitHubAdapter(Adapter):
    name = "github"
    description = "GitHub repos/files via api.github.com README + raw.githubusercontent.com (no HTML scraping)"

    def _parse(self, url: str):
        p = urlsplit(url)
        if p.netloc.lower() not in ("github.com", "www.github.com"):
            return None
        parts = [x for x in p.path.split("/") if x]
        if len(parts) < 2 or parts[0].lower() in _RESERVED:
            return None
        owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
        if len(parts) == 2 or (len(parts) >= 3 and parts[2] == "tree" and len(parts) <= 4):
            return {"kind": "repo", "owner": owner, "repo": repo}
        if len(parts) >= 5 and parts[2] == "blob":
            return {"kind": "blob", "owner": owner, "repo": repo, "ref": parts[3], "path": "/".join(parts[4:])}
        return None

    def matches(self, url: str) -> bool:
        return self._parse(url) is not None

    def api_fetch(self, url, http, result) -> bool:
        from ..models import FetchAttempt

        info = self._parse(url)
        if not info:
            return False
        o, r = info["owner"], info["repo"]
        api_headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        tok = os.environ.get("GITHUB_TOKEN")
        if tok:
            api_headers["Authorization"] = f"Bearer {tok}"

        meta = {}
        resp = http.get(f"https://api.github.com/repos/{o}/{r}", "agent", extra_headers=api_headers)
        detail = resp.error
        if resp.status in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
            detail = "GitHub API rate limit exhausted for this IP (set GITHUB_TOKEN to raise it); falling back to raw.githubusercontent.com"
        elif resp.status and resp.status != 200:
            detail = resp.text[:120]
        result.fetch_attempts.append(FetchAttempt("adapter:github-api", resp.url, "ok" if resp.status == 200 else "error",
                                                  resp.status, detail=detail, elapsed_ms=resp.elapsed_ms, bytes=resp.nbytes))
        if resp.status == 200:
            try:
                meta = json.loads(resp.text)
            except Exception:
                meta = {}

        if info["kind"] == "blob":
            raw_url = f"https://raw.githubusercontent.com/{o}/{r}/{info['ref']}/{info['path']}"
            label = "adapter:github-raw"
        elif meta.get("default_branch"):
            raw_url = None
            label = "adapter:github-readme"
        else:
            raw_url = f"https://raw.githubusercontent.com/{o}/{r}/HEAD/README.md"
            label = "adapter:github-raw"

        if raw_url is None:
            h = dict(api_headers, Accept="application/vnd.github.raw")
            resp = http.get(f"https://api.github.com/repos/{o}/{r}/readme", "agent", extra_headers=h)
        else:
            resp = http.get(raw_url, "agent")
        ok = resp.status == 200 and bool(resp.text.strip())
        result.fetch_attempts.append(FetchAttempt(label, resp.url, "ok" if ok else "error", resp.status, detail=resp.error,
                                                  elapsed_ms=resp.elapsed_ms, bytes=resp.nbytes, content_chars=len(resp.text) if ok else 0))
        if not ok:
            return False
        body = resp.text
        path = info.get("path", "")
        if path and not re.search(r"\.(md|markdown|mdx|txt|rst)$", path, re.I):
            lang = path.rsplit(".", 1)[-1] if "." in path else ""
            body = f"```{lang}\n{body.rstrip()}\n```"
        result.final_url = url
        result.status = 200
        result.title = f"{o}/{r}" + (f": {path}" if path else "")
        result.author = (meta.get("owner") or {}).get("login") or o
        result.published = meta.get("created_at")
        result.site_name = "GitHub"
        result.description = meta.get("description")
        result.content_markdown = body
        result.fetch_attempts[-1].content_chars = len(body)
        result.extraction_method = label.replace("adapter:", "")
        return True
