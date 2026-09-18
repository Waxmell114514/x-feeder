"""Grouping vectors that mean the same thing.

The X-era pipeline clustered documents, because the blocs were discovered
from the data and named by a language model. Jev writes nothing, so blocs
are declared or mined up front and documents are *routed* to them - which
leaves clustering one, smaller job: merging candidate phrases that say the
same thing, so a six-seat panel does not spend three seats on one argument.

The merge is average-linkage on cosine similarity with a threshold rather
than a fixed k: how many distinct arguments are live on a question is not
known in advance, and forcing k either splits one argument or fuses two.
"""
from __future__ import annotations

import numpy as np

LARGE_N = 400


def agglomerate(vectors: np.ndarray, threshold: float) -> list[list[int]]:
    """Average-linkage agglomerative clustering on cosine similarity.

    Exact for the sizes that occur here (a few dozen candidate phrases);
    above `LARGE_N` it degrades to single-pass leader clustering, which is
    O(n*k) and good enough when a bucket is that crowded.
    """
    n = len(vectors)
    if n <= 1:
        return [[i] for i in range(n)]
    if n > LARGE_N:
        return leader_clusters(vectors, threshold)

    sim = vectors @ vectors.T
    clusters: dict[int, list[int]] = {i: [i] for i in range(n)}

    def key(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a < b else (b, a)

    sums: dict[tuple[int, int], float] = {
        (i, j): float(sim[i, j]) for i in range(n) for j in range(i + 1, n)
    }

    while len(clusters) > 1 and sums:
        best_pair, best_avg = None, -2.0
        for (i, j), total in sums.items():
            avg = total / (len(clusters[i]) * len(clusters[j]))
            if avg > best_avg:
                best_avg, best_pair = avg, (i, j)
        if best_pair is None or best_avg < threshold:
            break

        i, j = best_pair
        for k in clusters:
            if k in (i, j):
                continue
            sums[key(i, k)] = sums.get(key(i, k), 0.0) + sums.pop(key(j, k), 0.0)
        sums.pop(key(i, j), None)
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]

    return [sorted(v) for v in clusters.values()]


def leader_clusters(vectors: np.ndarray, threshold: float) -> list[list[int]]:
    """Single-pass leader clustering: assign to the nearest centroid over
    threshold, else start a new cluster. Order-dependent but linear."""
    centroids: list[np.ndarray] = []
    groups: list[list[int]] = []
    for idx, vec in enumerate(vectors):
        best, best_sim = -1, threshold
        for ci, c in enumerate(centroids):
            s = float(vec @ c)
            if s >= best_sim:
                best, best_sim = ci, s
        if best < 0:
            centroids.append(vec.copy())
            groups.append([idx])
        else:
            groups[best].append(idx)
            c = centroids[best] * (len(groups[best]) - 1) + vec
            norm = np.linalg.norm(c) or 1.0
            centroids[best] = c / norm
    return groups
