"""Stage 2 - collect the documents the plan asked for.

Each planned query carries the tier it is expected to return, because a
query aimed at a named subreddit or an institution's own feed already
answers "who is this" for free, and free beats a model call every time.

Nothing here is metered, so the budget that matters is politeness rather
than money: one query at a time, a per-run document cap, and a failure on
one query costs that query, not the run.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from .. import textutil
from ..models import Document, SearchPlan
from ..sources import build_sources
from ..store import Store


def run_ingest(cfg, store: Store, issue, plan: SearchPlan,
               since: dt.datetime | None = None, log=print) -> dict:
    sources = build_sources(cfg)
    if since is None:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=issue.window_hours)

    budget = cfg.source.per_run_document_budget
    fetched = stored = 0
    per_source: Counter = Counter()
    seen_ids: set[str] = set()

    for query in plan.queries:
        source = sources.get(query.source)
        if source is None:
            log(f"  ! no collector for source {query.source!r}")
            continue
        if fetched >= budget:
            log(f"  ! per-run budget of {budget} documents reached; stopping")
            break
        try:
            result = source.fetch(query, since)
        except Exception as e:                                    # noqa: BLE001
            log(f"  ! [{query.source}] {query.channel or query.query[:40]!r}: {e}")
            store.log_ingest(issue.id, query.tag, 0, 0, note=f"error: {e}")
            continue

        documents = []
        for doc in result.documents:
            if doc.id in seen_ids:
                continue                    # the same thread found by two queries
            seen_ids.add(doc.id)
            doc.query_tag = query.tag
            if query.tier and not doc.tier_hint:
                doc.tier_hint = query.tier
            if not doc.lang:
                doc.lang = textutil.detect_lang(doc.body)
            documents.append(doc)

        new = store.upsert_documents(documents, textutil.text_hash)
        store.log_ingest(issue.id, query.tag, len(documents), new, result.note)
        fetched += len(documents)
        stored += new
        per_source[query.source] += len(documents)
        log(f"  [{query.source:11}] {len(documents):4} fetched, {new:4} new   "
            f"{query.channel or query.query[:44]}  {result.note}")

    return {"fetched": fetched, "new": stored, "per_source": dict(per_source),
            "since": since}


def window_documents(cfg, store: Store, issue, plan: SearchPlan | None,
                     log=None) -> list[Document]:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=issue.window_hours)
    tags = [q.tag for q in plan.queries] if plan else []
    documents = store.documents_in_window(since, query_tags=tags or None)
    if not documents and tags:
        # Nothing carries this issue's tags - data loaded by some other route.
        # Fall back to the whole window, but say so: with several issues in
        # one database this pulls in documents collected for the others.
        documents = store.documents_in_window(since)
        if documents and log:
            log(f"  ! no documents tagged for {issue.id}; falling back to all "
                f"{len(documents)} in the window")
    return documents
