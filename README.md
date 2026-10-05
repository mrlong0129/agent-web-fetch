# agent-web-fetch

> 给 AI Agent 用的网页读取工具：**先用最便宜的办法，被挡住了才升级；读不到时，明确告诉你为什么。**
>
> A web-reading toolkit for AI agents: cheapest first, escalate only when blocked, and always report *why* a page could not be read.

[English](#english) · Python 库 · CLI (`awf`) · MCP Server (`awf-mcp`) · Agent Skill (`skill/SKILL.md`) · MIT

作者 [Yomin Ma](https://yomin.love) · GitHub [@mrlong0129](https://github.com/mrlong0129) · 姊妹项目：[wechat-article-fetcher](https://github.com/mrlong0129/wechat-article-fetcher)（专门读微信公众号文章） · [项目页](https://yomin.love/projects/agent-web-fetch/)

## Quick start for agents

不用安装、不用配置，复制一行就能用（需要 [uv](https://docs.astral.sh/uv/)；默认行为零配置）：

```bash
# 1) 直接读一个网页（不安装）
uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf fetch <url>
uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf fetch <url> --format json   # 结构化结果

# 2) Claude Code：一行注册 MCP server
claude mcp add agent-web-fetch -- uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf-mcp
```

**Cursor**：写入 `~/.cursor/mcp.json`（全局）或项目里的 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "agent-web-fetch": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/mrlong0129/agent-web-fetch", "awf-mcp"]
    }
  }
}
```

**Agent Skill**（不装包也能用，Agent 会照着手册用自己的工具读网页）：

```bash
# Claude Code
mkdir -p ~/.claude/skills/agent-web-fetch && curl -fsSL https://raw.githubusercontent.com/mrlong0129/agent-web-fetch/main/skill/SKILL.md -o ~/.claude/skills/agent-web-fetch/SKILL.md
# Cursor
mkdir -p ~/.cursor/skills/agent-web-fetch && curl -fsSL https://raw.githubusercontent.com/mrlong0129/agent-web-fetch/main/skill/SKILL.md -o ~/.cursor/skills/agent-web-fetch/SKILL.md
```

给编码 Agent 的完整说明见 [AGENTS.md](AGENTS.md)。

### 输出长什么样

```bash
awf fetch https://mp.weixin.qq.com/s/<article-id>
```

```text
---
title: "示例标题"
url: "https://mp.weixin.qq.com/s/<article-id>"
author: "示例作者"
published: "2026-10-05T23:59:51+08:00"
site_name: "示例公众号"
extraction_method: "wechat:content_noencode"
---

# 示例标题

正文（Markdown）……
```

读不到时，退出码为 `2`，stderr 里写明原因，例如 `blocked_reason: login_required`，以及每一步尝试了什么。

---

## 为什么要做这个

大多数给大模型用的"网页抓取"工具，逻辑都只有一步：发一个请求，拿到 HTML，转成文本。成功了很好；失败了，Agent 拿到的要么是一个报错，要么是一段"请完成验证"的页面文字，然后它会一本正经地"总结"这段验证页。

真实的网络不是这样的。**同一个链接，换一个"访问者"，拿到的可能完全是另一个页面**：

- 微信公众号文章：从云服务器请求，经常返回"环境异常，完成验证后即可继续访问"；用手机微信的身份请求，同一个链接返回完整文章。
- 很多新闻站：正文由 JavaScript 渲染，HTML 里只有一个空壳，但页面里藏着给搜索引擎看的 JSON-LD，里面就是全文。
- Next.js / Nuxt 站点：`<div id="__next"></div>` 是空的，正文在 `__NEXT_DATA__` 这段 JSON 里。
- YouTube：页面是重度 JS 应用，但视频标题和简介就在 `ytInitialPlayerResponse` 里。
- 有的页面压根不该读：需要登录、付费墙、验证码。这时候 Agent 最需要的不是"再试一次"，而是**知道原因**，好去问用户。

`agent-web-fetch` 把一个有经验的人读网页的思路，写成了一套 Agent 能直接调用的流程。

## 升级策略：先便宜，后昂贵

核心原则只有一句：**一条路不通就换下一条，先用最便宜的办法，不行再上更重的；每一步都检查自己拿到的到底是不是正文。**

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 0. 站点适配器 API     GitHub README / X 公开嵌入接口        最便宜、最稳 │
 │ 1. 普通 HTTP 请求     一套真实、自洽的浏览器请求头                     │
 │ 2. 换身份重试         桌面 Chrome → 手机 Safari → Android / 微信内置浏览器│
 │                       + 同站 Referer、Accept-Language、礼貌退避重试    │
 │ 3. 每一步都做检测     Cloudflare？验证码？登录墙？付费墙？软 404？限流？ │
 │ 4. 多策略正文提取     trafilatura / readability / JSON-LD / JS 状态 /  │
 │                       og: 元数据 / 站点专用规则，挑最丰富的那个        │
 │    └ 正文太薄时        试 AMP 版本、RSS/Atom 订阅源                     │
 │ 5. 无头浏览器         Playwright（可选安装），真正把页面渲染出来   最贵 │
 └──────────────────────────────────────────────────────────────────────┘
```

