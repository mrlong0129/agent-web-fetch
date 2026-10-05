from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..detect import Verdict
from ..models import Candidate
from ..profiles import DEFAULT_ROTATION

if TYPE_CHECKING:  # pragma: no cover
    from ..http import HttpClient
    from ..models import PageResult


class Adapter:
    """Override only what you need.

    - ``profiles``        : client profiles to try, in order (cheapest/most natural first)
    - ``accept_language`` : language the site expects
    - ``api_fetch``       : use a public, login-free endpoint instead of scraping HTML
    - ``classify``        : site-specific block / removal detection (runs before generic)
    - ``candidates``      : site-specific extraction candidates (compete with generic ones)
    """

    name = "base"
    description = ""
    profiles: list[str] = DEFAULT_ROTATION
    accept_language: Optional[str] = None
    # Content from this adapter's own candidates is complete even when short
    # (e.g. a 100-character WeChat text post); None = use the global min_chars.
    trusted_min_chars: Optional[int] = None

    def matches(self, url: str) -> bool:  # pragma: no cover - abstract
        return False

    def api_fetch(self, url: str, http: "HttpClient", result: "PageResult") -> bool:
        """Fill ``result`` from an API and return True, or return False to fall through to HTML."""
        return False

    def classify(self, status: Optional[int], headers: dict, html: str, url: str) -> Optional[Verdict]:
        return None

    def candidates(self, html: str, url: str) -> list[Candidate]:
        return []
