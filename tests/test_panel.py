"""The panel of virtual opinion leaders: declared, mined, and versioned."""
from chorus.config import LeaderSpec
from chorus.models import Panel
from chorus.pipeline import panel as P
from chorus.store import Store

from conftest import leader, make_document, make_reading


def mined_from(cfg, issue, jev, rows):
    documents, readings = [], {}
    for i, (stance, speaker, text) in enumerate(rows):
        doc = make_document(f"d{i}", author=speaker, text=text)
        documents.append(doc)
        readings[doc.id] = make_reading(doc.id, stance=stance)
    return P.mine_panel(cfg, issue, documents, readings, jev, log=lambda *a: None)


def test_a_declared_panel_is_used_as_written(issue):
    issue = issue.model_copy(deep=True)
    issue.panel = [LeaderSpec(id="a", label="A", label_zh="甲", stance="hike",
                              description="argues for a hike", flip_on="cooler CPI")]
    panel = P.declared_panel(issue)
    assert panel.origin == "declared"
    assert panel.leaders[0].flip_on == "cooler CPI"


def test_no_panel_is_declared_when_the_issue_leaves_it_empty(issue):
    assert P.declared_panel(issue) is None


def test_mined_leaders_use_phrases_that_appear_in_the_documents(cfg, issue, jev):
    rows = [("hike", f"u{i}",
             f"core services inflation has not come down at all, point {i}")
            for i in range(4)]
    rows += [("hold", f"v{i}",
              f"the labour market is cooling fast this quarter, point {i}")
             for i in range(4)]
    panel = mined_from(cfg, issue, jev, rows)
    labels = {leader.label for leader in panel.leaders}
    assert labels
    for label in labels:
        assert any(label in text for _, _, text in rows)


def test_copy_paste_cannot_buy_a_seat_on_the_panel(cfg, issue, jev):
    """Six accounts posting one identical line are one voice in the
    vocabulary, exactly as they are one voice in the weighting."""
    spam = "BUY MY NEWSLETTER the fed will hike no matter what happens"
    rows = [("hike", f"spam{i}", spam) for i in range(6)]
    rows += [
        ("hike", "real0", "core services inflation has not come down at all"),
        ("hike", "real1", "core services inflation has not come down, so they hike"),
        ("hike", "real2", "look at core services inflation has not come down yet"),
    ]
    panel = mined_from(cfg, issue, jev, rows)
    labels = " ".join(leader.label for leader in panel.leaders)
    assert "newsletter" not in labels


def test_a_phrase_used_by_both_sides_is_nobodys_label(cfg, issue, jev):
    """'hold rates steady' turns up in hike arguments too - 'the people
    telling you they will hold rates steady are wrong'."""
    rows = [("hike", f"u{i}",
             f"anyone telling you they hold rates steady is selling something {i}")
            for i in range(4)]
    rows += [("hold", f"v{i}", f"they will hold rates steady this month {i}")
             for i in range(4)]
    panel = mined_from(cfg, issue, jev, rows)
    for item in panel.leaders:
        if "hold rates steady" in item.label:
            assert item.stance == "hold"


def test_two_windows_over_one_sentence_are_one_seat(cfg, issue, jev):
    """'futures imply a 38%' and 'chance of a hike' share no words and are
    the same bloc - the same documents wrote both."""
    rows = [("hike", f"u{i}",
             f"futures imply a 38% chance of a hike, case {i}") for i in range(4)]
    panel = mined_from(cfg, issue, jev, rows)
    assert len(panel.leaders) == 1


def test_seats_are_allocated_by_speakers_not_by_documents(cfg, issue, jev):
    """One prolific account does not earn its side another seat."""
    speakers = {"hike": {f"u{i}" for i in range(9)}, "hold": {"v0"}}
    allocation = P._allocate(6, speakers)
    assert allocation["hike"] > allocation["hold"]
    assert sum(allocation.values()) <= 6


def test_a_side_with_a_real_following_always_gets_a_seat(cfg):
    speakers = {"hike": {f"u{i}" for i in range(8)},
                "hold": {f"v{i}" for i in range(2)}}
    allocation = P._allocate(6, speakers)
    assert allocation["hold"] >= 1


def test_the_panel_version_changes_when_a_question_would_change(cfg):
    a = Panel(issue_id="i", leaders=[leader("x", description="argues one thing")])
    b = Panel(issue_id="i", leaders=[leader("x", description="argues another")])
    assert P.panel_version(a) != P.panel_version(b)


def test_the_panel_version_survives_a_cosmetic_rename(cfg):
    """Changing a display label must not re-judge every document."""
    a = Panel(issue_id="i", leaders=[leader("x", label="Hawks",
                                            description="argues for a hike")])
    b = Panel(issue_id="i", leaders=[leader("x", label="The Hawks",
                                            description="argues for a hike")])
    assert P.panel_version(a) == P.panel_version(b)


def test_a_declared_panel_beats_a_stored_one(cfg, issue, jev, tmp_path):
    issue = issue.model_copy(deep=True)
    issue.panel = [LeaderSpec(id="declared", label="D", stance="hike")]
    with Store(cfg.db_path) as store:
        store.save_panel(Panel(issue_id=issue.id, leaders=[leader("stored")],
                               origin="mined"))
        panel = P.load_or_build(cfg, store, issue, [], {}, jev, log=lambda *a: None)
    assert [x.id for x in panel.leaders] == ["declared"]


def test_a_mined_panel_is_reused_until_it_is_rebuilt(cfg, issue, jev):
    with Store(cfg.db_path) as store:
        store.save_panel(Panel(issue_id=issue.id, leaders=[leader("stored")],
                               origin="mined"))
        panel = P.load_or_build(cfg, store, issue, [], {}, jev, log=lambda *a: None)
        assert [x.id for x in panel.leaders] == ["stored"]

        rebuilt = P.load_or_build(cfg, store, issue, [], {}, jev, rebuild=True,
                                  log=lambda *a: None)
        assert rebuilt.leaders == []          # nothing to mine from


def test_mining_nothing_is_not_an_error(cfg, issue, jev):
    panel = P.mine_panel(cfg, issue, [], {}, jev, log=lambda *a: None)
    assert panel.leaders == [] and panel.origin == "mined"
