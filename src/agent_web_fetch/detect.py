"""Block / wall / soft-404 detection.

Given a response, decide whether we actually got the content, and if not,
*why* not. Agents need a precise reason so they can decide what to do next
(retry later, ask the user to log in, open a real browser, give up).

Keyword checks only fire when the page is also "thin" (little visible text),
so real articles that merely *mention* captchas or logins are not misflagged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlsplit

from lxml import html as lxml_html


@dataclass
class Verdict:
    reason: str
    detail: str
    retryable: bool  # would a different client profile / later retry plausibly help?


def visible_text(html: str) -> str:
    try:
        root = lxml_html.fromstring(html)
    except Exception:
        return ""
    for bad in root.xpath("//script|//style|//noscript|//template|//svg|//head"):
        bad.drop_tree()
    return re.sub(r"\s+", " ", root.text_content()).strip()


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


_CF_MARKERS = ("cf-browser-verification", "cf_chl_opt", "challenge-platform", "cf-challenge", "__cf_chl_", "cdn-cgi/challenge-platform")
_CAPTCHA_MARKERS = (
    ("g-recaptcha", "reCAPTCHA"),
    ("www.google.com/recaptcha", "reCAPTCHA"),
    ("h-captcha", "hCaptcha"),
    ("hcaptcha.com", "hCaptcha"),
    ("challenges.cloudflare.com/turnstile", "Cloudflare Turnstile"),
    ("cf-turnstile", "Cloudflare Turnstile"),
    ("geetest", "GeeTest"),
    ("tcaptcha", "Tencent TCaptcha"),
    ("captcha-delivery.com", "DataDome"),
    ("px-captcha", "PerimeterX/HUMAN"),
    ("arkoselabs", "Arkose Labs"),
    ("funcaptcha", "Arkose FunCaptcha"),
)
_LOGIN_URL = re.compile(r"/(login|signin|sign-in|sign_in|accounts/login|auth/login|passport|session/new|uas/login)\b", re.I)
_LOGIN_TEXT = re.compile(
    r"(sign in to (continue|view|see|read)|log in to (continue|view|see|read)|please (log|sign) in|you must be logged in|"
    r"login required|create an account to (continue|read)|join to (view|see)|"
    r"请登录|登录后(查看|阅读|继续)|登录以(查看|继续)|请先登录)",
    re.I,
)
_PAYWALL_TEXT = re.compile(
    r"(subscribe to (continue|read|keep reading)|subscribers only|this (article|story|content) is (for|available to) (paid )?subscribers|"
    r"already a subscriber|start your (free )?trial to (continue|read)|to continue reading,? (please )?subscribe|"
    r"付费(阅读|内容|订阅)|订阅后(查看|阅读)|开通会员)",
    re.I,
)
_PAYWALL_CLASS = re.compile(r"class=\"[^\"]*\b(paywall|tp-modal|piano-|meteredContent|regwall|subscription-wall)\b", re.I)
_RATE_TEXT = re.compile(r"(rate limit(ed)?|too many requests|slow down|请求过于频繁|访问过于频繁)", re.I)
_SOFT404_TITLE = re.compile(r"(\b404\b|not found|page (does not|doesn't) exist|找不到|页面不存在|无法访问|已删除)", re.I)
_REMOVED_TEXT = re.compile(
    r"(该内容已被发布者删除|此内容因违规无法查看|此内容被投诉且经审核涉嫌侵权|该公众号已迁移|内容已被删除|"
    r"this (post|tweet|video|content) (has been|was) (removed|deleted)|account (has been )?suspended)",
    re.I,
)
_VERIFY_TEXT = re.compile(
    r"(环境异常|完成验证后即可继续访问|unusual traffic|verify (that )?you are (a )?human|are you a robot|"
    r"checking (if the site connection is secure|your browser)|人机验证|安全验证|请完成验证)",
    re.I,
)

_BOTBLOCK_TEXT = re.compile(r"(安全风控|风控策略|security control policy|access denied|request (was )?(rejected|blocked)|访问被拒绝|bot detected)", re.I)

THIN_CHARS = 600  # below this much visible text, keyword heuristics are trusted


def paywall_from_jsonld(html: str) -> bool:
    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        blob = m.group(1)
        if re.search(r'"isAccessibleForFree"\s*:\s*("false"|false|"False")', blob):
            return True
    return False


def classify(
    status: Optional[int],
    headers: Mapping[str, str] | None,
    html: str,
    url: str = "",
    final_url: str = "",
) -> Optional[Verdict]:
    """Return a Verdict if the response is NOT the real content, else None."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    low = html[:400_000].lower() if html else ""
    title = _title(html or "")
    text = visible_text(html) if html else ""
    thin = len(text) < THIN_CHARS
    server = headers.get("server", "").lower()

    # --- Cloudflare / bot-management interstitials -------------------------
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return Verdict("cloudflare_challenge", "cf-mitigated: challenge header", True)
    # Note: Cloudflare injects /cdn-cgi/challenge-platform scripts into many *normal*
    # pages, so markers alone are not enough: require a challenge status or a thin page.
    if any(m in low for m in _CF_MARKERS) and (status in (403, 503) or thin):
        if "cloudflare" in server or "__cf" in low or "cloudflare" in low:
            return Verdict("cloudflare_challenge", f"Cloudflare interstitial (title={title!r}, status={status})", True)
    if title.lower() == "just a moment..." and thin:
        return Verdict("cloudflare_challenge", "'Just a moment...' interstitial", True)

    # --- rate limiting --------------------------------------------------------
    if status == 429:
        ra = headers.get("retry-after")
        return Verdict("rate_limited", f"HTTP 429{f' (Retry-After: {ra})' if ra else ''}", True)

    # --- explicit content removal (often HTTP 200) -----------------------------
    if html and thin and _REMOVED_TEXT.search(text):
        return Verdict("content_removed", f"page says: {_REMOVED_TEXT.search(text).group(0)!r}", False)

    # --- verification walls / captchas ------------------------------------------
    if html and thin:
        vm = _VERIFY_TEXT.search(text) or _VERIFY_TEXT.search(title)
        cap = next((name for marker, name in _CAPTCHA_MARKERS if marker in low), None)
        if vm and cap:
            return Verdict("verification_wall", f"verification page ({vm.group(0)!r}, {cap})", True)
        if vm:
            return Verdict("verification_wall", f"verification page ({vm.group(0)!r})", True)
        if cap:
            # Not retryable: switching clients to dodge a CAPTCHA would be a bypass.
            return Verdict("captcha", f"{cap} challenge on a near-empty page", False)
        if "captcha" in title.lower():
            return Verdict("captcha", f"title {title!r}", False)

    # --- HTTP status families ------------------------------------------------------
    if status in (401, 407):
        return Verdict("login_required", f"HTTP {status}", False)
    if status in (404, 410):
        return Verdict("not_found", f"HTTP {status}", False)
    if status == 451:
        return Verdict("forbidden", "HTTP 451 Unavailable For Legal Reasons", False)

    # --- login walls -----------------------------------------------------------------
    fu = final_url or url
    if fu and url and _LOGIN_URL.search(urlsplit(fu).path) and not _LOGIN_URL.search(urlsplit(url).path):
        return Verdict("login_required", f"redirected to login page {fu}", False)
    if html and thin:
        lm = _LOGIN_TEXT.search(text)
        has_pw = bool(re.search(r"<input[^>]+type=[\"']?password", low))
        if lm or (has_pw and len(text) < 300):
            return Verdict("login_required", f"login wall ({lm.group(0)!r})" if lm else "password form on near-empty page", False)

    # --- paywalls ---------------------------------------------------------------------
    if html:
        pm = _PAYWALL_TEXT.search(text)
        if (pm and (thin or _PAYWALL_CLASS.search(html))) or (paywall_from_jsonld(html) and (thin or pm or _PAYWALL_CLASS.search(html))):
            return Verdict("paywall", f"paywall ({pm.group(0)!r})" if pm else "JSON-LD isAccessibleForFree=false", False)

    if status == 403 or (status and 400 <= status < 500):
        bm = _BOTBLOCK_TEXT.search(text) or _BOTBLOCK_TEXT.search(title)
        hint = f", page says {bm.group(0)!r}" if bm else ""
        return Verdict("forbidden", f"HTTP {status} (server={server or '?'}{hint})", True)
    if status and status >= 500:
        return Verdict("server_error", f"HTTP {status}", True)
    if html and thin and _RATE_TEXT.search(text):
        return Verdict("rate_limited", f"page says {_RATE_TEXT.search(text).group(0)!r}", True)

    # --- soft 404 -------------------------------------------------------------------------
    if status == 200 and html and len(text) < 1500 and _SOFT404_TITLE.search(title):
        return Verdict("soft_404", f"HTTP 200 but title is {title!r}", False)

    return None


def looks_like_js_shell(html: str) -> bool:
    """Lots of script, almost no text, typical SPA mount point -> needs a browser."""
    if not html:
        return False
    text = visible_text(html)
    if len(text) > 400:
        return False
    low = html.lower()
    scripts = low.count("<script")
    mount = re.search(r"<div[^>]+id=[\"'](root|app|__next|__nuxt|svelte|main-app)[\"'][^>]*>\s*</div>", low)
    return bool(mount) or scripts >= 5 or "enable javascript" in low or "需要启用 javascript" in low


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        import time

        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except Exception:
        return None


__all__ = ["Verdict", "classify", "looks_like_js_shell", "visible_text", "parse_retry_after", "paywall_from_jsonld"]
