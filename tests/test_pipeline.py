"""End to end over the bundled documents, with the keyword stand-in."""
import pathlib

import pytest

from chorus.pipeline import panel as panel_mod
from chorus.pipeline import plan as plan_mod
from chorus.pipeline.assign import run_assign
from chorus.pipeline.ingest import run_ingest, window_documents
from chorus.pipeline.read import run_read
from chorus.pipeline.synthesize import run_synthesize
from chorus.pipeline.tier import run_tier
from chorus.render import html as html_render
from chorus.store import Store

QUIET = lambda *a, **k: None          # noqa: E731


def words_in_order(phrase: str, text: str) -> bool:
    """Is `phrase` the document's own words, in the document's own order?

    Not a substring test: the miner works on word tokens, so punctuation
    inside a span is elided - "rates steady next week, economists" comes
    back without the comma. Everything else must match exactly.
    """
    from chorus import textutil
    needle = textutil.tokens(phrase)
    haystack = textutil.tokens(text)
    if not needle:
        return False
    for i in range(len(haystack) - len(needle) + 1):
        if haystack[i:i + len(needle)] == needle:
            return True
    return False


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One full cycle, shared by every test below."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from chorus.config import load_config
    from chorus.jev import JevClient

    repo = pathlib.Path(__file__).resolve().parents[1]
    tmp = tmp_path_factory.mktemp("run")
    cfg = load_config(repo / "config" / "demo.yaml")
    cfg.source.fixture_path = str(repo / "fixtures" / "fed_rate_demo.jsonl")
    cfg.db_path = str(tmp / "test.db")
    cfg.jev.cache_dir = str(tmp / "cache")
    cfg.output_dir = str(tmp / "out")
    cfg.jev.offline = True

    issue = cfg.issue("fed-rate")
    jev = JevClient(cfg)
    store = Store(cfg.db_path)

    plan = plan_mod.build_plan(cfg, issue, jev, log=QUIET)
    store.save_plan(plan)
    run_ingest(cfg, store, issue, plan, log=QUIET)
    documents = window_documents(cfg, store, issue, plan)
    run_tier(cfg, store, documents, jev, issue=issue, log=QUIET)
    run_read(cfg, store, issue, documents, jev, log=QUIET)
    readings = store.get_readings(issue.id)
    panel = panel_mod.load_or_build(cfg, store, issue, documents, readings, jev,
                                    log=QUIET)
    run_assign(cfg, store, issue, documents, readings, panel, jev, log=QUIET)
    assignments = store.get_assignments(issue.id)
    snap = run_synthesize(cfg, store, issue, documents, readings, panel,
                          assignments, log=QUIET)
    store.add_snapshot(snap)
    return {"cfg": cfg, "issue": issue, "store": store, "snap": snap,
            "documents": {d.id: d for d in documents}, "readings": readings,
            "panel": panel, "assignments": assignments, "tmp": tmp}


# ------------------------------------------------------------- collection
def test_every_source_in_the_plan_is_actually_collected(run):
    sources = {d.source for d in run["documents"].values()}
    assert {"reddit", "gnews", "rss", "hackernews"} <= sources


def test_documents_are_not_collected_twice_by_overlapping_queries(run):
    ids = list(run["documents"])
    assert len(ids) == len(set(ids))


# ------------------------------------------------------------------ tiers
def test_all_four_tiers_are_represented(run):
    assert set(run["snap"].tiers) == {"official", "pro_media", "expert", "crowd"}


def test_an_institutions_own_feed_is_official(run):
    tiers = run["store"].get_channel_tiers()
    assert tiers["federalreserve.gov"].tier == "official"
    assert tiers["federalreserve.gov"].method == "allowlist"


def test_a_subreddit_named_in_the_issue_file_keeps_its_tier(run):
    tiers = run["store"].get_channel_tiers()
    assert tiers["r/AskEconomics"].tier == "expert"
    assert tiers["r/economics"].tier == "crowd"


