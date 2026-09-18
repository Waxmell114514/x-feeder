"""Any RSS or Atom feed, polled whole and filtered locally.

This is the source that makes the system work outside the reach of the
other three: an institution's own press feed (the only honest way to fill
the `official` tier), a trade publication, a Chinese outlet, an RSSHub
route, a single analyst's blog.

A feed has no query interface, so the planned terms are applied here after
the fetch. That is a different failure mode from a search: a feed that
publishes twice a week returns everything it has, and the term filter is
what keeps an unrelated press release out of the window.
"""
from __future__ import annotations

import datetime as dt

from ..http import fetch_text
from ..models import Document, Engagement, PlannedQuery
from . import feedparse
from .base import SourceResult, stable_id


class RssSource:
    name = "rss"

    def __init__(self, cfg):
        self.cfg = cfg

    def fetch(self, query: PlannedQuery, since: dt.datetime) -> SourceResult:
        url = query.channel or query.options.get("url", "")
        if not url:
            return SourceResult(note="feed query carried no url")
        xml = fetch_text(url, headers={"User-Agent": self.cfg.source.user_agent},
                         timeout=self.cfg.source.request_timeout, retries=2)
        entries = feedparse.parse(xml)
        channel = query.options.get("channel") or feedparse.host_of(url) or url
        terms = [t.lower() for t in query.terms] if query.options.get("match_terms", True) else []

        documents, skipped = [], 0
        for entry in entries:
            if entry.published is None or entry.published < since:
                continue
            haystack = f"{entry.title} {entry.summary}".lower()
            if terms and not any(t in haystack for t in terms):
                skipped += 1
                continue
            documents.append(Document(
                id=f"rss:{stable_id(url, entry.id)}",
                source="rss",
                channel=channel,
                channel_kind="feed",
                author=entry.author,
                title=entry.title,
                text=entry.summary,
                url=entry.link or url,
                created_at=entry.published,
                engagement=Engagement(),
                query_tag=query.tag,
                raw={"feed": url},
            ))
        note = f"{len(entries)} entries, {skipped} off-topic" if entries else "empty feed"
        return SourceResult(documents=documents[: query.max_results], note=note)
