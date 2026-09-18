"""RSS and Atom parsing, with the stdlib.

Feeds are the one format every publisher on earth already exposes, which is
what makes them the escape hatch for anything these built-in sources do not
reach - a Chinese outlet, a regulator's press page, a niche blog, an
RSSHub route. So this parser is deliberately forgiving: it accepts RSS 2.0
and Atom, ignores namespaces it does not know, and never raises on a feed
that is merely untidy.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import urllib.parse
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(value: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", value or ""))).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node, *names: str) -> str:
    for child in node:
        if _local(child.tag) in names:
            return (child.text or "").strip()
    return ""


def _link(node) -> str:
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href:
            # Atom: several links, the alternate is the article itself.
            if child.get("rel") in (None, "alternate"):
                return href
            continue
        if child.text:
            return child.text.strip()
    return ""


def parse_date(value: str) -> Optional[dt.datetime]:
    value = (value or "").strip()
    if not value:
        return None
    try:                                   # RFC 822, as RSS uses
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except (TypeError, ValueError, IndexError):
        pass
    try:                                   # ISO 8601, as Atom uses
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


class Entry:
    __slots__ = ("id", "title", "summary", "link", "published", "author", "source_name",
                 "source_url")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot, "") if slot != "published" else kw.get(slot))


def parse(xml: str) -> list[Entry]:
    try:
        root = ElementTree.fromstring(xml.strip())
    except ElementTree.ParseError:
        return []

    nodes = [n for n in root.iter() if _local(n.tag) in ("item", "entry")]
    out: list[Entry] = []
    for node in nodes:
        title = strip_html(_text(node, "title"))
        summary = strip_html(_text(node, "description", "summary", "content"))
        link = _link(node)
        published = parse_date(_text(node, "pubDate", "published", "updated", "date"))
        author = strip_html(_text(node, "creator", "author", "name"))

        source_name = source_url = ""
        for child in node:
            if _local(child.tag) == "source":
                source_name = (child.text or "").strip()
                source_url = child.get("url", "")
        guid = _text(node, "guid", "id") or link or title
        out.append(Entry(id=guid, title=title, summary=summary, link=link,
                         published=published, author=author,
                         source_name=source_name, source_url=source_url))
    return out


def host_of(url: str) -> str:
    try:
        netloc = urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.split(":")[0]