### 第 0 层：能用官方/公开接口，就别解析 HTML

如果一个网站本来就提供了不需要登录的公开接口，那它永远比抓网页更快、更稳、更礼貌。

- **GitHub**：仓库地址直接走 `api.github.com` 拿 README 原文（Markdown），API 限额用完就退到 `raw.githubusercontent.com`。
- **X / Twitter**：单条推文走嵌入推文用的公开接口 `cdn.syndication.twimg.com`，不行再走官方 oEmbed。个人主页、时间线、搜索需要登录，工具会直接报告 `login_required`，不会去硬抓。

### 第 1 层：一次普通的请求，但要像个正常浏览器

很多工具默认的 User-Agent 是 `python-requests/2.x`，这本身就会被不少站点直接拒绝。我们发送的是一整套**自洽**的请求头：User-Agent、Accept、Accept-Language、`sec-ch-ua` 系列，彼此对得上，就像一个真的 Chrome。大部分公开页面在这一步就成功了，耗时几十到几百毫秒。

### 第 2 层：换一个"访问者"

被挡住时，最先要试的是换一个更合适的访问方式：

| 身份 | 适用场景 |
|---|---|
| `desktop_chrome` | 默认，绝大多数网站 |
| `mobile_safari` | 很多站点给手机版的是更轻、服务端渲染的页面 |
| `android_chrome` | 同上，另一种常见移动客户端 |
| `wechat_ios` | 微信内置浏览器。公众号文章就是为它设计的，所以微信适配器第一步就用它 |

从第二次尝试开始，会带上同站的 `Referer`（就像用户从网站首页点进来）。遇到 429 / 503 会读取 `Retry-After`，按指数退避礼貌地重试，并且对同一个站点的请求之间保持最小间隔。同一次抓取的多个尝试共享 Cookie，就像同一个浏览器会话。

### 第 3 层：每一步都检查"我拿到的到底是什么"

这是和普通抓取工具最大的区别。每次拿到响应，都会先判断它是不是正文，如果不是，给出一个机器可读的原因 `blocked_reason`：

| `blocked_reason` | 含义 | 换身份有用吗 |
|---|---|---|
| `cloudflare_challenge` | Cloudflare "Just a moment..." 等待页 | 可能（浏览器层可能通过） |
| `captcha` | reCAPTCHA / hCaptcha / Turnstile / GeeTest / DataDome / 腾讯验证码 | 否：不解、不换身份绕，立即停止并报告 |
| `verification_wall` | 环境验证页，例如微信"环境异常" | 可能 |
| `login_required` | 需要登录才能看 | 否，立即停止 |
| `paywall` | 付费墙（包括 JSON-LD 里 `isAccessibleForFree=false`） | 否，立即停止 |
| `rate_limited` | 429 或"请求过于频繁" | 稍后再试 |
| `forbidden` | 403 / 412 等，没有识别出具体的挑战页（例如风控） | 可能 |
| `not_found` / `soft_404` | 真 404，或 HTTP 200 但页面写着"找不到" | 否 |
| `content_removed` | 内容已被删除 / 违规无法查看 | 否 |
| `js_required` | 纯 JavaScript 空壳，需要浏览器渲染 | 交给浏览器层 |
| `robots_disallowed` | robots.txt 禁止，且使用了 `--robots strict` | 否 |

两条细节：

1. **关键词只在"页面很薄"时才算数**。一篇讨论验证码的技术文章不会被误判成验证码页。
2. **Cloudflare 会往很多正常页面里注入 `challenge-platform` 脚本**，所以光有这个标记不算被挡，必须同时满足"挑战状态码"或"页面几乎没有内容"。

遇到验证码、登录墙、付费墙、404、已删除这类"换谁来都一样"的情况，梯子会立刻停下来，不浪费请求，也不去绕。

### 第 4 层：多策略提取，挑最丰富的，并记录谁赢了

