"""Replay source: documents from a JSONL file.

Two jobs. It makes the whole pipeline runnable with no keys and no network
(`chorus demo`), and it is the seam for any collector this package does not
ship - scrape or export whatever you like into this shape and everything
downstream is unchanged.

Line shape (one JSON object per line):
  {"id","source","channel","channel_kind","author","title","text","url",
   "created_at","score","comments","reposts","lang","query_tag","tier_hint"}
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

from .. import textutil
from ..config import normalise_channel
from ..models import Document, Engagement, PlannedQuery
from .base import SourceResult
from .feedparse import host_of


class FixtureSource:
    name = "fixture"

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = pathlib.Path(cfg.source.fixture_path)
        self._cache: list[Document] | None = None

    def _load(self) -> list[Document]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            raise FileNotFoundError(f"fixture not found: {self.path}")
        docs = [_row(json.loads(line))
                for line in self.path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]

        if docs and self.cfg.source.fixture_time_shift:
            # Slide the file so its newest item is "just now". Without this a
            # shipped fixture decays to zero weight as the repo ages and the
            # demo quietly stops showing anything.
            newest = max(d.created_at for d in docs)
            shift = dt.datetime.now(dt.timezone.utc) - newest - dt.timedelta(minutes=4)
            for doc in docs:
                doc.created_at = doc.created_at + shift

        self._cache = docs
        return docs

    def fetch(self, query: PlannedQuery, since: dt.datetime) -> SourceResult:
        out = []
        for doc in self._load():
            if doc.created_at < since or not _matches(query, doc):
                continue
            out.append(doc.model_copy(deep=True))
            if len(out) >= query.max_results:
                break
        return SourceResult(documents=out, note=f"fixture:{self.path.name}")


def _matches(query: PlannedQuery, doc: Document) -> bool:
    """Answer a planned query the way the real collector would.

    A fixture whose rows were handed a query tag is filtered on it. Rows
    without one - the normal case - are matched on source and venue, so a
    replay exercises the same routing a live run would: a subreddit query
    sees that subreddit, a feed query sees that feed.
    """
    if doc.query_tag and query.tag:
        return doc.query_tag == query.tag
    if query.source not in ("fixture", "") and doc.source != query.source:
        return False
    if query.source == "reddit" and query.channel:
        return normalise_channel(doc.channel) == normalise_channel(query.channel)
    if query.source == "rss":
        wanted = query.options.get("channel") or host_of(query.channel)
        return normalise_channel(doc.channel) == normalise_channel(wanted)
    return True


def _row(row: dict) -> Document:
    created = row["created_at"]
    if isinstance(created, str):
        created = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
    text = row.get("text", "")
    title = row.get("title", "")
    return Document(
        id=str(row["id"]),
        source=row.get("source", "fixture"),
        channel=row.get("channel", ""),
        channel_kind=row.get("channel_kind", "forum"),
        author=row.get("author", ""),
        title=title,
        text=text,
        url=row.get("url", ""),
        created_at=created,
        engagement=Engagement(
            score=int(row.get("score", 0)),
            comments=int(row.get("comments", 0)),
            reposts=int(row.get("reposts", 0)),
        ),
        lang=row.get("lang") or textutil.detect_lang(f"{title} {text}"),
        query_tag=row.get("query_tag", ""),
        tier_hint=row.get("tier_hint", ""),
        raw=row.get("raw", {}),
    )
