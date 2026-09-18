import datetime as dt
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from chorus.config import load_config                                # noqa: E402
from chorus.jev import JevClient                                     # noqa: E402
from chorus.models import Document, Engagement, Leader, Panel, Reading  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def cfg(tmp_path):
    c = load_config(REPO / "config" / "demo.yaml")
    c.source.fixture_path = str(REPO / "fixtures" / "fed_rate_demo.jsonl")
    c.db_path = str(tmp_path / "test.db")
    c.jev.cache_dir = str(tmp_path / "jev-cache")
    c.output_dir = str(tmp_path / "out")
    return c


@pytest.fixture
def issue(cfg):
    return cfg.issue("fed-rate")


@pytest.fixture
def jev(cfg):
    """The offline client: same interface, keyword stand-in behind it."""
    cfg.jev.offline = True
    return JevClient(cfg)


def make_document(doc_id, channel="r/economics", author="someone", text="x",
                  title="", hours_ago=1.0, score=0, comments=0, source="reddit",
                  kind="forum"):
    return Document(
        id=doc_id, source=source, channel=channel, channel_kind=kind,
        author=author, title=title, text=text,
        created_at=NOW - dt.timedelta(hours=hours_ago),
        engagement=Engagement(score=score, comments=comments),
    )


def make_reading(doc_id, stance="hike", stated=None, intensity=0.7,
                 speaks_for="own_view", confidence=0.9, relevant=True,
                 sarcastic=False, is_question=False):
    return Reading(
        doc_id=doc_id, issue_id="fed-rate", relevant=relevant, relevance=0.9,
        stance=stance, stance_confidence=confidence, speaks_for=speaks_for,
        intensity=intensity, sarcastic=sarcastic, is_question=is_question,
        stated_probability=stated, reader_version="test",
    )


def make_panel(*leaders):
    return Panel(issue_id="fed-rate", leaders=list(leaders), origin="declared")


def leader(lid, stance="hike", label=None, description=""):
    return Leader(id=lid, label=label or lid, label_zh=label or lid,
                  stance=stance, description=description or f"argues {stance}")
