"""X / Twitter single posts via public, login-free embed endpoints.

1. ``cdn.syndication.twimg.com/tweet-result`` (the JSON behind embedded tweets)
2. ``publish.twitter.com/oembed`` (official oEmbed) as a fallback

Only individual posts are supported. Timelines, search, replies and X Articles
require login and are reported as ``login_required`` rather than scraped.
"""
from __future__ import annotations

import html as _html
import json
import math
import re
from typing import Optional
from urllib.parse import quote, urlsplit

from ..markdown import html_to_markdown
from .base import Adapter

_STATUS = re.compile(r"^/(?:[^/]+|i/web)/status(?:es)?/(\d+)")


def syndication_token(tweet_id: str) -> str:
    """Port of the token formula used by Twitter's embed JS:
    ((id / 1e15) * Math.PI).toString(36).replace(/(0+|\\.)/g, '')"""
    x = (int(tweet_id) / 1e15) * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    ip, fp = int(x), x - int(x)
    s = ""
    while ip:
        ip, rem = divmod(ip, 36)
        s = digits[rem] + s
    s = s or "0"
    frac = ""
    for _ in range(12):
        fp *= 36
        d = int(fp)
        frac += digits[d]
        fp -= d
        if fp <= 0:
            break
    return re.sub(r"(0+|\.)", "", f"{s}.{frac}") or "a"


class XAdapter(Adapter):
    name = "x"
    description = "Single X/Twitter posts via public syndication JSON, falling back to oEmbed"

    def _id(self, url: str) -> Optional[str]:
        p = urlsplit(url)
        if p.netloc.lower().removeprefix("www.").removeprefix("mobile.") not in ("x.com", "twitter.com"):
            return None
        m = _STATUS.match(p.path)
        return m.group(1) if m else None

    def matches(self, url: str) -> bool:
        p = urlsplit(url)
        return p.netloc.lower().removeprefix("www.").removeprefix("mobile.") in ("x.com", "twitter.com")

    def api_fetch(self, url, http, result) -> bool:
        from ..models import FetchAttempt

        tid = self._id(url)
        if not tid:
            result.fetch_attempts.append(FetchAttempt("adapter:x", url, "skipped", detail="not a single-post URL; X requires login for profiles/timelines"))
            result.blocked_reason = "login_required"
            result.blocked_detail = "X profiles, timelines and search need a logged-in session; only single posts are readable via public embeds"
            return True  # handled: do not hammer x.com HTML (it is a JS shell + login wall)

        api = f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token={syndication_token(tid)}&lang=en"
        r = http.get(api, "agent", extra_headers={"Accept": "application/json"})
        data = None
        if r.status == 200 and r.text.strip().startswith("{"):
            try:
                data = json.loads(r.text)
            except Exception:
                data = None
        good = bool(data and data.get("__typename") in ("Tweet", None) and data.get("text"))
        result.fetch_attempts.append(FetchAttempt("adapter:x-syndication", api, "ok" if good else "error", r.status,
                                                  detail=r.error or (None if good else (r.text[:120] or "empty")),
                                                  elapsed_ms=r.elapsed_ms, bytes=r.nbytes))
        if good:
            self._fill_from_syndication(data, url, result)
            return True

        canon = f"https://twitter.com/i/status/{tid}"
        oe = f"https://publish.twitter.com/oembed?url={quote(canon, safe='')}&omit_script=true&dnt=true"
        r = http.get(oe, "agent", extra_headers={"Accept": "application/json"})
        ok = r.status == 200 and r.text.strip().startswith("{")
        result.fetch_attempts.append(FetchAttempt("adapter:x-oembed", oe, "ok" if ok else "error", r.status,
                                                  detail=r.error, elapsed_ms=r.elapsed_ms, bytes=r.nbytes))
        if ok:
            d = json.loads(r.text)
            md = html_to_markdown(d.get("html", ""), "https://x.com/")
            result.content_markdown = md
            result.author = d.get("author_name")
            result.title = f"{d.get('author_name')} on X"
            result.site_name = "X"
            result.final_url = d.get("url") or url
            result.status = 200
            result.extraction_method = "x-oembed"
            return True
        if r.status == 404:
            result.blocked_reason = "not_found"
            result.blocked_detail = "post not found, deleted, or from a protected account"
            return True
        return False

    def _fill_from_syndication(self, d: dict, url: str, result) -> None:
        user = d.get("user") or {}
        text = d.get("text", "")
        # note_tweet carries the full text of long posts when present
        nt = ((d.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
        if isinstance(nt.get("text"), str) and len(nt["text"]) > len(text):
            text = nt["text"]
        for u in (d.get("entities") or {}).get("urls", []):
            if u.get("url") and u.get("expanded_url"):
                text = text.replace(u["url"], f"[{u.get('display_url') or u['expanded_url']}]({u['expanded_url']})")
        for m in d.get("mediaDetails") or []:
            if m.get("url"):
                text = text.replace(m["url"], "")
        text = _html.unescape(text).strip()
        parts = [text.replace("\n", "  \n")]
        for m in d.get("mediaDetails") or []:
            src = m.get("media_url_https")
            if src:
                parts.append(f"![{m.get('type', 'media')}]({src})")
        q = d.get("quoted_tweet")
        if q and q.get("text"):
            qu = (q.get("user") or {}).get("screen_name", "")
            parts.append("> " + f"@{qu}: " + _html.unescape(q["text"]).replace("\n", "\n> "))
        stats = f"❤ {d.get('favorite_count', 0)} · 💬 {d.get('conversation_count', 0)}"
        parts.append(f"— {user.get('name')} (@{user.get('screen_name')}), {d.get('created_at')} · {stats}")
        result.content_markdown = "\n\n".join(p for p in parts if p)
        result.title = f"{user.get('name')} (@{user.get('screen_name')}) on X"
        result.author = f"{user.get('name')} (@{user.get('screen_name')})"
        result.published = d.get("created_at")
        result.site_name = "X"
        result.lang = d.get("lang")
        result.final_url = f"https://x.com/{user.get('screen_name')}/status/{d.get('id_str')}"
        result.status = 200
        result.extraction_method = "x-syndication"
