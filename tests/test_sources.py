"""Collectors: feed parsing, document mapping, and stable identity."""
import datetime as dt

from chorus.models import PlannedQuery
from chorus.sources import feedparse, stable_id
from chorus.sources.fixture import _matches
from chorus.sources.gnews import _document as gnews_document
from chorus.sources.hackernews import _document as hn_document
from chorus.sources.reddit import _comment_document, _thread_document

from conftest import make_document

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Fed holds rates &amp; signals patience - Reuters</title>
    <description>&lt;p&gt;The central bank left rates unchanged.&lt;/p&gt;</description>
    <link>https://www.reuters.com/markets/story</link>
    <pubDate>Wed, 16 Sep 2026 10:00:00 GMT</pubDate>
    <source url="https://www.reuters.com">Reuters</source>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Press release</title>
    <summary>Policy statement</summary>
    <link rel="alternate" href="https://www.federalreserve.gov/a.htm"/>
    <updated>2026-09-16T10:00:00Z</updated>
    <id>tag:fed,2026:a</id>
  </entry>
</feed>"""


def test_rss_is_parsed_including_entities_and_html():
    entry = feedparse.parse(RSS)[0]
    assert entry.title == "Fed holds rates & signals patience - Reuters"
    assert entry.summary == "The central bank left rates unchanged."
    assert entry.published.year == 2026
    assert feedparse.host_of(entry.source_url) == "reuters.com"


def test_atom_is_parsed_too():
    entry = feedparse.parse(ATOM)[0]
    assert entry.title == "Press release"
    assert entry.link.endswith("a.htm")
    assert entry.published.tzinfo is not None


def test_a_broken_feed_returns_nothing_rather_than_raising():
    assert feedparse.parse("<not xml") == []
    assert feedparse.parse("") == []


def test_ids_are_stable_across_processes():
    """A salted hash would give the same article a new id every run, and the
    store would never recognise one it already had."""
    assert stable_id("a", "b") == stable_id("a", "b")
    assert stable_id("a", "b") != stable_id("a", "c")


def test_google_news_names_the_outlet_as_the_venue():
    doc = gnews_document(feedparse.parse(RSS)[0], "tag")
    assert doc.channel == "reuters.com"
    assert doc.channel_kind == "outlet"
    assert doc.speaker == "reuters.com"       # an outlet piece is the outlet


def test_google_news_strips_the_outlet_suffix_from_the_headline():
    """Google appends ' - Outlet' to every headline; keeping it in the text
    would put the masthead into the reading."""
    doc = gnews_document(feedparse.parse(RSS)[0], "tag")
    assert doc.title == "Fed holds rates & signals patience"


def test_a_reddit_thread_maps_to_a_document():
    doc = _thread_document({
        "id": "abc", "subreddit": "economics", "author": "someone",
        "title": "Will they hike?", "selftext": "asking",
        "permalink": "/r/economics/abc", "created_utc": 1789000000,
        "score": 12, "num_comments": 3, "num_crossposts": 1,
    }, "tag")
    assert doc.id == "reddit:abc"
    assert doc.channel == "r/economics"
    assert doc.speaker == "reddit/someone"
    assert doc.engagement.comments == 3


def test_a_stickied_thread_is_skipped():
    assert _thread_document({"id": "a", "stickied": True}, "t") is None


def test_a_comment_carries_the_thread_it_answers():
    """A bare 'yes, exactly this' is unreadable without the question."""
    thread = make_document("reddit:t1", channel="r/economics",
                           title="Will they hike?")
    doc = _comment_document({"id": "c1", "author": "u", "body": "yes, exactly",
                             "permalink": "/r/economics/c1",
                             "created_utc": 1789000000, "score": 4}, thread, "tag")
    assert doc.title == "Will they hike?"
    assert "yes, exactly" in doc.body


def test_deleted_comments_are_skipped():
    thread = make_document("reddit:t1")
    for body in ("[deleted]", "[removed]", ""):
        assert _comment_document({"id": "c", "body": body}, thread, "t") is None


def test_hacker_news_maps_stories_and_comments():
    story = hn_document({"objectID": "1", "title": "Fed holds", "author": "a",
                         "points": 30, "num_comments": 5,
                         "created_at_i": 1789000000}, "tag")
    comment = hn_document({"objectID": "2", "story_title": "Fed holds",
                           "comment_text": "I disagree", "author": "b",
                           "created_at_i": 1789000000}, "tag")
    assert story.channel == comment.channel == "news.ycombinator.com"
    assert "I disagree" in comment.body


# ------------------------------------------------------------ the replay
def test_the_fixture_answers_a_query_the_way_a_collector_would():
    doc = make_document("reddit:1", channel="r/economics", source="reddit")
    subreddit = PlannedQuery(source="reddit", channel="economics", tag="t")
    elsewhere = PlannedQuery(source="reddit", channel="investing", tag="t")
    news = PlannedQuery(source="gnews", tag="t")
    assert _matches(subreddit, doc)
    assert not _matches(elsewhere, doc)
    assert not _matches(news, doc)


def test_a_site_wide_query_matches_every_venue_on_that_source():
    doc = make_document("reddit:1", channel="r/whatever", source="reddit")
    assert _matches(PlannedQuery(source="reddit", tag="t"), doc)


def test_a_feed_query_matches_by_channel():
    doc = make_document("rss:1", channel="federalreserve.gov", source="rss")
    query = PlannedQuery(source="rss", channel="https://www.federalreserve.gov/f.xml",
                         options={"channel": "federalreserve.gov"}, tag="t")
    assert _matches(query, doc)