def test_officialdom_and_the_public_are_measured_separately(run):
    tiers = run["snap"].tiers
    assert tiers["official"].probability != tiers["crowd"].probability
    assert run["snap"].divergences


# --------------------------------------------------------------- reading
def test_the_leading_position_is_never_no_position(run):
    """A tier can be mostly undecided - that is what unclear's share says -
    but "the leading position is: no position" tells a reader nothing."""
    anchors = set(run["issue"].anchors())
    for verdict in run["snap"].tiers.values():
        if any(s in anchors for s in verdict.stance_shares):
            assert verdict.dominant_stance in anchors


def test_off_topic_documents_are_dropped(run):
    off_topic = [d for d in run["documents"].values()
                 if d.author == "off_topic_andy"]
    assert off_topic
    assert not run["readings"][off_topic[0].id].relevant


def test_a_policy_rate_is_never_read_as_a_probability(run):
    """4.25-4.50% is a rate. Reading it as odds destroyed the official tier's
    number in the previous generation of this project."""
    for doc in run["documents"].values():
        if "4.25-4.50%" in doc.body:
            assert run["readings"][doc.id].stated_probability is None


def test_a_stated_likelihood_is_picked_up(run):
    stated = {r.stated_probability for r in run["readings"].values()
              if r.stated_probability is not None}
    assert 0.38 in stated


# ----------------------------------------------------------------- panel
def test_the_panel_speaks_in_phrases_from_the_documents(run):
    corpus = " ".join(d.body for d in run["documents"].values())
    for leader in run["panel"].leaders:
        assert words_in_order(leader.label, corpus)


def test_every_leader_holds_a_stance_on_the_axis(run):
    valid = set(run["issue"].stance_ids())
    assert all(leader.stance in valid for leader in run["panel"].leaders)


def test_a_delegate_never_contradicts_its_own_bloc(run):
    for verdict in run["snap"].tiers.values():
        for delegate in verdict.delegates:
            for quote in delegate.quotes:
                assert run["readings"][quote.doc_id].stance == delegate.stance


# ------------------------------------------------------------- arithmetic
def test_delegate_shares_never_exceed_the_tier(run):
    for verdict in run["snap"].tiers.values():
        assigned = sum(d.share for d in verdict.delegates)
        assert assigned <= 1.0001
        accounted = assigned + verdict.unassigned_share + verdict.undecided_share
        assert abs(accounted - 1.0) < 0.02


def test_taking_no_side_is_not_counted_against_the_panel(run):
    """A document that took no position could not have joined any bloc, so
    it belongs in `undecided`, not in the panel's gap."""
    anchors = set(run["issue"].anchors())
    for tier, verdict in run["snap"].tiers.items():
        undecided = sum(
            share for stance, share in verdict.stance_shares.items()
            if stance not in anchors
        )
        assert abs(verdict.undecided_share - undecided) < 0.08


def test_stance_shares_sum_to_one(run):
    for verdict in run["snap"].tiers.values():
        assert abs(sum(verdict.stance_shares.values()) - 1.0) < 1e-6


def test_a_flooding_account_is_capped(run):
    """One account posted nine times in the crowd tier."""
    cfg, snap = run["cfg"], run["snap"]
    crowd = [d for d in run["documents"].values()
             if d.author == "loud_poster"]
    assert len(crowd) == 9
    cap = cfg.weighting.speaker_cap_pct
    verdict = snap.tiers["crowd"]
    for delegate in verdict.delegates:
        speakers = {q.speaker for q in delegate.quotes}
        assert "loud_poster" not in speakers or delegate.share <= cap * 20


def test_astroturf_never_leads_a_bloc(run):
    """Six accounts posted one identical line. It must not be the loudest
    voice anywhere."""
    for verdict in run["snap"].tiers.values():
        for delegate in verdict.delegates:
            if delegate.quotes:
                assert not delegate.quotes[0].speaker.startswith("acct_")


