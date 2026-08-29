"""Replay source: reads posts from a JSONL file.

Two jobs: it makes the whole pipeline runnable with no API keys (`xfeeder
demo`), and it is the seam for plugging in any other collector - export
your own posts to this shape and everything downstream works unchanged.

Line shape (one JSON object per line):
  {"id","author_id","handle","name","followers","verified","description",
   "created_at","text","lang","like","retweet","reply","quote","cohort_hint",
   "query_tag"}
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

from .. import textutil
from ..models import Author, Metrics, Post
from .base import SourceResult


class FixtureSource:
    name = "fixture"

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = pathlib.Path(cfg.source.fixture_path)
        self.store = None
        self._cache: list[tuple[Post, Author]] | None = None

    def _load(self) -> list[tuple[Post, Author]]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            raise FileNotFoundError(f"fixture not found: {self.path}")
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(_row_to_pair(json.loads(line)))

        if rows and self.cfg.source.fixture_time_shift:
            newest = max(p.created_at for p, _ in rows)
            shift = dt.datetime.now(dt.timezone.utc) - newest - dt.timedelta(minutes=4)
            for post, _ in rows:
                post.created_at = post.created_at + shift

        self._cache = rows
        return rows

    def search(self, query: str, since: dt.datetime, max_results: int = 100,
               tag: str = "") -> SourceResult:
        posts, authors = [], {}
        for post, author in self._load():
            if tag and post.query_tag and post.query_tag != tag:
                continue
            if post.created_at < since:
                continue
            posts.append(post)
            authors[author.id] = author
            if len(posts) >= max_results:
                break
        return SourceResult(posts=posts, authors=list(authors.values()),
                            note=f"fixture:{self.path.name}")


def _row_to_pair(row: dict) -> tuple[Post, Author]:
    created = row["created_at"]
    if isinstance(created, str):
        created = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
    text = row["text"]
    author = Author(
        id=str(row["author_id"]),
        handle=row.get("handle", ""),
        name=row.get("name", ""),
        followers=int(row.get("followers", 0)),
        verified=bool(row.get("verified", False)),
        description=row.get("description", ""),
        created_at=dt.datetime.fromisoformat(row["author_created_at"].replace("Z", "+00:00"))
        if row.get("author_created_at") else dt.datetime(2015, 1, 1, tzinfo=dt.timezone.utc),
        lang_hint=row.get("author_lang") or textutil.detect_lang(row.get("description", "")),
    )
    post = Post(
        id=str(row["id"]),
        author_id=author.id,
        text=text,
        lang=row.get("lang") or textutil.detect_lang(text),
        created_at=created,
        metrics=Metrics(
            like=int(row.get("like", 0)), retweet=int(row.get("retweet", 0)),
            reply=int(row.get("reply", 0)), quote=int(row.get("quote", 0)),
            impression=int(row.get("impression", 0)),
        ),
        url=row.get("url", f"https://x.com/{author.handle}/status/{row['id']}"),
        is_reply=bool(row.get("is_reply", False)),
        query_tag=row.get("query_tag", ""),
        raw={"cohort_hint": row.get("cohort_hint", "")},
    )
    return post, author
