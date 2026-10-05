"""WeChat Official Account articles (mp.weixin.qq.com/s/...).

What we learned the hard way:
1. Requests from datacenter IPs / non-browser clients often get a "环境异常，完成验证后
   即可继续访问" verification page. We *report* that; we never try to solve it.
2. The page is built for WeChat's in-app browser, so the iPhone WeChat UA is the most
   natural client and is tried first.
3. Long articles keep their body in ``#js_content``.
4. Short "text posts" (item_show_type 10) have an empty ``#js_content``; their full
   text lives in the ``content_noencode`` JS variable and (often) in ``og:title``.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import re
from typing import Optional
from urllib.parse import urlsplit

from lxml import html as lxml_html

from ..detect import Verdict, visible_text
from ..markdown import element_to_markdown, html_to_markdown
from ..models import Candidate
from .base import Adapter

_CN_TZ = _dt.timezone(_dt.timedelta(hours=8))


def decode_js_string(s: str) -> str:
    """Decode a single-quoted JS string literal body (\\xHH, \\uHHHH, \\n, \\', ...)."""

    def rep(m: re.Match) -> str:
        e = m.group(1)
        if e[0] == "x":
            return chr(int(e[1:], 16))
        if e[0] == "u":
            return chr(int(e[1:], 16))
        return {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}.get(e, e)

    return re.sub(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", rep, s, flags=re.S)


def js_var(html: str, name: str) -> Optional[str]:
    """Find ``name: '...'`` or ``var name = '...'`` / ``name = "..."`` and decode it."""
    pats = [
        rf"\b{re.escape(name)}\s*[:=]\s*'((?:[^'\\]|\\.)*)'",
        rf'\b{re.escape(name)}\s*[:=]\s*"((?:[^"\\]|\\.)*)"',
    ]
    for p in pats:
        for m in re.finditer(p, html, re.S):
            val = decode_js_string(m.group(1))
            if val.strip():
                return val
    return None


def _text_post_to_markdown(text: str) -> str:
    """content_noencode is plain text with \\n and occasional inline <a> tags."""
    text = text.replace("\r\n", "\n")
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    frag = "".join("<p>" + p.strip().replace("\n", "<br>") + "</p>" for p in paras)
    return html_to_markdown(frag, "https://mp.weixin.qq.com/")


class WeChatAdapter(Adapter):
    name = "wechat"
    description = "WeChat Official Account articles (mp.weixin.qq.com): in-app UA, 环境异常 detection, text-post fallback"
    profiles = ["wechat_ios", "mobile_safari", "desktop_chrome"]
    accept_language = "zh-CN,zh;q=0.9,en;q=0.8"
    trusted_min_chars = 1  # short text posts are complete as-is

    def matches(self, url: str) -> bool:
        return urlsplit(url).netloc.lower() in ("mp.weixin.qq.com", "weixin.qq.com")

    def classify(self, status, headers, html, url) -> Optional[Verdict]:
        if not html:
            return None
        text = visible_text(html)
        has_body = "id=\"js_content\"" in html or "content_noencode" in html
        if not has_body and ("环境异常" in text or "完成验证后即可继续访问" in text):
            return Verdict("verification_wall", "WeChat 环境异常 verification page (needs WeChat client / human verification)", True)
        for pat, reason in (
            ("该内容已被发布者删除", "content_removed"),
            ("此内容因违规无法查看", "content_removed"),
            ("此内容被投诉且经审核涉嫌侵权", "content_removed"),
            ("该公众号已迁移", "content_removed"),
        ):
            if pat in text and len(text) < 2000:
                return Verdict(reason, f"WeChat says: {pat}", False)
        if not has_body and "参数错误" in text and len(text) < 2000:
            return Verdict("not_found", "WeChat says 参数错误 (invalid or expired link)", False)
        return None

    def candidates(self, html: str, url: str) -> list[Candidate]:
        out: list[Candidate] = []
        meta = self._metadata(html)
        try:
            root = lxml_html.fromstring(html)
        except Exception:
            root = None
        # 1) long-form article body
        if root is not None:
            nodes = root.xpath('//*[@id="js_content"]')
            if nodes:
                md = element_to_markdown(nodes[0], "https://mp.weixin.qq.com/")
                if md.strip():
                    out.append(Candidate(method="wechat:js_content", markdown=md, weight=1.3, **meta))
        # 2) short text post: content_noencode
        raw = js_var(html, "content_noencode")
        if raw:
            out.append(Candidate(method="wechat:content_noencode", markdown=_text_post_to_markdown(raw), weight=1.3, **meta))
        # 3) og:title fallback (WeChat puts the whole text of short posts there)
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html)
        if m:
            og = _html.unescape(m.group(1)).replace("\\n", "\n")
            if len(og) > 80:
                out.append(Candidate(method="wechat:og_title", markdown=_text_post_to_markdown(og), weight=1.2, **meta))
        return out

    def _metadata(self, html: str) -> dict:
        title = None
        m = re.search(r'id="activity-name"[^>]*>(.*?)</h1>', html, re.S)
        if m:
            title = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() or None
        if not title:
            t = js_var(html, "window.title") or js_var(html, "msg_title")
            if not t:
                m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html)
                t = _html.unescape(m.group(1)).replace("\\n", "\n") if m else None
            if t:
                first = t.strip().split("\n", 1)[0]
                m2 = re.match(r"^(.{8,100}?[。！？!?])", first)
                title = m2.group(1) if m2 else (first if len(first) <= 80 else first[:80] + "…")
        author = js_var(html, "author")
        if not author:
            m = re.search(r'<meta name="author" content="([^"]+)"', html)
            author = _html.unescape(m.group(1)) if m else None
        nick = js_var(html, "nick_name")
        if not nick:
            m = re.search(r'id="js_name"[^>]*>(.*?)</', html, re.S)
            nick = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
        published = None
        m_epoch = re.search(r"\bori_create_time:\s*'(\d{10})'", html)
        m = re.search(r"create_time:\s*'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})'", html)
        if m_epoch:  # second precision
            published = _dt.datetime.fromtimestamp(int(m_epoch.group(1)), _CN_TZ).isoformat()
        elif m:
            published = m.group(1).replace(" ", "T") + ":00+08:00"
        else:
            m = re.search(r"\b(?:ct|create_time)\s*[:=]\s*['\"](\d{10})['\"]", html)
            if m:
                published = _dt.datetime.fromtimestamp(int(m.group(1)), _CN_TZ).isoformat()
        desc = None
        m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', html)
        if m:
            desc = _html.unescape(m.group(1))
        return {"title": title, "author": author, "site_name": nick or "微信公众平台", "published": published, "description": desc}
