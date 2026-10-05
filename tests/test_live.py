"""Live smoke tests. Run with: pytest -m live"""
import pytest

from agent_web_fetch import fetch

pytestmark = pytest.mark.live


@pytest.mark.parametrize("url", [
    "https://yomin.love/projects/claude-gemini-bridge/",
    "https://mp.weixin.qq.com/s/MiXQsJQiDbD5akEUq1XRdw",
])
def test_live(url):
    r = fetch(url, browser="never")
    assert r.ok or r.blocked_reason, r.to_dict()
