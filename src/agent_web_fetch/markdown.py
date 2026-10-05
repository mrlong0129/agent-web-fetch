"""Tiny, dependency-free (lxml only) HTML -> Markdown converter and helpers.

Good enough for article bodies: headings, paragraphs, emphasis, links, images,
lists, blockquotes, code, tables (flattened), line breaks.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from lxml import html as lxml_html

_SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "form", "button", "input", "select", "textarea", "head"}
_BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "aside", "figure", "figcaption", "table", "tr", "dl", "dt", "dd", "center", "nav"}


def _clean_ws(s: str) -> str:
    return re.sub(r"[ \t\r\f\v\u00a0\u200b]+", " ", s)


class _Conv:
    def __init__(self, base_url: Optional[str]):
        self.base = base_url

    def abs(self, u: Optional[str]) -> str:
        if not u:
            return ""
        u = u.strip()
        if u.startswith(("javascript:", "data:", "#")):
            return ""
        return urljoin(self.base, u) if self.base else u

    def children(self, el, ctx) -> str:
        out = []
        if el.text:
            out.append(el.text if ctx.get("pre") else _clean_ws(el.text))
        for ch in el:
            out.append(self.node(ch, ctx))
            if ch.tail:
                out.append(ch.tail if ctx.get("pre") else _clean_ws(ch.tail))
        return "".join(out)

    def node(self, el, ctx) -> str:
        tag = el.tag if isinstance(el.tag, str) else ""
        tag = tag.lower()
        if not tag or tag in _SKIP:
            return ""
        style = (el.get("style") or "").replace(" ", "").lower()
        if "display:none" in style and tag not in ("img",):
            return ""
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            inner = self.children(el, ctx).strip()
            return f"\n\n{'#' * int(tag[1])} {inner}\n\n" if inner else ""
        if tag == "br":
            return "  \n"
        if tag == "hr":
            return "\n\n---\n\n"
        if tag in ("strong", "b"):
            inner = self.children(el, ctx)
            return f"**{inner.strip()}**" if inner.strip() else inner
        if tag in ("em", "i"):
            inner = self.children(el, ctx)
            return f"*{inner.strip()}*" if inner.strip() else inner
        if tag == "code" and not ctx.get("pre"):
            inner = el.text_content()
            return f"`{inner}`" if inner.strip() else ""
        if tag == "pre":
            code = el.text_content().rstrip("\n")
            return f"\n\n```\n{code}\n```\n\n"
        if tag == "a":
            inner = self.children(el, ctx).strip()
            href = self.abs(el.get("href"))
            if not inner:
                return ""
            return f"[{inner}]({href})" if href else inner
        if tag == "img":
            src = self.abs(el.get("data-src") or el.get("data-original") or el.get("src"))
            if not src:
                return ""
            alt = (el.get("alt") or "").strip().replace("]", "")
            return f"![{alt}]({src})"
        if tag in ("ul", "ol"):
            items = []
            n = 0
            depth = ctx.get("depth", 0)
            for li in el:
                if not isinstance(li.tag, str) or li.tag.lower() != "li":
                    continue
                n += 1
                bullet = f"{n}." if tag == "ol" else "-"
                body = self.children(li, {**ctx, "depth": depth + 1}).strip()
                body = re.sub(r"\n{2,}", "\n", body).replace("\n", "\n" + "  " * (depth + 1))
                items.append("  " * depth + f"{bullet} {body}")
            return "\n\n" + "\n".join(items) + "\n\n" if items else ""
        if tag == "blockquote":
            inner = self.children(el, ctx).strip()
            inner = re.sub(r"\n{3,}", "\n\n", inner)
            return "\n\n" + "\n".join("> " + ln for ln in inner.splitlines()) + "\n\n"
        if tag in ("td", "th"):
            return self.children(el, ctx).strip() + " | "
        if tag == "li":
            return "\n- " + self.children(el, ctx).strip()
        inner = self.children(el, {**ctx, "pre": ctx.get("pre") or tag == "pre"})
        if tag in _BLOCK:
            return f"\n\n{inner}\n\n"
        return inner


def html_to_markdown(fragment: str, base_url: Optional[str] = None) -> str:
    if not fragment or not fragment.strip():
        return ""
    try:
        root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    except Exception:
        try:
            root = lxml_html.fromstring(fragment)
        except Exception:
            return fragment
    md = _Conv(base_url).node(root, {})
    return tidy_markdown(md)


def element_to_markdown(el, base_url: Optional[str] = None) -> str:
    return tidy_markdown(_Conv(base_url).node(el, {}))


def tidy_markdown(md: str) -> str:
    md = re.sub(r"[ \t]+\n", lambda m: "  \n" if m.group(0).startswith("  ") else "\n", md)
    lines = [ln.rstrip() if not ln.endswith("  ") else ln for ln in md.split("\n")]
    md = "\n".join(ln.lstrip() if not re.match(r"^\s*([-*]|\d+\.)\s", ln) else ln for ln in lines)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def markdown_to_text(md: str) -> str:
    if not md:
        return ""
    t = _IMG_RE.sub("", md)
    t = re.sub(r"(?<!!)\[\s*\]\([^)]*\)", "", t)  # links left empty after removing badge images
    t = _LINK_RE.sub(r"\1", t)
    t = re.sub(r"^```.*$", "", t, flags=re.M)
    t = re.sub(r"^\s{0,3}#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def links_from_markdown(md: str, limit: int = 300) -> list[dict[str, str]]:
    seen, out = set(), []
    for text, href in _LINK_RE.findall(md or ""):
        if href in seen or not href.startswith(("http://", "https://")):
            continue
        seen.add(href)
        out.append({"text": text.strip(), "href": href})
        if len(out) >= limit:
            break
    return out


def images_from_markdown(md: str, limit: int = 100) -> list[dict[str, str]]:
    seen, out = set(), []
    for alt, src in _IMG_RE.findall(md or ""):
        if src in seen:
            continue
        seen.add(src)
        out.append({"src": src, "alt": alt.strip()})
        if len(out) >= limit:
            break
    return out


def text_to_markdown(text: str) -> str:
    """Plain text (e.g. JSON-LD articleBody) -> paragraphs."""
    text = (text or "").replace("\r\n", "\n").strip()
    paras: Iterable[str] = re.split(r"\n\s*\n", text)
    return "\n\n".join(p.strip() for p in paras if p.strip())
