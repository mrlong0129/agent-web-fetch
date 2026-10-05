"""Minimal library usage for an agent loop."""
from agent_web_fetch import fetch

r = fetch("https://yomin.love/projects/claude-gemini-bridge/")
if r.ok:
    print(r.title, "-", r.extraction_method, "-", len(r.content_text), "chars")
    print(r.content_markdown[:500])
else:
    # Never try to bypass: tell the user why and what they can do.
    print("Could not read the page:", r.blocked_reason, r.blocked_detail)
    for a in r.fetch_attempts:
        print(" ", a.strategy, a.result, a.status, a.blocked_reason)
