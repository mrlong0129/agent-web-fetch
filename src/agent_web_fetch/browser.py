"""Optional headless-browser rung (Playwright). Install with:

    pip install "agent-web-fetch[browser]" && playwright install chromium

If Playwright is missing, the pipeline records a ``skipped`` attempt and moves on.
The browser renders the page like a normal visitor would. It does NOT solve
CAPTCHAs or interactive challenges; if one is still shown after rendering, the
result reports it.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from .http import Response
from .profiles import PROFILES

INSTALL_HINT = "pip install 'agent-web-fetch[browser]' && playwright install chromium"


def browser_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401

        return True
    except Exception:
        return False


def render(url: str, *, timeout: float = 30.0, profile: str = "desktop_chrome", locale: str = "en-US") -> Response:
    from playwright.sync_api import sync_playwright

    t0 = time.monotonic()
    ua = PROFILES.get(profile, PROFILES["desktop_chrome"])["User-Agent"]
    mobile = "iPhone" in ua or "Android" in ua
    with sync_playwright() as p:
        browser = None
        used = None
        errors = []
        channel = os.environ.get("AWF_BROWSER_CHANNEL")
        for kw in ([{"channel": channel}] if channel else []) + [{}, {"channel": "chrome"}, {"channel": "msedge"}]:
            try:
                browser = p.chromium.launch(headless=True, **kw)
                used = kw.get("channel") or "chromium"
                break
            except Exception as e:  # browser binary missing -> try the next one
                errors.append(str(e).splitlines()[0][:160])
        if browser is None:
            return Response(url=url, final_url=url, status=None, error="no browser binary: " + " | ".join(errors) + f" (run: {INSTALL_HINT})",
                            elapsed_ms=int((time.monotonic() - t0) * 1000))
        try:
            ctx = browser.new_context(
                user_agent=ua, locale=locale, is_mobile=mobile, has_touch=mobile,
                viewport={"width": 390, "height": 844} if mobile else {"width": 1366, "height": 900},
            )
            page = ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            try:
                page.wait_for_load_state("networkidle", timeout=min(15000, int(timeout * 1000)))
            except Exception:
                pass
            try:  # trigger lazy-loaded content
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)
            except Exception:
                pass
            html = ""
            for _ in range(4):  # client-side redirects can race page.content()
                try:
                    html = page.content()
                    break
                except Exception:
                    try:
                        page.wait_for_load_state("load", timeout=5000)
                    except Exception:
                        pass
                    page.wait_for_timeout(1000)
            return Response(
                url=url, final_url=page.url, status=resp.status if resp else None, error=None if html else "empty DOM after rendering",
                headers=dict(resp.headers) if resp else {}, text=html, content_type="text/html",
                nbytes=len(html.encode("utf-8", "ignore")), elapsed_ms=int((time.monotonic() - t0) * 1000), engine=used,
            )
        except Exception as e:
            return Response(url=url, final_url=url, status=None, error=f"{e.__class__.__name__}: {str(e)[:300]}",
                            elapsed_ms=int((time.monotonic() - t0) * 1000))
        finally:
            browser.close()
