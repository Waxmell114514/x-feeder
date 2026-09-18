"""V2EX, through the public sov2ex search index.

The Chinese-language problem this project has is not judgement, it is
supply: Weibo, Zhihu and Tieba have no public search interface, and the
RSS bridges that used to stand in for them are now closed. What is left
that is genuinely open, and genuinely a crowd rather than a newsroom, is
V2EX - a Chinese-language forum of software people.

V2EX's own API lists topics but cannot search them. `sov2ex` is a public
full-text index over the same content, and search is what a per-issue
pipeline needs. So this source reads sov2ex and links back to V2EX.

Two honest limits, both structural:

  * **It is one community, of one profession.** It is a real Chinese crowd,
    not the Chinese crowd. On a question about software work that is close
    to ideal; on a question about mortgage rates it is close to useless.
  * **Topics only, not replies.** The index covers posts; the argument in
    the comments below them is not reachable this way. A V2EX thread is
    therefore closer to a Reddit thread title than to a Reddit comment.
"""
from __future__ import annotations

import datetime as dt

from ..http import HttpError, request
from ..models import Document, Engagement, PlannedQuery
from .base import SourceResult, parse_iso

SEARCH = "https://www.sov2ex.com/api/search"
CHANNEL = "v2ex.com"


class V2exSource:
    name = "v2ex"

    def __init__(self, cfg):
        self.cfg = cfg

    def fetch(self, query: PlannedQuery, since: dt.datetime) -> SourceResult:
        params = {
            "q": query.query,
            "size": min(50, max(10, query.max_results)),
            # Newest first. The index also offers relevance, but a monitor
            # wants the window, not the all-time best match.
            "sort": query.options.get("sort", "created"),
        }
        node = query.options.get("node")
        if node:
            params["node"] = node
        try:
            payload = request("GET", SEARCH, params=params,
                              headers={"User-Agent": self.cfg.source.user_agent},
                              timeout=self.cfg.source.request_timeout, retries=2)
        except HttpError as e:
            if e.status in (403, 429):
                raise RuntimeError(
                    f"sov2ex refused the request ({e.status}); it is a "
                    f"volunteer-run index, so slow down."
                ) from e
            raise

        documents = []
        for hit in payload.get("hits") or []:
            doc = _document(hit, query.tag)
            if doc is not None and doc.created_at >= since:
                documents.append(doc)
        return SourceResult(documents=documents[: query.max_results],
                            note=f"{payload.get('total', 0)} indexed matches")


def _document(hit: dict, tag: str) -> Document | None:
    source = hit.get("_source") or {}
    native = source.get("id") or hit.get("_id")
    title = (source.get("title") or "").strip()
    if not native or not title:
        return None

    created = source.get("created")
    when = None
    if created:
        try:
            when = parse_iso(created).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            when = None
    if when is None:
        # `sort` carries the same instant as epoch milliseconds.
        stamps = hit.get("sort") or []
        if not stamps:
            return None
        when = dt.datetime.fromtimestamp(float(stamps[0]) / 1000.0, dt.timezone.utc)

    return Document(
        id=f"v2ex:{native}",
        source="v2ex",
        channel=CHANNEL,
        channel_kind="forum",
        author=str(source.get("member") or ""),
        title=title,
        text=(source.get("content") or "")[:8000],
        url=f"https://www.v2ex.com/t/{native}",
        created_at=when,
        # V2EX has no public score on a topic; replies are the only signal
        # of reception it exposes, so that is the only one used.
        engagement=Engagement(comments=int(source.get("replies") or 0)),
        lang="zh",
        query_tag=tag,
        raw={"node": source.get("node")},
    )
