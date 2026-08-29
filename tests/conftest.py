import datetime as dt
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from xfeeder.config import load_config                    # noqa: E402
from xfeeder.models import Author, Extraction, Metrics, Post   # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 8, 29, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def cfg():
    c = load_config(REPO / "config" / "demo.yaml")
    c.source.fixture_path = str(REPO / "fixtures" / "fed_rate_demo.jsonl")
    return c


@pytest.fixture
def issue(cfg):
    return cfg.issue("fed-rate")


def make_post(pid, author_id, text="x", hours_ago=1.0, like=0, retweet=0, quote=0):
    return Post(
        id=pid, author_id=author_id, text=text,
        created_at=NOW - dt.timedelta(hours=hours_ago),
        metrics=Metrics(like=like, retweet=retweet, quote=quote),
    )


def make_author(aid, followers=1000, verified=False, age_days=1000):
    return Author(id=aid, handle=aid, followers=followers, verified=verified,
                  created_at=NOW - dt.timedelta(days=age_days))


def make_extraction(pid, stance="hike", probability=None, intensity=0.7,
                    kind="opinion"):
    return Extraction(post_id=pid, issue_id="fed-rate", relevant=True,
                      stance=stance, probability=probability, intensity=intensity,
                      reasoning_kind=kind)
