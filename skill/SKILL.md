---
name: agent-web-fetch
description: Read a public web page reliably and honestly. Use whenever the user gives you a URL to read, summarise, or extract from (articles, blog posts, WeChat 公众号 links, GitHub repos, X posts, JS-heavy pages). Escalates from a plain request to client rotation, structured-data extraction, alternates (AMP/RSS) and a headless browser only when blocked, and reports exactly why a page could not be read instead of bypassing logins, paywalls or CAPTCHAs.
---

# Reading the web: the escalation playbook

Goal: get the real content of a public page with the least effort and the fewest requests, and if you cannot, tell the user precisely why. Never summarise a verification page, login page or error page as if it were the article.

## 0. If the `awf` tool is available, use it

- MCP: call `fetch_page(url)`; for HTML you already have, `extract_from_html(html, url)`.
- CLI: `awf fetch <url> --format json` (or `--format md`). Exit code 2 means blocked/empty; the reason is in `blocked_reason`.
- Not installed but `uv` is available? Run it with zero install/config: `uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf fetch <url> --format json`.
- Check `ok`. If `ok` is false, read `blocked_reason` and `fetch_attempts`, then follow "When you are blocked" below.
- `extraction_method` tells you where the text came from; `meta` means you only got a summary, so say so.

Everything below is the same strategy done by hand (with `curl`, a fetch tool, or a browser tool), for when the package is not installed.

## 1. Use a public API when the site has one (cheapest, most reliable)

- GitHub repo: `https://api.github.com/repos/{owner}/{repo}/readme` with header `Accept: application/vnd.github.raw`, or `https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md`.
- X/Twitter single post: `https://cdn.syndication.twimg.com/tweet-result?id={id}&token=a` (JSON), fallback `https://publish.twitter.com/oembed?url=https://twitter.com/i/status/{id}`. Profiles, timelines and search need login: report that, do not scrape x.com.
- Prefer a site's RSS/Atom feed for blogs when the HTML is hard to read.

## 2. One plain request that looks like a normal browser

Send a coherent header set, not a library default (`python-requests` and empty UAs are often refused):

```
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
Accept-Language: en-US,en;q=0.9,zh-CN;q=0.8
```

Most public pages succeed here.

## 3. Check what you actually got (after EVERY request)

Look at the status code, the `<title>`, and how much visible text there is. If the page is short and says any of these, it is not the content:

| Signal | Meaning | What to do |
|---|---|---|
| title "Just a moment...", `cf-mitigated: challenge`, `/cdn-cgi/challenge-platform` on a near-empty 403/503 page | Cloudflare challenge | try another client; a real browser may pass it |
| reCAPTCHA / hCaptcha / Turnstile / GeeTest / DataDome (`captcha-delivery.com`) / TCaptcha | CAPTCHA | stop; never solve or outsource a CAPTCHA; report |
| "环境异常", "完成验证后即可继续访问", "unusual traffic", "verify you are human" | verification wall | try another client once; otherwise report |
| HTTP 401, redirect to `/login`, "sign in to continue", password form on an empty page, "请登录" | login required | stop; ask the user to open it or paste the content |
| "subscribe to continue reading", JSON-LD `isAccessibleForFree: false` with a teaser | paywall | stop; report |
| HTTP 429, "too many requests", `Retry-After` | rate limited | wait `Retry-After` (if short) then retry once; otherwise report |
| HTTP 404/410, or 200 with title "Page not found" | not found / soft 404 | stop; report |
| "该内容已被发布者删除", "此内容因违规无法查看", "post was deleted" | content removed | stop; report |
| almost no text, `<div id="root"></div>` / `<div id="__next"></div>`, many scripts | JavaScript shell | go to step 5 first, then 6 |

Caution: an article that merely mentions "captcha" or "login" is fine. Only trust these signals when the page has little real text. Cloudflare injects `challenge-platform` scripts into many normal pages too.

## 4. Blocked for a retryable reason? Change the client, politely

Try, in order, with a pause of at least 1 second between requests to the same site:

1. Mobile Safari: `Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1`, plus `Referer: https://<same-site>/`
2. Android Chrome.
3. An in-app browser if the content was made for one. WeChat articles (`mp.weixin.qq.com/s/...`) are built for WeChat's browser, so for them start with:
   `Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.47(0x18002f2f) NetType/WIFI Language/zh_CN` with `Accept-Language: zh-CN,zh;q=0.9`.
   A different network egress (e.g. your own machine instead of a cloud fetch service) also often helps.

Do NOT impersonate search-engine crawlers (Googlebot etc.), forge cookies or tokens, or reuse someone's logged-in session. Do not retry non-retryable verdicts (CAPTCHA, login, paywall, 404, removed): a different client will get the same answer.

## 5. Extract with several strategies and keep the richest

The visible HTML is not the only place text lives. Check all of these and keep the longest real body:

1. Main content (readability-style): the `<article>` / main text block without nav, footer, ads.
2. JSON-LD: `<script type="application/ld+json">` objects of type `NewsArticle`, `Article`, `BlogPosting` often contain the full `articleBody` (also check inside `@graph`).
3. Embedded JS state: `<script id="__NEXT_DATA__">` (Next.js, look under `props.pageProps`), `__NUXT__` / `__NUXT_DATA__`, `window.__INITIAL_STATE__`, `__APOLLO_STATE__`, YouTube `ytInitialPlayerResponse` (`videoDetails.shortDescription`). Look for long prose under keys like `content`, `body`, `articleBody`, `html`, `text`, `description`.
4. Meta: `og:title`, `og:description`, `twitter:description`, `article:published_time`, `author`. Usually only a summary: label it as such.
5. Site rules. WeChat: long articles are in `#js_content` (images in `data-src`); short text posts have an empty `#js_content` and the full text in the JS variable `content_noencode` (decode `\x0a` etc.) and often in `og:title`. Author is `author`, account name `nick_name`, time `ori_create_time` (Unix seconds) or `create_time` (China time, +08:00).

If the result is still thin, try the page's AMP version (`<link rel="amphtml">`) and its RSS/Atom feed (`<link rel="alternate" type="application/rss+xml">`), matching the entry by URL.

Tell the user which source you used when it matters ("from the page's structured data", "only the meta description was available").

## 6. Last resort: a real (headless) browser

If you have a browser tool and the failure was retryable (JS shell, Cloudflare wait page, thin content), open the page like a normal visitor, wait for it to settle, scroll once, then run steps 3 and 5 on the rendered HTML. If a CAPTCHA or login is still shown, stop and report. Never click through CAPTCHAs.

## When you are blocked

Tell the user plainly, in one or two sentences: what you tried, what you hit, and what they can do. Examples:

- "That page is behind a login (it redirected me to the sign-in page), so I can't read it. If you paste the text or a screenshot, I'll work from that."
- "Reuters showed me a DataDome CAPTCHA. I don't solve CAPTCHAs; could you open it and paste the article?"
- "WeChat returned its '环境异常' verification page. Sending the article text or opening it in WeChat and sharing the content would work."

Never present a block page, cookie banner or error text as the article's content.

## Responsible use

Only public content. Respect robots.txt (always for crawls, batch jobs and schedules; for a single link the user handed you, note it if robots.txt disallows), the site's terms, rate limits (≥1 s between requests to one site, back off on 429/503), and copyright (summarise and cite; don't republish whole articles).
