from agent_web_fetch import extract_from_html
from agent_web_fetch.extract import discover_links, embedded_states, extract_jsonld
from agent_web_fetch.markdown import html_to_markdown, markdown_to_text

WX_URL = "https://mp.weixin.qq.com/s/ExampleTextPost01"


def test_wechat_text_post(fixture):
    # Trimmed real page structure (item_show_type=10) with placeholder text and IDs.
    r = extract_from_html(fixture("wechat_text_post.html"), WX_URL)
    assert r.ok and r.adapter == "wechat"
    assert r.extraction_method in ("wechat:content_noencode", "wechat:og_title")
    assert r.author == "示例作者"
    assert r.site_name == "示例公众号"
    assert r.published == "2026-10-05T23:59:51+08:00"
    assert r.title == "这是一条用于测试的示例短内容，正文全部是占位文字。"
    assert "第二段用来检查段落之间的空行有没有保留下来。" in r.content_text
    assert r.content_text.rstrip().endswith("最后一句用来确认结尾没有被截断。")  # not truncated
    assert "& 符号" in r.content_text and "&amp;" not in r.content_text  # entities decoded
    assert "\\n" not in r.content_markdown and "\\x0a" not in r.content_markdown  # escapes decoded
    assert "\n\n第二段" in r.content_markdown  # paragraph breaks kept
    assert [l["text"] for l in r.links] == ["示例链接一", "示例链接二"]
    assert all("MzAwMDAwMDAwMA==" in l["href"] and "&amp;" not in l["href"] for l in r.links)
    # decoys in the fixture must not leak into metadata
    assert "不应被当成" not in (r.title or "") and "不应被当成" not in r.content_text
    methods = {c["method"] for c in r.extraction_candidates}
    assert {"wechat:content_noencode", "wechat:og_title", "trafilatura", "json_ld", "meta"} <= methods


def test_wechat_verify_page_is_reported(fixture):
    r = extract_from_html(fixture("wechat_verify.html"), "https://mp.weixin.qq.com/s/abc")
    assert not r.ok
    assert r.blocked_reason == "verification_wall"
    assert "环境异常" in r.blocked_detail


def test_wechat_long_article_js_content():
    html = """<html><head><meta property="og:title" content="长文标题"></head><body>
    <h1 id="activity-name"> 长文标题 </h1><a id="js_name">测试号</a>
    <div id="js_content" style="visibility:hidden"><p>第一段内容，""" + "很长的正文。" * 60 + """</p>
    <p><img data-src="https://mmbiz.qpic.cn/a.jpg" alt="图"></p><h2>小标题</h2><p>第二段。</p></div></body></html>"""
    r = extract_from_html(html, "https://mp.weixin.qq.com/s/long")
    assert r.ok and r.extraction_method == "wechat:js_content"
    assert r.title == "长文标题" and r.site_name == "测试号"
    assert "## 小标题" in r.content_markdown
    assert r.images and r.images[0]["src"] == "https://mmbiz.qpic.cn/a.jpg"


def test_normal_article(fixture):
    url = "https://blog.example.com/posts/agents-read-the-web"
    r = extract_from_html(fixture("normal_article.html"), url)
    assert r.ok
    assert r.extraction_method in ("trafilatura", "readability")
    assert r.title == "Why Agents Should Read the Web Like Humans"
    assert r.author == "Ada Example"
    assert r.site_name == "Example Blog"
    assert r.published.startswith("2026-09-30")
    assert "Escalate only when blocked" in r.content_text
    assert "© 2026" not in r.content_text  # boilerplate removed
    assert r.feeds == ["https://blog.example.com/feed.xml"]
    assert r.amp_url == "https://blog.example.com/posts/agents-read-the-web/amp"
    assert any(l["href"] == "https://playwright.dev/" for l in r.links)


def test_jsonld_article_beats_teaser(fixture):
    r = extract_from_html(fixture("jsonld_article.html"), "https://news.example.com/ocean-robots")
    assert r.ok and r.extraction_method == "json_ld"
    assert r.title == "Ocean Robots Map the Deep Sea"
    assert r.author == "Kai Reporter"
    assert r.site_name == "Example News"
    assert "hydrothermal vents" in r.content_text
    assert r.content_markdown.count("\n\n") >= 2  # paragraphs preserved


