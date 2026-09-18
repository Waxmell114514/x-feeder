"""Hacker News, through the public Algolia search API.

No key, no quota worth worrying about, and full-text search over both
stories and comments with a date filter - which is exactly the shape this
pipeline wants. HN is one venue with one culture, so everything it returns
carries the same channel and is tiered as one thing.
"""
from __future__ import annotations

import datetime as dt

from ..http import request
from ..models import Document, Engagement, PlannedQuery
from . import feedparse
from .base import SourceResult, from_epoch

BASE = "https://hn.algolia.com/api/v1"
CHANNEL = "news.ycombinator.com"


class HackerNewsSource:
    name = "hackernews"

    def __init__(self, cfg):
        self.cfg = cfg

    def fetch(self, query: PlannedQuery, since: dt.datetime) -> SourceResult:
        opts = query.options
        tags = opts.get("tags") or "story"
        params = {
            "query": query.query,
            "tags": tags,
            "numericFilters": f"created_at_i>{int(since.timestamp())}",
            "hitsPerPage": min(100, max(10, query.max_results)),
        }
        payload = request("GET", f"{BASE}/search_by_date", params=params,
                          timeout=self.cfg.source.request_timeout, retries=2)
        documents = []
        for hit in payload.get("hits") or []:
            doc = _document(hit, query.tag)
            if doc is not None and doc.created_at >= since:
                documents.append(doc)
        return SourceResult(documents=documents[: query.max_results],
                            note=f"{payload.get('nbHits', 0)} hits")


def _document(hit: dict, tag: str) -> Document | None:
    native = hit.get("objectID")
    if not native:
        return None
    title = feedparse.strip_html(hit.get("title") or hit.get("story_title") or "")
    # Algolia hands back comment bodies as HTML, entities and anchor tags and
    # all. Left in, a quoted URL becomes "x2f gov x2f monetarypolicy" and that
    # phrase can win a seat on the panel.
    text = feedparse.strip_html(hit.get("story_text") or hit.get("comment_text") or "")
    if not (title or text):
        return None
    return Document(
        id=f"hn:{native}",
        source="hackernews",
        channel=CHANNEL,
        channel_kind="forum",
        author=hit.get("author") or "",
        title=title,
        text=text[:8000],
        url=f"https://news.ycombinator.com/item?id={native}",
        created_at=from_epoch(hit.get("created_at_i") or 0),
        engagement=Engagement(
            score=max(0, int(hit.get("points") or 0)),
            comments=int(hit.get("num_comments") or 0),
        ),
        query_tag=tag,
        raw={"external_url": hit.get("url") or ""},
    )
