from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol

from ..models import Author, Post


@dataclass
class SourceResult:
    posts: list[Post] = field(default_factory=list)
    authors: list[Author] = field(default_factory=list)
    note: str = ""


class Source(Protocol):
    name: str

    def search(self, query: str, since: dt.datetime, max_results: int,
               tag: str = "") -> SourceResult:
        ...
