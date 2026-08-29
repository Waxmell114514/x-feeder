import numpy as np
import pytest
from conftest import make_extraction, make_post

from xfeeder.pipeline.cluster import _agglomerate, _leader, build_clusters


def _vectors(groups, dim=24, noise=0.05, seed=0):
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(len(groups), dim))
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    rows = [c + noise * rng.normal(size=dim) for c, n in zip(centres, groups)
            for _ in range(n)]
    v = np.asarray(rows)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_agglomerative_recovers_separated_groups():
    v = _vectors([6, 6, 6])
    assert sorted(len(g) for g in _agglomerate(v, 0.6)) == [6, 6, 6]


def test_leader_fallback_recovers_the_same_groups():
    v = _vectors([6, 6, 6])
    assert sorted(len(g) for g in _leader(v, 0.6)) == [6, 6, 6]


def test_degenerate_inputs():
    assert _agglomerate(np.zeros((0, 4)), 0.6) == []
    assert _agglomerate(np.ones((1, 4)) / 2, 0.6) == [[0]]


def test_a_high_threshold_never_merges():
    v = _vectors([4, 4])
    assert all(len(g) == 1 for g in _agglomerate(v, 0.999))


def test_stances_are_never_merged_however_similar_the_text():
    """A bull and a bear are not one bloc, even saying the same words."""
    posts = {f"p{i}": make_post(f"p{i}", f"a{i}") for i in range(4)}
    ex = {"p0": make_extraction("p0", stance="hike"),
          "p1": make_extraction("p1", stance="hike"),
          "p2": make_extraction("p2", stance="hold"),
          "p3": make_extraction("p3", stance="hold")}
    for e in ex.values():
        e.key_claim = "identical text for every single post"

    clusters = build_clusters(
        issue_id="i", cohort="crowd", post_ids=list(posts), posts=posts,
        extractions=ex, weights={k: 1.0 for k in posts},
        handles={f"a{i}": f"a{i}" for i in range(4)},
        embed_fn=lambda t: np.ones((len(t), 8)) / 8 ** 0.5, threshold=0.5,
    )
    assert len(clusters) == 2
    assert {c.stance for c in clusters} == {"hike", "hold"}


def test_cluster_shares_sum_to_one_and_counts_are_distinct_authors():
    posts = {f"p{i}": make_post(f"p{i}", "same_author") for i in range(3)}
    ex = {k: make_extraction(k, stance="hike") for k in posts}
    for i, e in enumerate(ex.values()):
        e.key_claim = f"claim {i}"
    clusters = build_clusters(
        issue_id="i", cohort="crowd", post_ids=list(posts), posts=posts,
        extractions=ex, weights={k: 1.0 for k in posts},
        handles={"same_author": "loud"},
        embed_fn=lambda t: np.eye(len(t), 8), threshold=0.9,
    )
    assert sum(c.share for c in clusters) == pytest.approx(1.0)
    assert all(c.n_authors == 1 for c in clusters)