def test_quotes_are_distinct_texts_and_distinct_speakers(run):
    for verdict in run["snap"].tiers.values():
        for delegate in verdict.delegates:
            speakers = [q.speaker for q in delegate.quotes]
            texts = [q.text for q in delegate.quotes]
            assert len(speakers) == len(set(speakers))
            assert len(texts) == len(set(texts))


def test_a_delegates_reasons_are_verbatim_from_its_own_documents(run):
    """The replacement for a model-written rationale is quotation, and it
    has to actually be quotation."""
    for verdict in run["snap"].tiers.values():
        for delegate in verdict.delegates:
            members = " ".join(
                run["documents"][doc_id].body
                for doc_id, a in run["assignments"].items()
                if a.leader_id == delegate.leader_id and doc_id in run["documents"]
            )
            for reason in delegate.rationale:
                assert words_in_order(reason, members)


# ------------------------------------------------------------- the report
def test_every_sentence_in_the_report_is_populated(run):
    snap = run["snap"]
    assert snap.global_headline
    assert snap.notes
    for verdict in snap.tiers.values():
        assert verdict.headline


def test_snapshots_round_trip_through_sqlite(run):
    store, snap = run["store"], run["snap"]
    restored = store.latest_snapshots("fed-rate", limit=1)[0]
    assert restored.global_headline == snap.global_headline
    assert set(restored.tiers) == set(snap.tiers)
    assert [x.id for x in restored.panel] == [x.id for x in snap.panel]


def test_the_html_report_renders(run):
    out = pathlib.Path(run["tmp"]) / "report.html"
    html_render.render(run["snap"], run["issue"], alerts=[], lang="zh",
                       out_path=out)
    body = out.read_text(encoding="utf-8")
    assert "chorus" in body
    assert run["snap"].global_headline[:12] in body


def test_re_running_a_stage_costs_nothing(run):
    cfg, store, issue = run["cfg"], run["store"], run["issue"]
    from chorus.jev import JevClient
    jev = JevClient(cfg)
    documents = list(run["documents"].values())
    info = run_read(cfg, store, issue, documents, jev, log=QUIET)
    assert info["new"] == 0
    assert jev.usage["questions"] == 0


def test_a_tier_that_mostly_took_no_side_reports_no_number(cfg, issue):
    """18% of a tier saying 'hike' is not the tier saying 90%."""
    import datetime as dt
    from chorus.models import Panel
    from chorus.pipeline.synthesize import run_synthesize
    from chorus.store import Store
    from conftest import make_document, make_reading

    documents, readings = [], {}
    for i in range(12):
        doc = make_document(f"d{i}", author=f"u{i}", text="x" * 200,
                            channel="r/economics")
        documents.append(doc)
        readings[doc.id] = make_reading(
            doc.id, stance="hike" if i < 2 else "unclear")

    with Store(cfg.db_path) as store:
        store.upsert_channel_tiers([])
        snap = run_synthesize(cfg, store, issue, documents, readings,
                              Panel(issue_id=issue.id), {}, log=QUIET)
    crowd = snap.tiers["crowd"]
    assert crowd.probability is None
    assert crowd.n_docs == 12
    assert any("表了态" in n or "took a side" in n for n in snap.notes)


def test_one_tier_with_a_reading_is_not_the_tiers_agreeing(cfg, issue):
    """"The tiers agree" is true of a single number and false about the
    world."""
    from chorus import phrasing
    one = phrasing.global_headline(blended=0.9, top_divergence=None,
                                   dominant_label="加息", n_docs=47,
                                   n_readings=1, only_tier="大众讨论")
    many = phrasing.global_headline(blended=0.9, top_divergence=None,
                                    dominant_label="加息", n_docs=47,
                                    n_readings=3)
    assert "大众讨论" in one and "一致" not in one
    assert "一致" in many
