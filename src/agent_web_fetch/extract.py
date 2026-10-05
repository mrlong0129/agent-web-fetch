"""Multi-strategy content extraction.

Every strategy produces a Candidate. We run all of them (they are cheap compared
with a network round-trip), then keep the richest one and record which won.

Strategies
----------
- ``trafilatura``  : readability-style main-content extraction (best general default)
- ``readability``  : Mozilla-Readability port (readability-lxml), independent second opinion
- ``json_ld``      : schema.org ``articleBody`` that publishers embed for search engines
- ``js_state``     : embedded framework state (__NEXT_DATA__, __NUXT__, window.__INITIAL_STATE__...)
- ``meta``         : og:/twitter: description (last resort; usually a summary)
- adapters may add their own candidates (e.g. ``wechat:js_content``)
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional
from urllib.parse import urljoin

from lxml import html as lxml_html

from .markdown import html_to_markdown, markdown_to_text, text_to_markdown
from .models import Candidate

ARTICLE_TYPES = {
    "article", "newsarticle", "blogposting", "techarticle", "report", "scholarlyarticle",
    "socialmediaposting", "liveblogposting", "analysisnewsarticle", "opinionnewsarticle",
    "reportagenewsarticle", "discussionforumposting", "webpage", "howto", "recipe",
}

# Weights express trust when lengths are similar. A JSON-LD articleBody is the
# publisher's own canonical text; a meta description is only a summary.
WEIGHTS = {"adapter": 1.3, "json_ld": 1.1, "trafilatura": 1.0, "readability": 0.95, "js_state": 0.9, "meta": 0.5}


# ----------------------------------------------------------------------------- utils

def _parse(html: str):
    try:
        return lxml_html.fromstring(html)
    except Exception:
        return None


def _first(*vals: Optional[str]) -> Optional[str]:
    for v in vals:
        if isinstance(v, str) and v.strip():
            return re.sub(r"\s+", " ", v).strip()
    return None


# ----------------------------------------------------------------------------- meta

def meta_tags(html: str) -> dict[str, str]:
    """All <meta property|name=... content=...> pairs (first wins), lower-cased keys."""
    out: dict[str, str] = {}
    root = _parse(html)
    if root is None:
        return out
    for m in root.iter("meta"):
        key = (m.get("property") or m.get("name") or m.get("itemprop") or "").strip().lower()
        val = m.get("content")
        if key and val is not None and key not in out:
            out[key] = val
    t = root.find(".//title")
    if t is not None and t.text_content().strip():
        out.setdefault("_title", t.text_content().strip())
    lang = root.get("lang")
    if lang:
        out["_lang"] = lang
    return out


def extract_meta(html: str, url: Optional[str] = None) -> Candidate:
    m = meta_tags(html)
    desc = _first(m.get("og:description"), m.get("twitter:description"), m.get("description"))
    return Candidate(
        method="meta",
        markdown=text_to_markdown(desc or ""),
        title=_first(m.get("og:title"), m.get("twitter:title"), m.get("_title")),
        author=_first(m.get("author"), m.get("article:author"), m.get("twitter:creator"), m.get("parsely-author")),
        published=_first(m.get("article:published_time"), m.get("og:published_time"), m.get("pubdate"), m.get("date"),
                         m.get("parsely-pub-date"), m.get("dc.date")),
        site_name=_first(m.get("og:site_name"), m.get("application-name"), m.get("twitter:site")),
        description=desc,
        weight=WEIGHTS["meta"],
    )


# ----------------------------------------------------------------------------- JSON-LD

def _jsonld_objects(html: str) -> Iterable[dict]:
    for m in re.finditer(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.S | re.I):
        raw = m.group(1).strip()
        raw = re.sub(r"^\s*<!--|-->\s*$", "", raw)
        try:
            data = json.loads(raw)
        except Exception:
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", raw))
            except Exception:
                continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                yield d
                if "@graph" in d:
                    stack.append(d["@graph"])
                if isinstance(d.get("mainEntity"), (dict, list)):
                    stack.append(d["mainEntity"])


def _types(d: dict) -> set[str]:
    t = d.get("@type")
    if isinstance(t, str):
        return {t.lower()}
    if isinstance(t, list):
        return {str(x).lower() for x in t}
    return set()


def _name(v: Any) -> Optional[str]:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("name") if isinstance(v.get("name"), str) else None
    if isinstance(v, list):
        names = [n for n in (_name(x) for x in v) if n]
        return ", ".join(names) if names else None
    return None


def extract_jsonld(html: str, url: Optional[str] = None) -> Candidate:
    best: Optional[dict] = None
    meta_obj: Optional[dict] = None
    for d in _jsonld_objects(html):
        types = _types(d)
        if types & ARTICLE_TYPES:
            if meta_obj is None or (d.get("articleBody") and not (meta_obj or {}).get("articleBody")):
                meta_obj = d
            body = d.get("articleBody") or d.get("text")
            if isinstance(body, str) and (best is None or len(body) > len(best.get("_body", ""))):
                best = dict(d, _body=body)
    src = best or meta_obj or {}
    body = (best or {}).get("_body", "")
    if body and "<" in body and re.search(r"</?(p|div|br|h\d)\b", body, re.I):
        md = html_to_markdown(body, url)
    else:
        md = text_to_markdown(body)
    return Candidate(
        method="json_ld",
        markdown=md,
        title=_first(src.get("headline"), src.get("name")),
        author=_first(_name(src.get("author"))),
        published=_first(src.get("datePublished"), src.get("dateCreated")),
        site_name=_first(_name(src.get("publisher"))),
        description=_first(src.get("description")),
        weight=WEIGHTS["json_ld"],
        note="isAccessibleForFree=false" if str(src.get("isAccessibleForFree", "")).lower() == "false" else None,
    )


# ----------------------------------------------------------------------------- embedded JS state

_STATE_ASSIGN = re.compile(
    r"(?:window\.|var\s+|window\[\"|\b)(__INITIAL_STATE__|__PRELOADED_STATE__|__APOLLO_STATE__|__NUXT__|__INITIAL_DATA__|__DATA__|"
    r"__SERVER_DATA__|__remixContext|ytInitialPlayerResponse|ytInitialData|SIGI_STATE|__UNIVERSAL_DATA_FOR_REHYDRATION__)(?:\"\])?\s*=\s*",
)
_BODY_KEYS = ("articlebody", "body", "content", "contenthtml", "bodyhtml", "html", "markdown", "text", "richtext", "post_content",
              "description", "summary", "shortdescription", "desc")
_TITLE_KEYS = ("headline", "title", "name")
_AUTHOR_KEYS = ("author", "authorname", "byline", "creator")
_DATE_KEYS = ("datepublished", "publishedat", "published_at", "publishdate", "publishedtime", "created_at", "createdat", "date")


def _balanced_json(s: str, start: int) -> Optional[str]:
    """Return the balanced {...} or [...] literal starting at s[start]."""
    if start >= len(s) or s[start] not in "{[":
        return None
    open_c = s[start]
    close_c = "}" if open_c == "{" else "]"
    depth, i, in_str, esc, q = 0, start, False, False, ""
    while i < len(s):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == q:
                in_str = False
        else:
            if c in "\"'":
                in_str, q = True, c
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
        i += 1
    return None


def embedded_states(html: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    m = re.search(r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", html, re.S | re.I)
    if m:
        try:
            out.append(("__NEXT_DATA__", json.loads(m.group(1))))
        except Exception:
            pass
    m = re.search(r"<script[^>]+id=[\"']__NUXT_DATA__[\"'][^>]*>(.*?)</script>", html, re.S | re.I)
    if m:
        try:
            out.append(("__NUXT_DATA__", json.loads(m.group(1))))
        except Exception:
            pass
    for am in _STATE_ASSIGN.finditer(html):
        lit = _balanced_json(html, am.end())
        if not lit:
            continue
        try:
            out.append((am.group(1), json.loads(lit)))
        except Exception:
            try:  # JS literals sometimes use undefined
                out.append((am.group(1), json.loads(re.sub(r"\bundefined\b", "null", lit))))
            except Exception:
                continue
    return out


def _walk(obj: Any, path: str = "", depth: int = 0):
    if depth > 40:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, str(k), v
            yield from _walk(v, p, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:500]):
            yield from _walk(v, f"{path}[{i}]", depth + 1)


def _looks_like_prose(s: str) -> bool:
    if len(s) < 200:
        return False
    if re.match(r"^\s*(https?://|data:|[A-Za-z0-9+/=]{200,}$)", s):
        return False
    letters = sum(ch.isalpha() for ch in s[:2000])
    spaces_or_cjk = sum(ch.isspace() or "\u4e00" <= ch <= "\u9fff" for ch in s[:2000])
    return letters > 0.5 * min(len(s), 2000) * 0.6 and spaces_or_cjk > 0.05 * min(len(s), 2000)


def extract_js_state(html: str, url: Optional[str] = None) -> Candidate:
    best_val, best_path, best_len = None, None, 0
    title = author = published = None
    for name, data in embedded_states(html):
        for path, key, val in _walk(data):
            lk = key.lower()
            if isinstance(val, str):
                if lk in _BODY_KEYS and _looks_like_prose(val):
                    plain = re.sub(r"<[^>]+>", "", val) if "<" in val else val
                    if len(plain) > best_len:
                        best_val, best_path, best_len = val, f"{name}:{path}", len(plain)
                elif lk in _TITLE_KEYS and title is None and 5 < len(val) < 300:
                    title = val
                elif lk in _DATE_KEYS and published is None and re.match(r"\d{4}-\d{2}-\d{2}", val):
                    published = val
                elif lk in _AUTHOR_KEYS and author is None and 1 < len(val) < 120:
                    author = val
            elif lk in _AUTHOR_KEYS and author is None and isinstance(val, dict) and isinstance(val.get("name"), str):
                author = val["name"]
            elif lk in ("blocks", "children", "paragraphs") and isinstance(val, list):
                pass
    md = ""
    if best_val:
        if re.search(r"</?(p|div|br|h\d|ul|li|span)\b", best_val, re.I):
            md = html_to_markdown(best_val, url)
        else:
            md = text_to_markdown(best_val)
    return Candidate(method="js_state", markdown=md, title=title, author=author, published=published,
                     weight=WEIGHTS["js_state"], note=best_path)


# ----------------------------------------------------------------------------- trafilatura / readability

def extract_trafilatura(html: str, url: Optional[str] = None) -> Candidate:
    try:
        import trafilatura
    except Exception:  # pragma: no cover
        return Candidate(method="trafilatura", note="trafilatura not installed")
    md = trafilatura.extract(
        html, url=url, output_format="markdown", include_links=True, include_images=True,
        include_formatting=True, include_tables=True, include_comments=False, favor_recall=True,
        with_metadata=False,
    ) or ""
    c = Candidate(method="trafilatura", markdown=md.strip(), weight=WEIGHTS["trafilatura"])
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        if meta:
            c.title, c.author, c.published = meta.title, meta.author, meta.date
            c.site_name, c.description = meta.sitename, meta.description
    except Exception:
        pass
    return c


def extract_readability(html: str, url: Optional[str] = None) -> Candidate:
    try:
        from readability import Document  # readability-lxml
    except Exception:
        return Candidate(method="readability", note="readability-lxml not installed")
    try:
        doc = Document(html, url=url)
        summary = doc.summary(html_partial=True)
        return Candidate(method="readability", markdown=html_to_markdown(summary, url), title=doc.short_title() or None,
                         weight=WEIGHTS["readability"])
    except Exception as e:
        return Candidate(method="readability", note=f"error: {e.__class__.__name__}")


# ----------------------------------------------------------------------------- discovery

def discover_links(html: str, url: Optional[str] = None) -> dict[str, Any]:
    """Feeds, AMP version, canonical URL."""
    out: dict[str, Any] = {"feeds": [], "amp_url": None, "canonical": None}
    root = _parse(html)
    if root is None:
        return out
    for link in root.iter("link"):
        rel = (link.get("rel") or "").lower().split()
        href = link.get("href")
        if not href:
            continue
        full = urljoin(url or "", href)
        typ = (link.get("type") or "").lower()
        if "alternate" in rel and typ in ("application/rss+xml", "application/atom+xml", "application/feed+json", "application/json+feed"):
            if full not in out["feeds"]:
                out["feeds"].append(full)
        elif "amphtml" in rel:
            out["amp_url"] = out["amp_url"] or full
        elif "canonical" in rel:
            out["canonical"] = out["canonical"] or full
    return out


# ----------------------------------------------------------------------------- orchestration

def score(c: Candidate) -> float:
    return c.chars * c.weight


def run_strategies(html: str, url: Optional[str] = None, extra: Optional[list[Candidate]] = None) -> list[Candidate]:
    cands: list[Candidate] = list(extra or [])
    for fn in (extract_trafilatura, extract_readability, extract_jsonld, extract_js_state, extract_meta):
        try:
            cands.append(fn(html, url))
        except Exception as e:  # never let one strategy break the pipeline
            cands.append(Candidate(method=fn.__name__.replace("extract_", ""), note=f"error: {e.__class__.__name__}: {e}"))
    return cands


def pick_best(cands: list[Candidate]) -> Optional[Candidate]:
    usable = [c for c in cands if c.markdown and c.chars > 0]
    if not usable:
        return None
    return max(usable, key=score)


def merge_metadata(cands: list[Candidate], best: Optional[Candidate]) -> dict[str, Optional[str]]:
    """Field-wise merge: adapter > json_ld > meta > trafilatura > others."""
    order = {"json_ld": 1, "meta": 2, "trafilatura": 3, "readability": 4, "js_state": 5}

    def rank(c: Candidate) -> int:
        return 0 if ":" in c.method or c.method.startswith("adapter") else order.get(c.method, 6)

    ranked = sorted(cands, key=rank)
    out: dict[str, Optional[str]] = {}
    for f in ("title", "author", "published", "site_name", "description"):
        out[f] = next((getattr(c, f) for c in ranked if getattr(c, f)), None)
    return out


def text_of(md: str) -> str:
    return markdown_to_text(md)