拿到真正的页面之后，同时跑多种提取策略（它们都在本地运行，比再发一次网络请求便宜得多）：

| 策略 | 擅长 |
|---|---|
| `trafilatura` | 通用的正文提取（readability 类算法），去掉导航、页脚、广告 |
| `readability` | Mozilla Readability 的 Python 移植，第二意见 |
| `json_ld` | 新闻站给搜索引擎准备的 `articleBody`，常常就是全文 |
| `js_state` | `__NEXT_DATA__`、`__NUXT__`、`window.__INITIAL_STATE__`、`__APOLLO_STATE__`、`ytInitialPlayerResponse` 等内嵌状态 |
| `meta` | `og:` / `twitter:` 描述，最后的兜底（通常只是摘要） |
| `wechat:*` 等 | 站点适配器自己的规则，例如公众号的 `js_content` / `content_noencode` / `og:title` |

按"文本长度 × 可信度权重"打分，选最高的。所有候选及其字数都写在 `extraction_candidates` 里，赢家写在 `extraction_method` 里，方便你调试，也方便 Agent 判断结果可不可靠。元数据（标题、作者、时间、站点名）则按字段合并：适配器 > JSON-LD > og 元数据 > trafilatura。

如果正文还是太薄，会尝试页面声明的 **AMP 版本**（`<link rel="amphtml">`），以及 **RSS/Atom 订阅源**里与当前链接匹配的那一条（订阅源本来就是给机器读的，又便宜又礼貌）。

### 第 5 层：真正打开一个浏览器

前面都不行、而且失败原因是"换个方式可能有用"的那一类时，才会启动 Playwright 无头浏览器，像普通访客一样把页面渲染出来，再走一遍检测和提取。它是可选依赖，没装就记录一条 `skipped` 然后跳过，不会报错。浏览器层**不解验证码、不做交互式挑战**；渲染后依然是验证码，就照实报告。

### 微信公众号：一个完整的例子

这个项目就是从读一篇公众号文章开始的。`wechat` 适配器把踩过的坑都写进去了：

1. 第一个尝试就用 iPhone 微信内置浏览器的 UA，`Accept-Language: zh-CN`。
2. 识别"环境异常 / 完成验证后即可继续访问"的验证页 → `verification_wall`；识别"该内容已被发布者删除""此内容因违规无法查看" → `content_removed`。
3. 长文：正文在 `#js_content` 里，图片地址在 `data-src` 里。
4. 短图文（`item_show_type=10`）：`#js_content` 是空的，全文在 `content_noencode` 这个 JS 变量里，`og:title` 里通常也有一份完整的。
5. 作者、公众号名、发布时间分别来自 `author`、`nick_name`、`ori_create_time`（没有时用 `create_time`），时间统一输出为带 `+08:00` 的 ISO 格式。

## 安装

```bash
# 从 GitHub 安装（发布到 PyPI 后可直接 pip install agent-web-fetch）
pip install "git+https://github.com/mrlong0129/agent-web-fetch"

# 可选：无头浏览器层
pip install "agent-web-fetch[browser] @ git+https://github.com/mrlong0129/agent-web-fetch"
playwright install chromium     # 或者系统里已有 Google Chrome 也可以，会自动尝试

# 不想装？用 uvx 直接跑
uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf fetch https://example.com
```

需要 Python 3.10+。

## 使用

### CLI

```bash
awf fetch <url>                       # 默认输出 Markdown（带 front matter）
awf fetch <url> --format json         # 完整结构化结果
awf fetch <url> --format text         # 纯文本
awf fetch <url> --browser             # 直接用无头浏览器
awf fetch <url> --no-browser          # 永远不用浏览器
awf fetch <url> --adapter none        # 关闭站点适配器（auto | none | wechat | github | x）
awf fetch <url> --profile wechat_ios  # 指定身份（可重复，按顺序尝试）
awf fetch <url> --robots strict       # robots.txt 不允许就不抓（批量/爬取场景请用这个）
awf extract page.html --url <原链接>   # 对本地 HTML 做检测 + 提取，不联网
awf adapters                          # 列出适配器
awf reasons                           # 列出所有 blocked_reason
```

退出码：`0` 成功，`2` 被挡或没有内容（原因打印在 stderr），`1` 其他错误。

JSON 输出字段：`url`、`final_url`、`status`、`ok`、`title`、`author`、`published`、`site_name`、`description`、`lang`、`content_markdown`、`content_text`、`links`、`images`、`extraction_method`、`extraction_candidates`、`adapter`、`feeds`、`amp_url`、`fetch_attempts`（每一步的 `strategy`、`result`、`status`、`blocked_reason`、`detail`、耗时、字节数、提取字数）、`blocked_reason`、`blocked_detail`、`robots`、`warnings`。

