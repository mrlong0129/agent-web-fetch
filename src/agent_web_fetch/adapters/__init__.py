"""Site adapters: small, site-specific knowledge layered on the generic pipeline."""
from __future__ import annotations

from typing import Optional

from .base import Adapter
from .github import GitHubAdapter
from .wechat import WeChatAdapter
from .x import XAdapter

ADAPTERS: list[Adapter] = [WeChatAdapter(), GitHubAdapter(), XAdapter()]


def get_adapter(name_or_auto: Optional[str], url: str) -> Optional[Adapter]:
    if name_or_auto in (None, "", "none", "off"):
        return None
    if name_or_auto == "auto":
        return next((a for a in ADAPTERS if a.matches(url)), None)
    for a in ADAPTERS:
        if a.name == name_or_auto:
            return a
    raise ValueError(f"unknown adapter {name_or_auto!r}; available: auto, none, {', '.join(a.name for a in ADAPTERS)}")


__all__ = ["Adapter", "ADAPTERS", "get_adapter", "WeChatAdapter", "GitHubAdapter", "XAdapter"]
