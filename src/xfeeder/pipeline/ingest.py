"""Stage 1 - collect posts for an issue.

Each configured query is bound to a cohort, which gives us a strong prior on
who wrote what (a `from:federalreserve` query can only yield official posts).
That prior is recorded on the post's `query_tag` and consulted later by the
classifier, which is much cheaper and more reliable than asking a model to
re-derive it.
"""
from __future__ import annotations

import datetime as dt
import hashlib

from .. import textutil
from ..models import Post
from ..sources import build_source
from ..store import Store


def run_ingest(cfg, store: Store, issue_id: str, since: dt.datetime | None = None,
               log=print) -> dict:
    issue = cfg.issue(issue_id)
    source = build_source(cfg)
    if hasattr(source, "store"):
        source.store = store

    if since is None:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=issue.window_hours)

    total_fetched = total_stored = 0
    per_cohort: dict[str, int] = {}

    for q in issue.queries:
        tag = query_tag(issue, q)
        try:
            result = source.search(q.query, since=since, max_results=q.max_results,
                                   tag=tag)
        except Exception as e:                                   # noqa: BLE001
            log(f"  ! query failed [{q.cohort}] {q.query[:60]!r}: {e}")
            store.log_ingest(issue.id, tag, 0, 0, note=f"error: {e}")
            continue

        for p in result.posts:
            p.query_tag = tag
            p.raw.setdefault("cohort_hint", q.cohort)
            if not p.lang:
                p.lang = textutil.detect_lang(p.text)

        store.upsert_authors(result.authors)
        stored = store.upsert_posts(result.posts, textutil.text_hash)
        store.log_ingest(issue.id, tag, len(result.posts), stored, result.note)

        total_fetched += len(result.posts)
        total_stored += stored
        per_cohort[q.cohort] = per_cohort.get(q.cohort, 0) + len(result.posts)
        log(f"  [{q.cohort:9}] {len(result.posts):4} fetched, {stored:4} new"
            f"  {result.note}")

    return {"fetched": total_fetched, "new": total_stored, "per_cohort": per_cohort,
            "since": since}


def query_tag(issue, q) -> str:
    """Stable identity for a query.

    Must not use the builtin hash(): it is salted per process, so the tag -
    and with it the since_id cursor and the window filter - would silently
    change between runs.
    """
    if q.tag:
        return q.tag
    digest = hashlib.sha1(q.query.encode("utf-8")).hexdigest()[:8]
    return f"{issue.id}:{q.cohort}:{digest}"


def window_posts(cfg, store: Store, issue_id: str, log=None) -> list[Post]:
    issue = cfg.issue(issue_id)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=issue.window_hours)
    tags = [query_tag(issue, q) for q in issue.queries]
    posts = store.posts_in_window(since, query_tags=tags or None)
    if not posts and tags:
        # Nothing carries this issue's tags - data loaded by some other route.
        # Fall back to the whole window, but say so: with several issues in
        # one database this pulls in posts collected for the others.
        posts = store.posts_in_window(since)
        if posts and log:
            log(f"  ! no posts tagged for {issue_id}; falling back to all "
                f"{len(posts)} posts in the window")
    return posts
