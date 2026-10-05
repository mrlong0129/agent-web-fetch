"""Realistic client profiles (User-Agent + matching headers).

The point is not to impersonate anyone specific; it is to look like the kind of
ordinary client a page was built for. Many sites serve different HTML to
different clients (WeChat articles, for example, are designed for WeChat's
in-app browser). Each profile sends a coherent, consistent header set.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"

try:  # brotli is optional; only advertise it if we can decode it
    import brotli  # type: ignore  # noqa: F401

    _ENCODINGS = "gzip, deflate, br"
except Exception:  # pragma: no cover
    _ENCODINGS = "gzip, deflate"

PROFILES: dict[str, dict[str, str]] = {
    "desktop_chrome": {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Accept": ACCEPT_HTML,
        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
    "mobile_safari": {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
        "Accept": ACCEPT_HTML,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    },
    "android_chrome": {
        "User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
        "Accept": ACCEPT_HTML,
        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    },
    # WeChat's in-app browser on iOS. WeChat article pages are built for this client.
    "wechat_ios": {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.47(0x18002f2f) NetType/WIFI Language/zh_CN",
        "Accept": ACCEPT_HTML,
    },
    # Honest, self-identifying client for APIs/feeds that ask you to identify yourself.
    "agent": {
        "User-Agent": "agent-web-fetch/0.1 (+https://github.com/mrlong0129/agent-web-fetch)",
        "Accept": "application/json, application/xml, text/html;q=0.9, */*;q=0.5",
    },
}

DEFAULT_ROTATION = ["desktop_chrome", "mobile_safari", "android_chrome"]

DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"


def build_headers(
    profile: str,
    url: str,
    *,
    accept_language: Optional[str] = None,
    referer: Optional[str] = None,
) -> dict[str, str]:
    if profile not in PROFILES:
        raise KeyError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")
    h = dict(PROFILES[profile])
    h["Accept-Language"] = accept_language or DEFAULT_ACCEPT_LANGUAGE
    h["Accept-Encoding"] = _ENCODINGS
    if referer:
        h["Referer"] = referer
        if "Sec-Fetch-Site" in h:
            h["Sec-Fetch-Site"] = "same-origin" if _origin(referer) == _origin(url) else "cross-site"
    return h


def _origin(u: str) -> str:
    p = urlsplit(u)
    return f"{p.scheme}://{p.netloc}"


def origin_of(u: str) -> str:
    return _origin(u) + "/"
