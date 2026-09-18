"""The source interface: one method, one shape.

Every collector in this package - Reddit, Hacker News, Google News, an
arbitrary RSS feed, a JSONL replay - takes a `PlannedQuery` and returns
`Document`s. Nothing downstream knows which one produced what, which is why
adding a source is a file, not a refactor.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from ..models import Document, PlannedQuery


@dataclass
class SourceResult:
    documents: list[Document] = field(default_factory=list)
    note: str = ""


class Source(Protocol):
    name: str

    def fetch(self, query: PlannedQuery, since: dt.datetime) -> SourceResult:
        ...


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def from_epoch(seconds: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(float(seconds), dt.timezone.utc)


def stable_id(*parts: str) -> str:
    """A deterministic short id for a document that has no native one.

    Must not use the builtin hash(): it is salted per process, so the same
    article would get a new id on every run and the store would never
    recognise it as one it already has.
    """
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]
