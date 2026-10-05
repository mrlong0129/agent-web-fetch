# AGENTS.md — agent-web-fetch

Instructions for coding agents (Claude Code, Cursor, Codex, etc.) that want to **use** this tool or **work on** this repo.

## Use it in one step (no install, no config)

```bash
uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf fetch <url>               # Markdown
uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf fetch <url> --format json # structured
```

Requires only [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`). Defaults need zero configuration: no API keys, no environment variables. Optional: `GITHUB_TOKEN` raises GitHub API limits; `--browser` needs the `[browser]` extra.

Register as an MCP server:

```bash
claude mcp add agent-web-fetch -- uvx --from git+https://github.com/mrlong0129/agent-web-fetch awf-mcp
```

Cursor (`~/.cursor/mcp.json` or `.cursor/mcp.json`):

```json
{"mcpServers": {"agent-web-fetch": {"command": "uvx", "args": ["--from", "git+https://github.com/mrlong0129/agent-web-fetch", "awf-mcp"]}}}
```

MCP tools: `fetch_page(url, format, use_browser, adapter, max_chars, min_chars)` and `extract_from_html(html, url, format, adapter, max_chars)`.

Install the skill (works even without the package):

```bash
mkdir -p ~/.claude/skills/agent-web-fetch && curl -fsSL https://raw.githubusercontent.com/mrlong0129/agent-web-fetch/main/skill/SKILL.md -o ~/.claude/skills/agent-web-fetch/SKILL.md
```

## How to read results

- `ok: true` → use `content_markdown` (or `content_text`), `title`, `author`, `published`, `site_name`.
- `ok: false` → read `blocked_reason` and `blocked_detail`, tell the user plainly, and ask them to open the page or paste the content. **Do not try to bypass** `captcha`, `login_required`, `paywall`, or `content_removed`.
- `extraction_method: "meta"` means only a summary was available; say so.
- `fetch_attempts` lists every rung tried (strategy, result, status, reason).
- CLI exit codes: `0` ok, `2` blocked / no content, `1` other error.

## Work on this repo

```bash
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"   # add ,browser for Playwright
pytest                 # offline, fast; must pass before committing
pytest -m live         # optional network smoke tests
```

- Code lives in `src/agent_web_fetch/`: `pipeline.py` (escalation ladder), `detect.py` (block detection), `extract.py` (strategies), `http.py`, `profiles.py`, `browser.py`, `adapters/`, `cli.py`, `mcp_server.py`.
- New site adapter: subclass `adapters/base.py:Adapter`, register in `adapters/__init__.py`, add an offline fixture + test. Only public, login-free endpoints; verify against the live site.
- Fixtures must not contain third-party article text or real account IDs/tokens; use placeholders (see `tests/fixtures/README.md`).
- Never add features that solve CAPTCHAs, reuse login sessions, or impersonate search-engine crawlers.
