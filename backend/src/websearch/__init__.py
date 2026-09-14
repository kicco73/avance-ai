"""A web search whose answer is a table.

    WebSearch     — a query in, CSV out: the pages a search engine
                    returns, read and folded into one schema by a model
    WebCorpus     — what was crawled for one query, as the model sees it
    WebCrawler    — the search-and-fetch half on its own, injectable
    CrawledPage   — one fetched page's text

Two callers ask for the same thing by different routes: the Source
card's own "AI Web Import" (project/web_import_job.py), which shows each
step as it happens, and a `task: task.websearch(...)` line, which wants
only the CSV. So the steps are public and `csv_for` composes them."""
from .crawler import CrawledPage, WebCrawler, resolve_result_url
from .search import WebCorpus, WebSearch

__all__ = ["CrawledPage", "WebCorpus", "WebCrawler", "WebSearch", "resolve_result_url"]
