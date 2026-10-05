"""Polite HTTP layer: realistic headers, per-host pacing, bounded retry/backoff."""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .detect import parse_retry_after
from .profiles import PROFILES, build_headers


@dataclass
class Response:
    url: str
    final_url: str
    status: Optional[int]
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    content_type: str = ""
    nbytes: int = 0
    elapsed_ms: int = 0
    error: Optional[str] = None
    retries: int = 0
    engine: Optional[str] = None  # browser engine/channel when rendered


def _decode(resp: httpx.Response) -> str:
    raw = resp.content
    ct = resp.headers.get("content-type", "")
    m = re.search(r"charset=([\w\-]+)", ct, re.I)
    enc = m.group(1) if m else None
    if not enc:
        head = raw[:4096].decode("ascii", "ignore")
        mm = re.search(r"<meta[^>]+charset=[\"']?([\w\-]+)", head, re.I)
        enc = mm.group(1) if mm else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class HttpClient:
    """One client per fetch() call so cookies set by an earlier rung carry over,
    like a real browser session would."""

    def __init__(
        self,
        timeout: float = 20.0,
        min_interval: float = 1.0,
        max_retries: int = 2,
        max_retry_after: float = 20.0,
        proxy: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.max_retry_after = max_retry_after
        self._last: dict[str, float] = {}
        kw = dict(timeout=timeout, follow_redirects=True, max_redirects=10)
        if proxy:
            kw["proxy"] = proxy
        if transport is not None:
            kw["transport"] = transport
        self.client = httpx.Client(**kw)
        self._robots: dict[str, Optional[RobotFileParser]] = {}

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------------------------------------------------------------- pacing
    def _pace(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last[host] = time.monotonic()

    # ---------------------------------------------------------------- GET
    def get(
        self,
        url: str,
        profile: str = "desktop_chrome",
        *,
        referer: Optional[str] = None,
        accept_language: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> Response:
        headers = build_headers(profile, url, accept_language=accept_language, referer=referer) if profile in PROFILES else {}
        if extra_headers:
            headers.update(extra_headers)
        attempt = 0
        t0 = time.monotonic()
        while True:
            self._pace(url)
            try:
                r = self.client.get(url, headers=headers)
            except httpx.HTTPError as e:
                if attempt < self.max_retries and isinstance(e, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)):
                    attempt += 1
                    time.sleep(min(8.0, 2 ** attempt) * (0.5 + random.random() / 2))
                    continue
                return Response(url=url, final_url=url, status=None, error=f"{e.__class__.__name__}: {e}",
                                elapsed_ms=int((time.monotonic() - t0) * 1000), retries=attempt)
            # Polite retry for transient throttling / overload.
            if r.status_code in (429, 502, 503, 504) and attempt < self.max_retries:
                ra = parse_retry_after(r.headers.get("retry-after"))
                is_cf_challenge = r.headers.get("cf-mitigated", "").lower() == "challenge"
                if not is_cf_challenge and (ra is None or ra <= self.max_retry_after):
                    attempt += 1
                    time.sleep(ra if ra is not None else min(8.0, 2 ** attempt) * (0.5 + random.random() / 2))
                    continue
            ct = r.headers.get("content-type", "")
            text = _decode(r) if (not ct or re.search(r"text/|html|xml|json|javascript", ct, re.I)) else ""
            return Response(
                url=url, final_url=str(r.url), status=r.status_code, headers=dict(r.headers), text=text,
                content_type=ct, nbytes=len(r.content), elapsed_ms=int((time.monotonic() - t0) * 1000), retries=attempt,
            )

    # ---------------------------------------------------------------- robots.txt
    def robots_allowed(self, url: str, user_agent: str = "agent-web-fetch") -> Optional[bool]:
        p = urlsplit(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp: Optional[RobotFileParser] = RobotFileParser()
            try:
                r = self.client.get(base + "/robots.txt", headers={"User-Agent": PROFILES["agent"]["User-Agent"]}, timeout=8.0)
                if r.status_code == 200 and "text" in r.headers.get("content-type", "text"):
                    rp.parse(r.text.splitlines())
                elif r.status_code in (401, 403):
                    rp.disallow_all = True  # type: ignore[attr-defined]
                else:
                    rp.allow_all = True  # type: ignore[attr-defined]
            except Exception:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        if rp is None:
            return None
        return rp.can_fetch(user_agent, url)
