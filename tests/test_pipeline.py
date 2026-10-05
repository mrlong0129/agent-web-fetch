"""Escalation-ladder tests with a mock network (no internet needed)."""
import json

import httpx
import pytest

from agent_web_fetch import fetch
from agent_web_fetch.adapters.x import syndication_token


def make_transport(routes):
    """routes: callable(request) -> httpx.Response"""
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return routes(request)

    t = httpx.MockTransport(handler)
    t.calls = calls
    return t


def kw(t):
    return dict(transport=t, min_interval=0, robots="ignore", browser="never")


def ua(req):
    return req.headers.get("user-agent", "")


def test_escalates_from_cloudflare_to_mobile(fixture):
    article = fixture("normal_article.html")
    cf = fixture("cloudflare_challenge.html")

    def routes(req):
        if "iPhone" in ua(req):
            return httpx.Response(200, text=article, headers={"content-type": "text/html"})
        return httpx.Response(403, text=cf, headers={"content-type": "text/html", "server": "cloudflare", "cf-mitigated": "challenge"})

    t = make_transport(routes)
    r = fetch("https://blog.example.com/posts/agents-read-the-web", **kw(t))
    assert r.ok
    strategies = [(a.strategy, a.result, a.blocked_reason) for a in r.fetch_attempts]
    assert strategies[0] == ("http:desktop_chrome", "blocked", "cloudflare_challenge")
    assert strategies[1][0] == "http:mobile_safari+referer" and strategies[1][1] == "ok"
    assert t.calls[1].headers["referer"] == "https://blog.example.com/"
    assert r.blocked_reason is None and any("earlier rung was blocked" in w for w in r.warnings)


def test_all_blocked_reports_reason(fixture):
    cf = fixture("cloudflare_challenge.html")
    t = make_transport(lambda req: httpx.Response(403, text=cf, headers={"content-type": "text/html", "cf-mitigated": "challenge"}))
    r = fetch("https://example.com/x", **kw(t))
    assert not r.ok and r.blocked_reason == "cloudflare_challenge"
    assert len(r.fetch_attempts) == 3
    assert all(a.result == "blocked" for a in r.fetch_attempts)


def test_login_wall_stops_escalation(fixture):
    t = make_transport(lambda req: httpx.Response(200, text=fixture("login_wall.html"), headers={"content-type": "text/html"}))
    r = fetch("https://community.example.com/t/1", **kw(t))
    assert r.blocked_reason == "login_required"
    assert len(r.fetch_attempts) == 1  # non-retryable: rotating clients would not help


