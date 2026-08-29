"""Group posts that are making the same argument.

Clustering happens on the extracted `key_claim`, not on the raw post. Raw
posts cluster by style, language and meme - "同意" and "agreed" land far
apart, while two unrelated rants full of rocket emoji land together. The
canonicalised claim sentence is the thing we actually want to count.

Partitioning is two-level:
  1. hard split by stance (a bull and a bear are never the same bloc, no
     matter how similar their prose), then
  2. agglomerative merge inside each stance on cosine similarity.

The merge is average-linkage with a threshold rather than a fixed k: the
number of live arguments about a question is not known in advance, and
forcing k would either split one argument or fuse two.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Optional

import numpy as np

from .. import textutil
from ..models import Extraction, OpinionCluster, Post, Quote


LARGE_N = 400


def _agglomerate(vectors: np.ndarray, threshold: float) -> list[list[int]]:
    """Average-linkage agglomerative clustering on cosine similarity.

    Exact for the sizes that occur in practice (one stance inside one tier);
    above `LARGE_N` it degrades to single-pass leader clustering, which is
    O(n*k) and good enough when a bucket is that crowded.
    """
    n = len(vectors)
    if n <= 1:
        return [[i] for i in range(n)]
    if n > LARGE_N:
        return _leader(vectors, threshold)

    sim = vectors @ vectors.T
    clusters: dict[int, list[int]] = {i: [i] for i in range(n)}

    def key(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a < b else (b, a)

    # sums of pairwise similarity between live clusters
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


def _leader(vectors: np.ndarray, threshold: float) -> list[list[int]]:
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


def _pick_quotes(members: list[str], posts: dict[str, Post], handles: dict[str, str],
                 weights: dict[str, float], limit: int = 5) -> list[Quote]:
    """Heaviest posts first, one per distinct text and one per account.

    Without the dedup a copy-pasted line fills every quote slot and the
    reader is shown five copies of a spam post as the bloc's representative
    voices. Members arrive sorted by weight, so the survivor of a duplicate
    group is its heaviest instance.
    """
    seen_text: set[str] = set()
    seen_author: set[str] = set()
    picked: list[Quote] = []
    for pid in members:
        post = posts[pid]
        key = textutil.text_hash(post.text)
        if key in seen_text or post.author_id in seen_author:
            continue
        seen_text.add(key)
        seen_author.add(post.author_id)
        picked.append(Quote(
            post_id=pid, handle=handles.get(post.author_id, "?"),
            text=post.text, url=post.url, weight=weights.get(pid, 0.0),
        ))
        if len(picked) >= limit:
            break
    return picked


def build_clusters(
    *,
    issue_id: str,
    cohort: str,
    post_ids: list[str],
    posts: dict[str, Post],
    extractions: dict[str, Extraction],
    weights: dict[str, float],
    handles: dict[str, str],
    embed_fn,
    threshold: float,
) -> list[OpinionCluster]:
    by_stance: dict[str, list[str]] = defaultdict(list)
    for pid in post_ids:
        by_stance[extractions[pid].stance].append(pid)

    cohort_total = sum(weights.get(p, 0.0) for p in post_ids) or 1.0
    out: list[OpinionCluster] = []

    for stance, ids in by_stance.items():
        claims = [extractions[p].key_claim or posts[p].text for p in ids]
        vectors = embed_fn(claims)
        groups = _agglomerate(vectors, threshold)

        for gi, group in enumerate(groups):
            members = [ids[i] for i in group]
            members.sort(key=lambda p: -weights.get(p, 0.0))
            weight = sum(weights.get(p, 0.0) for p in members)
            authors = {posts[p].author_id for p in members}
            probs = [extractions[p].probability for p in members
                     if extractions[p].probability is not None]
            pw = [weights.get(p, 0.0) for p in members
                  if extractions[p].probability is not None]
            mean_p: Optional[float] = None
            if probs and sum(pw) > 0:
                mean_p = sum(x * w for x, w in zip(probs, pw)) / sum(pw)

            seen: set[str] = set()
            exemplars: list[str] = []
            for p in members:
                c = extractions[p].key_claim.strip()
                if c and c not in seen:
                    seen.add(c)
                    exemplars.append(c)

            cid = hashlib.sha1(
                f"{issue_id}|{cohort}|{stance}|{gi}|{members[0]}".encode()
            ).hexdigest()[:12]

            out.append(OpinionCluster(
                id=cid, issue_id=issue_id, cohort=cohort, stance=stance,
                post_ids=members, n_posts=len(members), n_authors=len(authors),
                weight=weight, share=weight / cohort_total,
                exemplar_claims=exemplars,
                mean_probability=mean_p,
                top_quotes=_pick_quotes(members, posts, handles, weights),
            ))

    out.sort(key=lambda c: -c.weight)
    return out
