"""Merging candidate phrases that mean the same thing."""
import numpy as np

from chorus.jev.embeddings import embed
from chorus.pipeline.cluster import agglomerate, leader_clusters


def unit(vectors):
    m = np.asarray(vectors, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


SEPARATED = unit([[1, 0], [0.99, 0.1], [0, 1], [0.1, 0.99]])


def test_agglomerative_recovers_separated_groups():
    groups = agglomerate(SEPARATED, 0.9)
    assert sorted(sorted(g) for g in groups) == [[0, 1], [2, 3]]


def test_the_linear_fallback_recovers_the_same_groups():
    groups = leader_clusters(SEPARATED, 0.9)
    assert sorted(sorted(g) for g in groups) == [[0, 1], [2, 3]]


def test_degenerate_inputs():
    assert agglomerate(unit([[1, 0]]), 0.5) == [[0]]
    assert agglomerate(np.zeros((0, 2), dtype=np.float32), 0.5) == []


def test_a_high_threshold_never_merges():
    groups = agglomerate(SEPARATED, 0.999)
    assert len(groups) == 4


def test_paraphrases_of_one_position_land_together(cfg):
    phrases = [
        "core services inflation has not come down",
        "services inflation has not come down at all",
        "the labour market is cooling",
    ]
    vectors = np.asarray(embed(phrases, cfg))
    groups = agglomerate(vectors, 0.45)
    merged = {tuple(sorted(g)) for g in groups}
    assert (0, 1) in merged
    assert (2,) in merged
