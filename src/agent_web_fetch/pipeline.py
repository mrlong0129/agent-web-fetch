"""The escalation ladder: cheapest first, escalate only when blocked.

    0. site adapter API       (public JSON/raw endpoints: GitHub, X)        cheapest, most reliable
    1. plain HTTP GET         (realistic, coherent browser headers)
    2. client rotation        (desktop Chrome -> mobile Safari -> Android / in-app UA, Referer)
    3. detection              (after every rung: block? captcha? login? paywall? soft 404?)
    4. multi-strategy extraction (trafilatura, readability, JSON-LD, JS state, meta, adapter)
       + AMP version and RSS/Atom feed when the HTML is thin
    5. headless browser       (optional Playwright extra)                   most expensive

Non-retryable verdicts (CAPTCHA, login wall, paywall, 404, removed) stop the ladder at once:
changing clients would not change the answer, and we never bypass them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from lxml import etree

from . import browser as _browser
from .adapters import Adapter, get_adapter
from .detect import Verdict, classify, looks_like_js_shell
from .extract import discover_links, merge_metadata, meta_tags, pick_best, run_strategies
from .http import HttpClient, Response
from .markdown import html_to_markdown, images_from_markdown, links_from_markdown, markdown_to_text
from .models import Candidate, FetchAttempt, PageResult
from .profiles import DEFAULT_ROTATION, origin_of


@dataclass
class Extraction:
    best: Optional[Candidate]
    candidates: list[Candidate]
    meta: dict
    discovery: dict
    lang: Optional[str] = None
    js_shell: bool = False
    verdict: Optional[Verdict] = None

    @property
    def chars(self) -> int:
        return self.best.chars if self.best else 0


def _extract(html: str, url: str, adapter: Optional[Adapter]) -> Extraction:
    extra = adapter.candidates(html, url) if adapter else []
    cands = run_strategies(html, url, extra)
    best = pick_best(cands)
    return Extraction(
        best=best, candidates=cands, meta=merge_metadata(cands, best), discovery=discover_links(html, url),
        lang=meta_tags(html).get("_lang"), js_shell=looks_like_js_shell(html),
    )


def _threshold(ad: Optional[Adapter], best: Optional[Candidate], min_chars: int) -> int:
    """Adapter-specific candidates may be trusted even when short."""
    if ad is not None and ad.trusted_min_chars is not None and best is not None and best.method.startswith(ad.name + ":"):
        return ad.trusted_min_chars
    return min_chars


def _apply(res: PageResult, ex: Extraction, resp: Optional[Response]) -> None:
    if resp is not None:
        res.final_url, res.status = resp.final_url, resp.status
    for k, v in ex.meta.items():
        if v and not getattr(res, k):
            setattr(res, k, v)
    res.lang = res.lang or ex.lang
    res.feeds = ex.discovery.get("feeds", []) or res.feeds
    res.amp_url = ex.discovery.get("amp_url") or res.amp_url
    res.extraction_candidates = [
        {"method": c.method, "chars": c.chars, "score": round(c.chars * c.weight, 1), **({"note": c.note} if c.note else {})}
        for c in ex.candidates
    ]
    if ex.best:
        res.content_markdown = ex.best.markdown
        res.extraction_method = ex.best.method


def adapter_rule_ok(res: PageResult) -> bool:
    return bool(res.adapter and (res.extraction_method or "").startswith(res.adapter + ":"))


def _finalize(res: PageResult, min_chars: int) -> PageResult:
    md = res.content_markdown or ""
    res.content_text = markdown_to_text(md)
    res.links = links_from_markdown(md)
    res.images = images_from_markdown(md)
    if res.blocked_reason and res.content_text and (len(res.content_text) >= min_chars or adapter_rule_ok(res)):
        # A later rung succeeded; keep the earlier block only as a warning.
        res.blocked_reason = res.blocked_detail = None
    res.ok = bool(res.content_text) and res.blocked_reason is None
    if res.ok:
        hops = sorted({a.blocked_reason for a in res.fetch_attempts if a.result == "blocked" and a.blocked_reason})
        if hops:
            res.warnings.append(f"earlier rung was blocked ({', '.join(hops)}); recovered by escalating")
    api_rung = bool(res.fetch_attempts) and res.fetch_attempts[-1].strategy.startswith("adapter:")
    adapter_rule = bool(res.adapter and (res.extraction_method or "").startswith(res.adapter + ":"))
    if res.ok and len(res.content_text) < min_chars and not api_rung and not adapter_rule:
        res.warnings.append(f"content is short ({len(res.content_text)} chars); it may be a summary or need a browser")
    return res


def _normalize(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def extract_from_html(html: str, url: Optional[str] = None, *, adapter: Optional[str] = "auto",
                      status: Optional[int] = 200, headers: Optional[dict] = None, min_chars: int = 200) -> PageResult:
    """Run detection + extraction on HTML you already have (no network)."""
    url = url or ""
    ad = get_adapter(adapter, url) if url else (get_adapter(adapter, "") if adapter not in ("auto",) else None)
    res = PageResult(url=url, final_url=url or None, status=status, adapter=ad.name if ad else None)
    verdict = (ad.classify(status, headers or {}, html, url) if ad else None) or classify(status, headers, html, url, url)
    res.fetch_attempts.append(FetchAttempt("offline:html", url, "blocked" if verdict else "ok", status,
                                           blocked_reason=verdict.reason if verdict else None,
                                           detail=verdict.detail if verdict else None, bytes=len(html.encode("utf-8", "ignore"))))
    if verdict:
        res.blocked_reason, res.blocked_detail = verdict.reason, verdict.detail
        return _finalize(res, min_chars)
    ex = _extract(html, url or None, ad)
    _apply(res, ex, None)
    res.fetch_attempts[-1].content_chars = ex.chars
    if not ex.best:
        res.blocked_reason = "js_required" if ex.js_shell else "empty_content"
        res.blocked_detail = "no extractable text in the HTML"
    return _finalize(res, min_chars)


def _feed_entry(xml: str, url: str) -> Optional[tuple[str, Optional[str]]]:
    """Find the feed entry whose link matches url; return (html_or_text, title)."""
    try:
        root = etree.fromstring(xml.encode("utf-8"), parser=etree.XMLParser(recover=True, resolve_entities=False))
    except Exception:
        return None
    norm = lambda u: (u or "").split("#")[0].split("?")[0].rstrip("/")  # noqa: E731
    target = norm(url)
    for item in root.iter("{*}item", "{*}entry", "item", "entry"):
        links = [l.text for l in item.iter("{*}link", "link") if l.text] + [l.get("href") for l in item.iter("{*}link", "link") if l.get("href")]
        guid = [g.text for g in item.iter("{*}guid", "guid", "{*}id", "id") if g.text]
        if target not in {norm(x) for x in links + guid}:
            continue
        body = None
        for tag in ("{http://purl.org/rss/1.0/modules/content/}encoded", "{*}content", "content", "{*}description", "description", "{*}summary", "summary"):
            el = next(iter(item.iter(tag)), None)
            if el is not None and (el.text or len(el)):
                body = el.text if el.text and el.text.strip() else etree.tostring(el, encoding="unicode", method="html")
                if body and len(body) > 100:
                    break
        title_el = next(iter(item.iter("{*}title", "title")), None)
        return (body or "", title_el.text if title_el is not None else None)
    return None


def fetch(
    url: str,
    *,
    adapter: Optional[str] = "auto",
    browser: str = "auto",  # auto | never | always
    profiles: Optional[list[str]] = None,
    timeout: float = 20.0,
    min_chars: int = 200,
    robots: str = "warn",  # warn | strict | ignore
    accept_language: Optional[str] = None,
    min_interval: float = 1.0,
    max_retries: int = 2,
    proxy: Optional[str] = None,
    transport=None,
) -> PageResult:
    """Fetch a URL with the escalation ladder and return a PageResult."""
    url = _normalize(url)
    res = PageResult(url=url)
    ad = get_adapter(adapter, url)
    res.adapter = ad.name if ad else None
    al = accept_language or (ad.accept_language if ad else None)
    if browser not in ("auto", "never", "always"):
        raise ValueError("browser must be auto|never|always")

    with HttpClient(timeout=timeout, min_interval=min_interval, max_retries=max_retries, proxy=proxy, transport=transport) as http:
        # ---- robots.txt --------------------------------------------------------------
        if robots != "ignore":
            allowed = http.robots_allowed(url)
            res.robots = {"mode": robots, "allowed": allowed}
            if allowed is False:
                if robots == "strict":
                    res.blocked_reason, res.blocked_detail = "robots_disallowed", "robots.txt disallows this URL (robots=strict)"
                    res.fetch_attempts.append(FetchAttempt("robots.txt", url, "blocked", blocked_reason="robots_disallowed"))
                    return _finalize(res, min_chars)
                res.warnings.append("robots.txt disallows automated crawling of this URL; fetched once as a user-initiated read. "
                                    "Use robots='strict' for crawls or batch jobs.")

        # ---- rung 0: adapter API ----------------------------------------------------------
        if ad is not None and browser != "always":
            try:
                if ad.api_fetch(url, http, res):
                    return _finalize(res, min_chars)
            except Exception as e:
                res.fetch_attempts.append(FetchAttempt(f"adapter:{ad.name}", url, "error", detail=f"{e.__class__.__name__}: {e}"))

        best: Optional[tuple[Extraction, Response]] = None
        last_verdict: Optional[Verdict] = None
        done = False

        def consider(ex: Extraction, resp: Response) -> None:
            nonlocal best
            if best is None or ex.chars > best[0].chars:
                best = (ex, resp)

        def run_rung(strategy: str, resp: Response) -> bool:
            """Classify + extract one response; return True when content is good enough."""
            nonlocal last_verdict
            att = FetchAttempt(strategy, resp.url, "ok", resp.status, elapsed_ms=resp.elapsed_ms, bytes=resp.nbytes)
            res.fetch_attempts.append(att)
            if resp.error:
                att.result, att.blocked_reason, att.detail = "error", "network_error", resp.error
                last_verdict = last_verdict or Verdict("network_error", resp.error, True)
                return False
            if resp.content_type and not re.search(r"html|xml|text/plain", resp.content_type, re.I):
                att.result, att.blocked_reason, att.detail = "blocked", "unsupported_content_type", resp.content_type
                last_verdict = Verdict("unsupported_content_type", resp.content_type, False)
                return False
            v = (ad.classify(resp.status, resp.headers, resp.text, resp.final_url) if ad else None) or classify(
                resp.status, resp.headers, resp.text, resp.url, resp.final_url)
            if v:
                att.result, att.blocked_reason, att.detail = "blocked", v.reason, v.detail
                last_verdict = v
                return False
            if "text/plain" in (resp.content_type or ""):
                ex = Extraction(best=Candidate("plain_text", markdown=resp.text.strip()), candidates=[], meta={}, discovery={})
            else:
                ex = _extract(resp.text, resp.final_url, ad)
            att.content_chars = ex.chars
            consider(ex, resp)
            if ex.chars >= _threshold(ad, ex.best, min_chars):
                return True
            att.result = "thin"
            att.detail = "JavaScript shell (needs rendering)" if ex.js_shell else f"only {ex.chars} chars extracted"
            return False

        # ---- rungs 1-2: HTTP with client rotation ------------------------------------------------
        if browser != "always":
            rotation = profiles or (ad.profiles if ad else DEFAULT_ROTATION)
            for i, prof in enumerate(rotation):
                referer = origin_of(url) if i > 0 else None
                resp = http.get(url, prof, referer=referer, accept_language=al)
                if run_rung(f"http:{prof}" + ("+referer" if referer else ""), resp):
                    done = True
                    break
                if last_verdict and not last_verdict.retryable:
                    break  # login wall / paywall / 404 / removed: rotation will not help
                if best and best[0].js_shell:
                    break  # same JS shell for every UA; skip to AMP/feed/browser

            # ---- AMP / feed alternates when HTML was thin -------------------------------------------
            if not done and best and not (last_verdict and not last_verdict.retryable):
                amp = best[0].discovery.get("amp_url")
                if amp and amp != url:
                    if run_rung("amp", http.get(amp, "mobile_safari", accept_language=al)):
                        done = True
                if not done:
                    for feed in best[0].discovery.get("feeds", [])[:2]:
                        r = http.get(feed, "agent")
                        att = FetchAttempt("feed", feed, "error", r.status, elapsed_ms=r.elapsed_ms, bytes=r.nbytes, detail=r.error)
                        res.fetch_attempts.append(att)
                        hit = _feed_entry(r.text, url) if r.status == 200 and r.text else None
                        if not hit:
                            att.result, att.detail = "thin", att.detail or "no feed entry matches this URL"
                            continue
                        body, ftitle = hit
                        md = html_to_markdown(body, url) if "<" in body else body
                        cand = Candidate("feed", markdown=md, title=ftitle, weight=1.0)
                        att.result, att.content_chars = ("ok" if cand.chars >= min_chars else "thin"), cand.chars
                        if cand.chars > best[0].chars:
                            ex = best[0]
                            ex.candidates.append(cand)
                            ex.best = cand
                        if cand.chars >= min_chars:
                            done = True
                            break

        # ---- rung 5: headless browser ------------------------------------------------------------------
        want_browser = browser == "always" or (
            browser == "auto" and not done and not (last_verdict and not last_verdict.retryable)
        )
        if want_browser:
            if not _browser.browser_available():
                res.fetch_attempts.append(FetchAttempt("browser", url, "skipped", detail=f"Playwright not installed ({_browser.INSTALL_HINT})"))
            else:
                prof = (ad.profiles[0] if ad else "desktop_chrome")
                rr = _browser.render(url, timeout=max(timeout, 30.0), profile=prof,
                                     locale="zh-CN" if (al or "").startswith("zh") else "en-US")
                if run_rung(f"browser:{rr.engine or 'chromium'}({prof})", rr):
                    done = True

        # ---- assemble -------------------------------------------------------------------------------------
        if best is not None:
            ex, resp = best
            _apply(res, ex, resp)
            if not done and last_verdict and best[0].chars < min_chars:
                res.blocked_reason, res.blocked_detail = last_verdict.reason, last_verdict.detail
        else:
            last = next((a for a in reversed(res.fetch_attempts) if a.status is not None), None)
            res.status = last.status if last else None
            if last_verdict:
                res.blocked_reason, res.blocked_detail = last_verdict.reason, last_verdict.detail
            else:
                res.blocked_reason, res.blocked_detail = "empty_content", "no rung produced extractable content"
        if best is not None and not best[0].best:
            res.blocked_reason = res.blocked_reason or ("js_required" if best[0].js_shell else "empty_content")
        if res.blocked_reason == "empty_content" and best and best[0].js_shell:
            res.blocked_reason = "js_required"
    return _finalize(res, min_chars)
