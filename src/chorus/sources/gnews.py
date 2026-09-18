"""Google News, through its public RSS search.

`news.google.com/rss/search?q=...` is an open, keyless index over most of
the world's news outlets, which is what makes the professional-media tier
cheap to populate. It has one limitation that has to be stated plainly
rather than papered over:

    **It returns headlines, not articles.**

A headline is a thin document. The pipeline knows this - `weighting.py`
discounts documents below `thin_document_chars` - but the honest summary is
that this source tells you what the press is *saying about*, not what the
press *argues*. For argument you need the outlet's own feed, which is what
`rss.py` is for.
"""
from __future__ import annotations

import datetime as dt

from ..http import fetch_text
from ..models import Document, Engagement, PlannedQuery
from . import feedparse
from .base import SourceResult, stable_id

BASE = "https://news.google.com/rss/search"


class GoogleNewsSource:
    name = "gnews"

    def __init__(self, cfg):
        self.cfg = cfg

    def fetch(self, query: PlannedQuery, since: dt.datetime) -> SourceResult:
        opts = query.options
        params = {
            "q": query.query,
            "hl": opts.get("hl", "en-US"),
            "gl": opts.get("gl", "US"),
            "ceid": opts.get("ceid", "US:en"),
        }
        xml = fetch_text(BASE, params=params,
                         headers={"User-Agent": self.cfg.source.user_agent},
                         timeout=self.cfg.source.request_timeout, retries=2)
        documents = []
        for entry in feedparse.parse(xml):
            doc = _document(entry, query.tag)
            if doc is not None and doc.created_at >= since:
                documents.append(doc)
        return SourceResult(documents=documents[: query.max_results])


def _document(entry, tag: str) -> Document | None:
    if not entry.title or entry.published is None:
        return None
    # The outlet is the speaker here, and Google names it on every item.
    channel = feedparse.host_of(entry.source_url) or entry.source_name.lower() or "news"
    # Google appends " - Outlet" to every headline; the outlet is already a
    # field, so carrying it in the text too would just bias the reader.
    title = entry.title
    if entry.source_name and title.endswith(f" - {entry.source_name}"):
        title = title[: -len(entry.source_name) - 3].strip()
    return Document(
        id=f"gnews:{stable_id(entry.id, entry.link)}",
        source="gnews",
        channel=channel,
        channel_kind="outlet",
        author="",
        title=title,
        text=entry.summary if len(entry.summary) > len(title) + 20 else "",
        url=entry.link,
        created_at=entry.published,
        engagement=Engagement(),
        query_tag=tag,
        raw={"outlet": entry.source_name},
    )