### Python

```python
from agent_web_fetch import fetch, extract_from_html

r = fetch("https://yomin.love/projects/claude-gemini-bridge/")
if r.ok:
    print(r.title, r.extraction_method, len(r.content_text))
else:
    print(r.blocked_reason, r.blocked_detail)   # 告诉用户原因，不要去绕

r = extract_from_html(html, url="https://example.com/post")  # 已经有 HTML 时
```

### MCP Server（Claude Code / Cursor / 其他 Agent）

提供两个工具：

- `fetch_page(url, format="markdown"|"text"|"both", use_browser="auto"|"never"|"always", adapter="auto", max_chars=40000)`
- `extract_from_html(html, url=None, format=..., adapter="auto", max_chars=40000)`

**Claude Code**

```bash
claude mcp add agent-web-fetch -- uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf-mcp
# 已经 pip install 过的话：
claude mcp add agent-web-fetch -- awf-mcp
```

或者在项目根目录的 `.mcp.json` 里：

```json
{
  "mcpServers": {
    "agent-web-fetch": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/mrlong0129/agent-web-fetch", "awf-mcp"]
    }
  }
}
```

**Cursor**：把同样的 JSON 写进 `~/.cursor/mcp.json`（全局）或项目里的 `.cursor/mcp.json`。

### Agent Skill（不装也能用）

[`skill/SKILL.md`](skill/SKILL.md) 是一份写给 Agent 的操作手册，用大白话讲清楚这套升级策略：先试什么、看到什么信号说明被挡了、什么时候该停下来问用户。把它放进 Claude Code 的 `~/.claude/skills/agent-web-fetch/SKILL.md`（或任何支持 skill 的 Agent），即使没有安装这个包，Agent 也能用自己的 `curl` / 浏览器工具照着做；装了包的话，它会优先调用 `awf`。

## 和普通抓取工具比

| | 普通 fetch 工具 | agent-web-fetch |
|---|---|---|
| 请求 | 一次，默认 UA | 一套自洽的浏览器请求头，按需换身份 |
| 被挡住 | 返回错误，或把验证页当正文 | 识别出原因（Cloudflare / 验证码 / 登录 / 付费 / 限流 / 软 404…）并报告 |
| JS 渲染页 | 拿到空壳 | 先挖 JSON-LD 和内嵌 JS 状态，再试 AMP / RSS，最后才开浏览器 |
| 正文提取 | 单一算法 | 多策略竞争，记录赢家和所有候选 |
| 可解释性 | 无 | `fetch_attempts` 记录每一步做了什么、结果如何 |
| 站点知识 | 无 | 适配器（微信、GitHub、X），可扩展 |

它不是爬虫框架，不做大规模抓取、不管理代理池，也不打算和专门的反爬对抗工具竞争。它解决的是 Agent 日常最常见的需求：**用户丢过来一个链接，读懂它；读不了，说清楚为什么。**

## 局限

- 只读公开内容。需要登录、付费、或者只对粉丝可见的内容读不到，这是设计如此。
- 检测是启发式的，可能有误判，所以每一步都记录了原因细节（`blocked_detail`）方便核查。
- 站点会变：微信、X 的公开接口随时可能调整，适配器需要维护。
- 从数据中心 IP 访问时，部分站点（例如路透社的 DataDome、知乎、B 站风控）无论换什么身份都会拒绝，工具会照实报告 `captcha` / `forbidden`。
- GitHub 未登录 API 每个 IP 每小时 60 次；设置环境变量 `GITHUB_TOKEN` 可以提高，但不是必需。
- 无头浏览器层慢（通常 10–20 秒），而且需要额外安装。

## 负责任地使用

- **只读公开内容。** 这个工具遇到登录墙、付费墙、验证码时会**报告**，而不是绕过。永远不要用它去绕过访问控制。
- **robots.txt**：工具每次都会检查 robots.txt，并在结果的 `robots` 和 `warnings` 字段里写明。默认模式 `warn` 面向"用户亲手给了一个链接、读一次"的场景（和浏览器打开链接性质相同）；**做批量抓取、爬虫、定时任务时，请使用 `--robots strict`**。
- **控制频率**：内置了同站请求间隔和退避重试。不要用它高频轮询任何网站。
- **遵守服务条款和版权**：读到的内容版权归原作者。引用请注明出处，不要整篇转载或用于违反网站条款的用途。
- **身份切换的边界**：切换 User-Agent 是为了拿到"为这类客户端设计的那个版本"（比如为手机微信设计的公众号页面），而不是伪装成搜索引擎爬虫或冒充特定用户。本工具不会冒充 Googlebot 等爬虫。