def test_wechat_uses_inapp_ua_and_detects_verification(fixture):
    verify, post = fixture("wechat_verify.html"), fixture("wechat_text_post.html")

    def routes(req):
        # pretend WeChat verifies the in-app UA but the mobile Safari rung gets through
        if "MicroMessenger" in ua(req):
            return httpx.Response(200, text=verify, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(200, text=post, headers={"content-type": "text/html; charset=utf-8"})

    t = make_transport(routes)
    r = fetch("https://mp.weixin.qq.com/s/ExampleTextPost01", **kw(t))
    assert "MicroMessenger" in ua(t.calls[0])  # in-app UA tried first
    assert t.calls[0].headers["accept-language"].startswith("zh-CN")
    assert r.fetch_attempts[0].blocked_reason == "verification_wall"
    assert r.ok and r.extraction_method.startswith("wechat:")


def test_rate_limit_retry_after(fixture):
    state = {"n": 0}

    def routes(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, text="Too Many Requests", headers={"retry-after": "0"})
        return httpx.Response(200, text=fixture("normal_article.html"), headers={"content-type": "text/html"})

    t = make_transport(routes)
    r = fetch("https://blog.example.com/p", **kw(t))
    assert r.ok and len(t.calls) == 2 and len(r.fetch_attempts) == 1  # retried inside the same rung


def test_feed_fallback_for_js_shell(fixture):
    def routes(req):
        if req.url.path == "/feed.xml":
            return httpx.Response(200, text=fixture("feed.xml"), headers={"content-type": "application/rss+xml"})
        return httpx.Response(200, text=fixture("thin_with_feed.html"), headers={"content-type": "text/html"})

    t = make_transport(routes)
    r = fetch("https://blog.example.com/posts/thin", **kw(t))
    assert r.ok and r.extraction_method == "feed"
    assert "full text of the post" in r.content_text
    assert [a.strategy for a in r.fetch_attempts] == ["http:desktop_chrome", "feed"]


def test_browser_skipped_gracefully(fixture, monkeypatch):
    from agent_web_fetch import browser

    monkeypatch.setattr(browser, "browser_available", lambda: False)
    t = make_transport(lambda req: httpx.Response(200, text="<html><body><div id='root'></div></body></html>", headers={"content-type": "text/html"}))
    r = fetch("https://spa.example.com/", transport=t, min_interval=0, robots="ignore", browser="auto")
    assert not r.ok and r.blocked_reason == "js_required"
    assert r.fetch_attempts[-1].strategy == "browser" and r.fetch_attempts[-1].result == "skipped"


def test_robots_strict(fixture):
    def routes(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /", headers={"content-type": "text/plain"})
        return httpx.Response(200, text=fixture("normal_article.html"), headers={"content-type": "text/html"})

    t = make_transport(routes)
    r = fetch("https://blog.example.com/p", transport=t, min_interval=0, robots="strict", browser="never")
    assert r.blocked_reason == "robots_disallowed"
    r2 = fetch("https://blog.example.com/p", transport=t, min_interval=0, robots="warn", browser="never")
    assert r2.ok and r2.robots["allowed"] is False and any("robots.txt" in w for w in r2.warnings)


def test_x_adapter_syndication():
    payload = {"__typename": "Tweet", "id_str": "20", "text": "Placeholder post text for tests https://t.co/abc", "created_at": "2026-01-01T00:00:00.000Z",
               "lang": "en", "favorite_count": 1, "entities": {"urls": [{"url": "https://t.co/abc", "expanded_url": "https://example.com/", "display_url": "example.com"}]},
               "user": {"name": "Example User", "screen_name": "example_user"}}

    def routes(req):
        assert req.url.host == "cdn.syndication.twimg.com"
        return httpx.Response(200, text=json.dumps(payload), headers={"content-type": "application/json"})

    t = make_transport(routes)
    r = fetch("https://x.com/example_user/status/20", **kw(t))
    assert r.ok and r.extraction_method == "x-syndication" and r.adapter == "x"
    assert "Placeholder post text for tests" in r.content_text
    assert {"text": "example.com", "href": "https://example.com/"} in r.links


def test_x_profile_is_login_required():
    t = make_transport(lambda req: pytest.fail("should not hit network"))
    r = fetch("https://x.com/HEee1850894", **kw(t))
    assert r.blocked_reason == "login_required"


def test_github_adapter():
    def routes(req):
        if req.url.path == "/repos/octo/demo":
            return httpx.Response(200, json={"default_branch": "main", "description": "Demo repo", "owner": {"login": "octo"}, "created_at": "2020-01-01T00:00:00Z"})
        if req.url.path == "/repos/octo/demo/readme":
            return httpx.Response(200, text="# Demo\n\nThis README explains the demo project in enough words to be meaningful for tests and agents alike, with more text here.", headers={"content-type": "text/plain"})
        return httpx.Response(404)

    t = make_transport(routes)
    r = fetch("https://github.com/octo/demo", **kw(t))
    assert r.ok and r.extraction_method == "github-readme" and r.description == "Demo repo"
    assert r.content_markdown.startswith("# Demo")


def test_syndication_token_shape():
    tok = syndication_token("1966524436040868087")
    assert tok and "." not in tok and "0" not in tok


def test_captcha_stops_immediately():
    page = "<html><head><title>reuters.com</title></head><body><script src='https://ct.captcha-delivery.com/c.js'></script></body></html>"
    t = make_transport(lambda req: httpx.Response(401, text=page, headers={"content-type": "text/html"}))
    r = fetch("https://news.example.com/a", transport=t, min_interval=0, robots="ignore", browser="auto")
    assert r.blocked_reason == "captcha" and len(r.fetch_attempts) == 1  # no rotation, no browser
