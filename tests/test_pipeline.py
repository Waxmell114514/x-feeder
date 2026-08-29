"""End-to-end on the bundled fixture. No network, no keys."""
import datetime as dt

import pytest
from conftest import REPO

from xfeeder import textutil
from xfeeder.pipeline.cohort import run_classify
from xfeeder.pipeline.extract import run_extract
from xfeeder.pipeline.ingest import run_ingest, window_posts
from xfeeder.pipeline.synthesize import run_synthesize
from xfeeder.store import Store


@pytest.fixture
def run(cfg, tmp_path):
    cfg.db_path = str(tmp_path / "t.db")
    cfg.llm.cache_dir = str(tmp_path / "cache")
    cfg.llm.offline = True
    store = Store(cfg.db_path)
    run_ingest(cfg, store, "fed-rate", log=lambda *a: None)
    posts = window_posts(cfg, store, "fed-rate")
    run_classify(cfg, store, posts, llm=None, log=lambda *a: None)
    run_extract(cfg, store, "fed-rate", posts, llm=None, log=lambda *a: None)
    snap = run_synthesize(cfg, store, "fed-rate", posts, llm=None,
                          log=lambda *a: None)
    yield cfg, store, snap, posts
    store.close()


def test_all_five_tiers_are_represented(run):
    _, _, snap, _ = run
    assert set(snap.cohorts) == {"official", "pro_media", "en_kol", "cn_kol", "crowd"}


def test_every_tier_gets_at_least_one_delegate(run):
    _, _, snap, _ = run
    for cohort, v in snap.cohorts.items():
        assert v.delegates, f"{cohort} produced no voice"
        assert all(d.verdict for d in v.delegates)


def test_delegate_shares_never_exceed_the_tier(run):
    _, _, snap, _ = run
    for v in snap.cohorts.values():
        assert sum(d.share for d in v.delegates) <= 1.0 + 1e-6


def test_every_delegate_cites_real_posts(run):
    """The synthesis must be traceable: no invented citations."""
    _, store, snap, posts = run
    known = {p.id for p in posts}
    for v in snap.cohorts.values():
        for d in v.delegates:
            assert d.quotes, f"{d.name} cites nothing"
            for q in d.quotes:
                assert q.post_id in known


def test_delegate_counts_match_their_cluster(run):
    _, _, snap, _ = run
    for v in snap.cohorts.values():
        for d in v.delegates:
            assert d.n_authors <= d.n_posts
            assert 0.0 < d.share <= 1.0


def test_stance_shares_sum_to_one(run):
    _, _, snap, _ = run
    for v in snap.cohorts.values():
        assert sum(v.stance_shares.values()) == pytest.approx(1.0)


def test_the_crowd_and_officialdom_are_measured_separately(run):
    """The whole point: tiers are not averaged into one number."""
    _, _, snap, _ = run
    assert snap.cohorts["crowd"].probability != snap.cohorts["official"].probability
    assert snap.blended_probability is not None


def test_a_flooding_account_is_capped(run):
    """One account posting nine times must not become the crowd's majority."""
    _, store, snap, posts = run
    crowd = snap.cohorts["crowd"]
    for d in crowd.delegates:
        assert d.n_authors > 1 or d.share <= 0.10


def test_astroturf_never_leads_a_bloc(run):
    """Six accounts posting one identical line is damped, and the copied
    line is never the bloc's strongest quote."""
    _, _, snap, _ = run
    for d in snap.cohorts["crowd"].delegates:
        assert "Join our channel" not in d.quotes[0].text


def test_quotes_are_distinct_texts_and_distinct_accounts(run):
    """A reader must never be shown five copies of the same post."""
    _, _, snap, _ = run
    for v in snap.cohorts.values():
        for d in v.delegates:
            texts = [textutil.text_hash(q.text) for q in d.quotes]
            handles = [q.handle for q in d.quotes]
            assert len(set(texts)) == len(texts)
            assert len(set(handles)) == len(handles)


def test_rerunning_extraction_is_free(run):
    cfg, store, _, posts = run
    info = run_extract(cfg, store, "fed-rate", posts, llm=None, log=lambda *a: None)
    assert info["new"] == 0 and info["reused"] == len(posts)


def test_snapshots_round_trip_through_sqlite(run):
    _, store, snap, _ = run
    store.add_snapshot(snap)
    back = store.latest_snapshots("fed-rate", limit=1)[0]
    assert back.issue_id == snap.issue_id
    assert set(back.cohorts) == set(snap.cohorts)
    assert back.cohorts["crowd"].delegates[0].verdict == \
        snap.cohorts["crowd"].delegates[0].verdict


def test_html_report_renders(run, tmp_path):
    cfg, store, snap, _ = run
    from xfeeder.render import html
    out = html.render(snap, cfg.issue("fed-rate"), alerts=[], lang="zh",
                      history=[snap], out_path=tmp_path / "r.html")
    body = out.read_text(encoding="utf-8")
    assert "大众用户" in body and "{{" not in body


def test_query_tags_are_stable_across_processes(cfg):
    """A tag derived from builtin hash() would change every run, silently
    breaking the since_id cursor and the window filter."""
    import subprocess
    import sys

    from xfeeder.pipeline.ingest import query_tag

    issue = cfg.issue("fed-rate")
    q = type(issue.queries[0])(cohort="crowd", query="some query text")
    here = query_tag(issue, q)

    code = (
        "import sys; sys.path.insert(0, %r);"
        "from xfeeder.config import load_config;"
        "from xfeeder.pipeline.ingest import query_tag;"
        "i = load_config(%r).issue('fed-rate');"
        "Q = type(i.queries[0]);"
        "print(query_tag(i, Q(cohort='crowd', query='some query text')))"
        % (str(REPO / "src"), str(REPO / "config" / "demo.yaml"))
    )
    # PYTHONHASHSEED is randomised per process by default; that is the point.
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == here
