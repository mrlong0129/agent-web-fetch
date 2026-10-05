# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-06

### Added

- Escalation ladder for reading public web pages: site adapter APIs → plain HTTP with a coherent browser header set → client rotation (desktop Chrome, mobile Safari, Android Chrome, WeChat in-app browser) with same-origin Referer, Accept-Language, per-host pacing and Retry-After-aware backoff → AMP / RSS-Atom alternates → optional Playwright headless browser (`agent-web-fetch[browser]`), skipped gracefully when not installed.
- Block detection with machine-readable `blocked_reason`: `cloudflare_challenge`, `captcha`, `verification_wall`, `login_required`, `paywall`, `rate_limited`, `forbidden`, `not_found`, `soft_404`, `content_removed`, `js_required`, `robots_disallowed`, `network_error`, `empty_content`, `unsupported_content_type`. CAPTCHAs, logins, paywalls and removed content stop the ladder immediately and are reported, never bypassed.
- Multi-strategy extraction (trafilatura, readability-lxml, JSON-LD `articleBody`, embedded JS state such as `__NEXT_DATA__` / `__NUXT__` / `window.__INITIAL_STATE__` / `ytInitialPlayerResponse`, og:/twitter: meta, site adapters); the richest result wins and is recorded in `extraction_method` with all candidates in `extraction_candidates`.
- Site adapters: `wechat` (mp.weixin.qq.com), `github` (README API / raw files), `x` (single posts via public syndication JSON and oEmbed).
- robots.txt check on every fetch (`--robots warn|strict|ignore`).
- CLI `awf` (`fetch`, `extract`, `adapters`, `reasons`, `mcp`), Python API (`fetch`, `extract_from_html`), MCP server `awf-mcp` with tools `fetch_page` and `extract_from_html`.
- Agent-friendly setup: zero-install `uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf fetch <url>`, a one-line `claude mcp add`, a Cursor `mcp.json` snippet, a curl one-liner to install the skill, and `AGENTS.md`. Defaults need no configuration.
- Agent skill `skill/SKILL.md`, `llms.txt`, and a Chinese/English README.
- Offline pytest suite with trimmed/placeholder WeChat fixtures and synthetic fixtures; live smoke tests behind `-m live`.

[0.1.0]: https://github.com/mrlong0129/agent-web-fetch/releases/tag/v0.1.0
