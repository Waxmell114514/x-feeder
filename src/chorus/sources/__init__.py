"""Collectors, and the table that maps a planned query to one."""
from __future__ import annotations

from .base import Source, SourceResult, stable_id
from .fixture import FixtureSource
from .gnews import GoogleNewsSource
from .hackernews import HackerNewsSource
from .reddit import RedditSource
from .rss import RssSource

BUILDERS = {
    "reddit": RedditSource,
    "hackernews": HackerNewsSource,
    "gnews": GoogleNewsSource,
    "rss": RssSource,
    "fixture": FixtureSource,
}


def build_sources(cfg) -> dict[str, Source]:
    """One instance per source name, reused across a run.

    In fixture mode every planned query is answered from the file, whatever
    source it names - that is what lets a demo config exercise the same plan
    the live one would run.
    """
    if cfg.source.provider == "fixture":
        shared = FixtureSource(cfg)
        return {name: shared for name in BUILDERS}
    return {name: builder(cfg) for name, builder in BUILDERS.items()}


__all__ = ["Source", "SourceResult", "build_sources", "stable_id",
           "FixtureSource", "RedditSource", "HackerNewsSource",
           "GoogleNewsSource", "RssSource", "BUILDERS"]
