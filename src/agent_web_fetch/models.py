"""Data models returned by agent-web-fetch.

Everything is a plain dataclass so results serialise cleanly to JSON for agents.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Machine-readable reasons a fetch could not return real content.
BLOCK_REASONS = {
    "cloudflare_challenge": "Cloudflare / bot-management interstitial ('Just a moment...')",
    "captcha": "A CAPTCHA (reCAPTCHA, hCaptcha, Turnstile, GeeTest, TCaptcha...) is required",
    "verification_wall": "The site asks the client to verify its environment (e.g. WeChat '环境异常')",
    "login_required": "Content is only visible after signing in",
    "paywall": "Content is behind a subscription paywall",
    "rate_limited": "The server is throttling us (HTTP 429 / rate-limit page)",
    "forbidden": "HTTP 403 without a recognisable challenge page",
    "not_found": "HTTP 404/410",
    "soft_404": "HTTP 200 but the page says it does not exist",
    "content_removed": "The publisher or platform removed the content",
    "server_error": "HTTP 5xx",
    "network_error": "DNS / TLS / timeout / connection failure",
    "robots_disallowed": "robots.txt disallows this URL and robots mode is 'strict'",
    "js_required": "Page is an empty JavaScript shell; needs a real browser to render",
    "empty_content": "Page loaded but no meaningful content could be extracted",
    "unsupported_content_type": "Response is not HTML/text (PDF, image, binary...)",
}


@dataclass
class FetchAttempt:
    """One rung of the escalation ladder."""

    strategy: str  # e.g. "http:desktop_chrome", "adapter:x-syndication", "amp", "browser:chromium"
    url: str
    result: str  # ok | thin | blocked | error | skipped
    status: Optional[int] = None
    blocked_reason: Optional[str] = None
    detail: Optional[str] = None
    elapsed_ms: int = 0
    bytes: int = 0
    content_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    """Output of one extraction strategy."""

    method: str
    markdown: str = ""
    title: Optional[str] = None
    author: Optional[str] = None
    published: Optional[str] = None
    site_name: Optional[str] = None
    description: Optional[str] = None
    weight: float = 1.0
    note: Optional[str] = None

    @property
    def chars(self) -> int:
        from .markdown import markdown_to_text

        return len(markdown_to_text(self.markdown))


@dataclass
class PageResult:
    url: str
    final_url: Optional[str] = None
    status: Optional[int] = None
    ok: bool = False
    title: Optional[str] = None
    author: Optional[str] = None
    published: Optional[str] = None
    site_name: Optional[str] = None
    description: Optional[str] = None
    lang: Optional[str] = None
    content_markdown: str = ""
    content_text: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)
    extraction_method: Optional[str] = None
    extraction_candidates: list[dict[str, Any]] = field(default_factory=list)
    adapter: Optional[str] = None
    feeds: list[str] = field(default_factory=list)
    amp_url: Optional[str] = None
    fetch_attempts: list[FetchAttempt] = field(default_factory=list)
    blocked_reason: Optional[str] = None
    blocked_detail: Optional[str] = None
    robots: Optional[dict[str, Any]] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fetch_attempts"] = [a.to_dict() if isinstance(a, FetchAttempt) else a for a in self.fetch_attempts]
        return d