---

<a id="english"></a>

## English

By [Yomin Ma](https://yomin.love) ([@mrlong0129](https://github.com/mrlong0129)). Sibling project: [wechat-article-fetcher](https://github.com/mrlong0129/wechat-article-fetcher), a focused reader for WeChat Official Account articles. See [Quick start for agents](#quick-start-for-agents) for zero-install one-liners and [AGENTS.md](AGENTS.md).

### Why

Most "fetch" tools for LLMs make one request and either return an error or hand the agent a verification page that it then confidently "summarises". On the real web the same URL can return a full article, a bot check, or an empty JavaScript shell depending on who is asking. `agent-web-fetch` encodes how an experienced human reads the web: **try the cheapest thing first, escalate only when blocked, check at every step whether you actually got the content, and when you can't, say exactly why.**

### The escalation ladder

0. **Site adapter APIs** – public, login-free endpoints (GitHub README API / raw, X syndication + oEmbed).
1. **Plain HTTP** – one request with a coherent, realistic browser header set.
2. **Client rotation** – desktop Chrome → mobile Safari → Android Chrome (WeChat in-app UA for WeChat), same-origin `Referer`, `Accept-Language`, polite pacing, `Retry-After`-aware exponential backoff, cookies shared across rungs.
3. **Detection after every rung** – `cloudflare_challenge`, `captcha`, `verification_wall`, `login_required`, `paywall`, `rate_limited`, `forbidden`, `not_found`, `soft_404`, `content_removed`, `js_required`, `robots_disallowed`. Keyword heuristics only fire on thin pages, so articles *about* captchas are not misflagged. Non-retryable verdicts (CAPTCHA, login, paywall, 404, removed) stop the ladder immediately.
4. **Multi-strategy extraction** – trafilatura, readability-lxml, JSON-LD `articleBody`, embedded JS state (`__NEXT_DATA__`, `__NUXT__`, `window.__INITIAL_STATE__`, `__APOLLO_STATE__`, `ytInitialPlayerResponse`…), og:/twitter: meta, and adapter rules compete; the richest (length × trust weight) wins and is recorded in `extraction_method`, with all candidates in `extraction_candidates`. Thin pages also try the **AMP** version and a matching **RSS/Atom** entry.
5. **Headless browser** – optional Playwright extra, used only when cheaper rungs failed for a retryable reason; skipped gracefully if not installed. It never solves CAPTCHAs.

### Install

```bash
pip install "git+https://github.com/mrlong0129/agent-web-fetch"
pip install "agent-web-fetch[browser] @ git+https://github.com/mrlong0129/agent-web-fetch" && playwright install chromium
```

### Use

```bash
awf fetch <url> [--format md|json|text] [--browser|--no-browser] [--adapter auto|none|wechat|github|x] [--robots warn|strict|ignore]
awf extract page.html --url <original-url>
awf-mcp            # MCP server on stdio: tools fetch_page, extract_from_html
```

Claude Code: `claude mcp add agent-web-fetch -- uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf-mcp`. Cursor: put the JSON above in `~/.cursor/mcp.json`. The agent skill lives in [`skill/SKILL.md`](skill/SKILL.md) and works even without the package.

### Limitations and responsible use

Public content only. The tool **reports** logins, paywalls and CAPTCHAs; it never bypasses them. It checks robots.txt on every fetch and surfaces the result; the default `warn` mode is meant for one-off, user-initiated reads of a link (like opening it in a browser) — use `--robots strict` for crawls, batch jobs and schedules. Built-in per-host pacing and backoff; don't use it to hammer sites. Respect each site's terms and the authors' copyright. Profile rotation picks the client a page was built for (e.g. WeChat's in-app browser); it does not impersonate search-engine crawlers or specific users. Detection is heuristic, adapters can break when sites change, and some sites block datacenter IPs regardless of client.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,browser]"
pytest            # offline tests (fixtures in tests/fixtures)
pytest -m live    # live smoke tests (network)
```

Adding a site adapter: subclass `agent_web_fetch.adapters.base.Adapter`, override `matches`, and any of `profiles`, `api_fetch`, `classify`, `candidates`; register it in `adapters/__init__.py`. Please only add adapters you have verified against the live site, using public, login-free endpoints.

## License

MIT © 2026 [Yomin Ma](https://yomin.love)
