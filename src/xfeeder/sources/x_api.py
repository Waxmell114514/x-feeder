"""X (Twitter) API v2 recent-search source.

Read access is metered and expensive, so this source is written to be
frugal by default:
  * `since_id` cursors so a re-run only pays for genuinely new posts
  * retweets excluded (they carry no new text, only amplification)
  * a hard per-run post budget

Endpoint: GET /2/tweets/search/recent  (last 7 days)
Auth:     App-only bearer token  ->  X_BEARER_TOKEN
"""
from __future__ import annotations

import datetime as dt
import os

from .. import textutil
from ..http import HttpError, request
from ..models import Author, Metrics, Post
from .base import SourceResult

BASE = "https://api.x.com/2"

TWEET_FIELDS = (
    "id,text,created_at,author_id,lang,public_metrics,conversation_id,"
    "referenced_tweets,possibly_sensitive,note_tweet"
)
USER_FIELDS = "id,username,name,description,created_at,public_metrics,verified,verified_type"


class XApiSource:
    name = "x_api"

    def __init__(self, cfg):
        self.cfg = cfg
        self.token = os.environ.get(cfg.source.bearer_token_env, "")
        self.store = None          # injected by the ingest stage for cursors
        self.budget = 10_000

    # ------------------------------------------------------------------
    def search(self, query: str, since: dt.datetime, max_results: int = 100,
               tag: str = "") -> SourceResult:
        if not self.token:
            raise RuntimeError(
                f"{self.cfg.source.bearer_token_env} is not set. Export an X API "
                "app-only bearer token, or switch source.provider to 'fixture'."
            )

        q = query
        if self.cfg.source.exclude_retweets and "-is:retweet" not in q:
            q = f"({q}) -is:retweet"

        since_id = None
        if self.store is not None:
            since_id = self.store.get_cursor(f"x_api:{tag or query}")

        params = {
            "query": q,
            "max_results": max(10, min(100, max_results)),
            "tweet.fields": TWEET_FIELDS,
            "user.fields": USER_FIELDS,
            "expansions": "author_id",
        }
        # since_id and start_time are mutually exclusive in the API.
        if since_id:
            params["since_id"] = since_id
        else:
            params["start_time"] = since.astimezone(dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        posts: list[Post] = []
        authors: dict[str, Author] = {}
        newest_id = since_id
        pages = 0
        note = ""

        while pages < self.cfg.source.max_pages and len(posts) < max_results:
            try:
                payload = request(
                    "GET", f"{BASE}/tweets/search/recent",
                    params=params,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
            except HttpError as e:
                if e.status in (401, 403):
                    raise RuntimeError(
                        "X API rejected the token (%d). Recent search needs a paid "
                        "tier; check the token and your access level." % e.status
                    ) from e
                raise

            data = payload.get("data") or []
            users = {u["id"]: u for u in (payload.get("includes", {}).get("users") or [])}

            for u in users.values():
                a = _to_author(u)
                authors[a.id] = a
            for t in data:
                p = _to_post(t, tag or query)
                if p is None:
                    continue
                posts.append(p)
                if newest_id is None or int(p.id) > int(newest_id):
                    newest_id = p.id

            meta = payload.get("meta", {})
            token = meta.get("next_token")
            pages += 1
            if not token or len(posts) >= max_results:
                break
            params["next_token"] = token

        if self.store is not None and newest_id:
            self.store.set_cursor(f"x_api:{tag or query}", newest_id)

        if since_id and not posts:
            note = "no new posts since last cursor"
        return SourceResult(posts=posts[:max_results],
                            authors=list(authors.values()), note=note)


def _to_author(u: dict) -> Author:
    pm = u.get("public_metrics", {}) or {}
    created = u.get("created_at")
    return Author(
        id=u["id"],
        handle=u.get("username", ""),
        name=u.get("name", ""),
        followers=pm.get("followers_count", 0),
        following=pm.get("following_count", 0),
        verified=bool(u.get("verified")) or u.get("verified_type") in ("blue", "business", "government"),
        description=u.get("description", "") or "",
        created_at=_parse_ts(created) if created else None,
        lang_hint=textutil.detect_lang(u.get("description", "") or ""),
    )


def _to_post(t: dict, tag: str) -> Post | None:
    refs = t.get("referenced_tweets") or []
    kinds = {r.get("type") for r in refs}
    if "retweeted" in kinds:
        return None
    pm = t.get("public_metrics", {}) or {}
    # note_tweet carries the untruncated body of long posts
    text = (t.get("note_tweet") or {}).get("text") or t.get("text", "")
    return Post(
        id=t["id"],
        author_id=t["author_id"],
        text=text,
        lang=t.get("lang") or textutil.detect_lang(text),
        created_at=_parse_ts(t["created_at"]),
        metrics=Metrics(
            like=pm.get("like_count", 0),
            retweet=pm.get("retweet_count", 0),
            reply=pm.get("reply_count", 0),
            quote=pm.get("quote_count", 0),
            impression=pm.get("impression_count", 0),
            bookmark=pm.get("bookmark_count", 0),
        ),
        url=f"https://x.com/i/web/status/{t['id']}",
        is_reply="replied_to" in kinds,
        is_quote="quoted" in kinds,
        conversation_id=t.get("conversation_id"),
        referenced_id=refs[0]["id"] if refs else None,
        query_tag=tag,
    )


def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