def test_jsonld_graph_parsing(fixture):
    c = extract_jsonld(fixture("jsonld_article.html"))
    assert c.published == "2026-10-01T09:30:00+00:00"


def test_next_data_page(fixture):
    html = fixture("nextjs_page.html")
    states = embedded_states(html)
    assert states and states[0][0] == "__NEXT_DATA__"
    r = extract_from_html(html, "https://dev.example.com/blog/edge-functions")
    assert r.ok and r.extraction_method == "js_state"
    assert "Edge functions run your code" in r.content_text
    assert "## Getting started" in r.content_markdown
    assert r.author == "Lin Dev"
    note = next(c for c in r.extraction_candidates if c["method"] == "js_state")["note"]
    assert note == "__NEXT_DATA__:props.pageProps.post.content"


def test_window_initial_state():
    body = "Paragraph about the topic with plenty of words to look like prose. " * 8
    html = f"<html><body><div id='app'></div><script>window.__INITIAL_STATE__ = {{\"article\": {{\"title\": \"State Title Here\", \"body\": \"{body}\"}}}};</script></body></html>"
    r = extract_from_html(html, "https://spa.example.com/a")
    assert r.ok and r.extraction_method == "js_state" and r.title == "State Title Here"


def test_cloudflare_challenge(fixture):
    r = extract_from_html(fixture("cloudflare_challenge.html"), "https://example.com/x", status=403,
                          headers={"server": "cloudflare", "cf-mitigated": "challenge"})
    assert not r.ok and r.blocked_reason == "cloudflare_challenge"
    # Even without the header, markers + 403 are enough
    r2 = extract_from_html(fixture("cloudflare_challenge.html"), "https://example.com/x", status=403, headers={"server": "cloudflare"})
    assert r2.blocked_reason == "cloudflare_challenge"


def test_cloudflare_script_on_normal_page_is_not_a_block(fixture):
    html = fixture("normal_article.html").replace("</body>", "<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script></body>")
    r = extract_from_html(html, "https://blog.example.com/p", headers={"server": "cloudflare"})
    assert r.ok and r.blocked_reason is None


def test_login_wall(fixture):
    r = extract_from_html(fixture("login_wall.html"), "https://community.example.com/t/123")
    assert not r.ok and r.blocked_reason == "login_required"


def test_paywall(fixture):
    r = extract_from_html(fixture("paywall.html"), "https://times.example.com/markets")
    assert not r.ok and r.blocked_reason == "paywall"


def test_soft_404(fixture):
    r = extract_from_html(fixture("soft_404.html"), "https://example.com/missing")
    assert not r.ok and r.blocked_reason == "soft_404"


def test_article_mentioning_captcha_is_not_blocked(fixture):
    html = fixture("normal_article.html").replace("Start cheap", "Why captcha and login walls exist")
    r = extract_from_html(html, "https://blog.example.com/p")
    assert r.ok


def test_discover_links(fixture):
    d = discover_links(fixture("normal_article.html"), "https://blog.example.com/posts/agents-read-the-web")
    assert d["canonical"] == "https://blog.example.com/posts/agents-read-the-web"


def test_markdown_roundtrip():
    md = html_to_markdown('<h2>T</h2><p>Hello <b>bold</b> <a href="/x">link</a></p><ul><li>a</li><li>b</li></ul><pre>code\n  x</pre>', "https://e.com/")
    assert "## T" in md and "**bold**" in md and "[link](https://e.com/x)" in md and "- a" in md and "```" in md
    assert markdown_to_text(md).startswith("T")


def test_4xx_risk_control_page_is_blocked():
    html = "<html><head><title>出错啦! - bilibili.com</title></head><body>错误号: 412 由于触发哔哩哔哩安全风控策略，该次访问请求被拒绝。</body></html>"
    r = extract_from_html(html, "https://www.bilibili.com/video/x", status=412)
    assert not r.ok and r.blocked_reason == "forbidden" and "412" in r.blocked_detail
