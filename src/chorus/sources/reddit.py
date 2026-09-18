"""Reddit, through the public JSON endpoints.

Any listing on Reddit is also JSON: append `.json` to the path and you get
the same page a browser gets, with no key, no OAuth, and no per-read
metering. That is the whole reason this project moved off X - the cost of a
read went from "a paid tier" to "be polite".

Polite means: a real User-Agent, one request at a time, and a hard cap on
pages, because anonymous traffic is throttled per IP and a 429 here costs
the whole run's coverage, not one document.

Two shapes are collected:
  * threads, from a search over a subreddit or over the whole site;
  * optionally, the top-level comments on the threads a search returned,
    which is where most of the actual arguing happens - a thread title is
    a topic, its comments are positions.
"""
from __future__ import annotations

import datetime as dt

from ..http import HttpError, request
from ..models import Document, Engagement, PlannedQuery
from .base import SourceResult, from_epoch

BASE = "https://www.reddit.com"


class RedditSource:
    name = "reddit"

    def __init__(self, cfg):
        self.cfg = cfg
        self.headers = {"User-Agent": cfg.source.user_agent,
                        "Accept": "application/json"}

    # ------------------------------------------------------------------
    def fetch(self, query: PlannedQuery, since: dt.datetime) -> SourceResult:
        opts = query.options
        params = {
            "q": query.query,
            "sort": opts.get("sort") or "new",
            "t": opts.get("time") or "week",
            "limit": min(100, max(10, query.max_results)),
            "raw_json": 1,
            "type": "link",
        }
        if query.channel:
            path = f"/r/{query.channel.lstrip('r/')}/search.json"
            params["restrict_sr"] = 1
        else:
            path = "/search.json"

        documents: list[Document] = []
        note = ""
        after = None
        pages = 0
        while pages < self.cfg.source.max_pages and len(documents) < query.max_results:
            if after:
                params["after"] = after
            try:
                payload = request("GET", BASE + path, params=params,
                                  headers=self.headers,
                                  timeout=self.cfg.source.request_timeout,
                                  retries=2)
            except HttpError as e:
                if e.status in (403, 429):
                    # Anonymous access is throttled per IP and blocked outright
                    # from some hosts. Say which, because the fix differs.
                    raise RuntimeError(
                        f"Reddit refused the request ({e.status}). Anonymous JSON "
                        f"access is rate-limited per IP; slow down, or set a "
                        f"descriptive source.user_agent."
                    ) from e
                raise
            children = (payload.get("data") or {}).get("children") or []
            for child in children:
                doc = _thread_document(child.get("data") or {}, query.tag)
                if doc is None or doc.created_at < since:
                    continue
                documents.append(doc)
            after = (payload.get("data") or {}).get("after")
            pages += 1
            if not after or not children:
                break

        documents = documents[: query.max_results]
        if opts.get("include_comments") and documents:
            extra = self._comments(documents, query, since,
                                   limit=int(opts.get("max_comment_threads", 6)))
            note = f"{len(extra)} comments from {min(len(documents), 6)} threads"
            documents.extend(extra)
        return SourceResult(documents=documents, note=note)

    # ------------------------------------------------------------------
    def _comments(self, threads: list[Document], query: PlannedQuery,
                  since: dt.datetime, limit: int = 6) -> list[Document]:
        """Top-level comments on the busiest threads we just found."""
        busiest = sorted(threads, key=lambda d: -d.engagement.comments)[:limit]
        out: list[Document] = []
        for thread in busiest:
            native = thread.id.split(":", 1)[1]
            try:
                payload = request(
                    "GET", f"{BASE}/comments/{native}.json",
                    params={"limit": 40, "depth": 1, "sort": "top", "raw_json": 1},
                    headers=self.headers,
                    timeout=self.cfg.source.request_timeout, retries=1,
                )
            except (HttpError, RuntimeError):
                continue                      # a thread we cannot read is not fatal
            if not isinstance(payload, list) or len(payload) < 2:
                continue
            for child in (payload[1].get("data") or {}).get("children") or []:
                doc = _comment_document(child.get("data") or {}, thread, query.tag)
                if doc is not None and doc.created_at >= since:
                    out.append(doc)
        return out


# ----------------------------------------------------------------------
def _thread_document(data: dict, tag: str) -> Document | None:
    if not data.get("id") or data.get("stickied"):
        return None
    subreddit = data.get("subreddit") or ""
    return Document(
        id=f"reddit:{data['id']}",
        source="reddit",
        channel=f"r/{subreddit}" if subreddit else "reddit",
        channel_kind="forum",
        author=data.get("author") or "",
        title=data.get("title") or "",
        text=(data.get("selftext") or "")[:8000],
        url=f"https://www.reddit.com{data.get('permalink', '')}",
        created_at=from_epoch(data.get("created_utc") or 0),
        engagement=Engagement(
            score=max(0, int(data.get("score") or 0)),
            comments=int(data.get("num_comments") or 0),
            reposts=int(data.get("num_crossposts") or 0),
        ),
        query_tag=tag,
        raw={"flair": data.get("link_flair_text") or "",
             "is_self": bool(data.get("is_self"))},
    )


def _comment_document(data: dict, thread: Document, tag: str) -> Document | None:
    body = (data.get("body") or "").strip()
    if not data.get("id") or not body or body in ("[deleted]", "[removed]"):
        return None
    return Document(
        id=f"reddit:{data['id']}",
        source="reddit",
        channel=thread.channel,
        channel_kind="forum",
        author=data.get("author") or "",
        # The thread title is the context the comment was written into, and
        # a bare "yes, exactly this" is unreadable without it.
        title=thread.title,
        text=body[:8000],
        url=f"https://www.reddit.com{data.get('permalink', '')}",
        created_at=from_epoch(data.get("created_utc") or 0),
        engagement=Engagement(score=max(0, int(data.get("score") or 0))),
        query_tag=tag,
        raw={"reply_to": thread.id},
    )
