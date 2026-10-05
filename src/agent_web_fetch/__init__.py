"""agent-web-fetch: a web-reading toolkit for AI agents.

Cheapest first, escalate only when blocked, and always say *why* a page could not be read.
"""
from .models import BLOCK_REASONS, FetchAttempt, PageResult
from .pipeline import extract_from_html, fetch

__version__ = "0.1.0"
__all__ = ["fetch", "extract_from_html", "PageResult", "FetchAttempt", "BLOCK_REASONS", "__version__"]
