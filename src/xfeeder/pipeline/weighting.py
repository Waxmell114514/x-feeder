"""How much voice each post gets.

This module is the political constitution of the system, and it is worth
being explicit about what it encodes.

The novel's premise is that everyone speaks and the machine understands all
of them. A naive implementation betrays that premise twice over: an LLM
asked to "summarise these 3,000 tweets" reports the most VIVID opinion, not
the most COMMON one; and raw engagement weighting hands the outcome to
whoever bought the most reach. So:

  * Weight is computed here, arithmetically, and handed to the model as
    fact. The model writes prose; it never decides how big a bloc is.
  * Inside the crowd tier, weighting is close to one-account-one-vote:
    `authority_weighting = 0` makes follower count irrelevant. A retail
    account with 40 followers counts as much as one with 40,000.
  * Inside the official and media tiers, authority is the whole point and
    is weighted accordingly.
  * Engagement enters through a log with a hard cap, so a viral post is
    worth a few ordinary posts - not a few thousand.
  * Identical text is damped as 1/sqrt(n): a hundred copies of the same
    line count as ten voices, not a hundred. This is the astroturf brake.
  * No single account may exceed `author_cap_pct` of its tier, enforced by
    water-filling. One person cannot be a majority of the public.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Optional

from ..models import Author, Extraction, Post

# How much a claim's provenance is worth when scoring confidence.
REASONING_QUALITY = {
    "data": 1.0,
    "official_guidance": 1.0,
    "market_pricing": 0.9,
    "analysis": 0.7,
    "opinion": 0.5,
    "noise": 0.1,
}


def credibility(author: Author, rule, weighting) -> float:
    """Account-level multiplier, tier-dependent."""
    authority = 1.0 + math.log10(1.0 + max(0, author.followers)) / 6.0
    aw = max(0.0, min(1.0, rule.authority_weighting))
    score = (1.0 - aw) * 1.0 + aw * authority

    age = author.account_age_days
    if age is not None and age < weighting.min_account_age_days:
        score *= weighting.new_account_penalty
    if author.followers < weighting.low_follower_floor and not author.verified:
        score *= weighting.bot_penalty
    return score


def reach(post: Post, engagement_weighting: float, cap: float) -> float:
    """Log-scaled, capped amplification."""
    m = post.metrics
    raw = m.like + 2 * m.retweet + 3 * m.quote
    boost = math.log10(1.0 + max(0, raw)) / 2.0
    return min(1.0 + engagement_weighting * boost, cap)


def recency(post: Post, now: dt.datetime, half_life_hours: float) -> float:
    created = post.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    age_h = max(0.0, (now - created).total_seconds() / 3600.0)
    return 0.5 ** (age_h / max(0.5, half_life_hours))


def assertion_quality(ex: Extraction) -> float:
    q = 0.35 + 0.65 * max(0.0, min(1.0, ex.intensity))
    if ex.is_question:
        q *= 0.5
    if ex.is_sarcastic:
        q *= 0.6
    return q


def post_weight(
    *, post: Post, author: Author, extraction: Extraction, rule, weighting,
    now: dt.datetime, half_life_hours: float, dup_size: int = 1,
    engagement_weighting: float = 1.0,
) -> float:
    w = credibility(author, rule, weighting)
    w *= reach(post, engagement_weighting, weighting.engagement_cap)
    w *= recency(post, now, half_life_hours)
    w *= assertion_quality(extraction)
    if weighting.duplicate_damping and dup_size > 1:
        w /= math.sqrt(dup_size)
    return max(0.0, w)


def apply_author_cap(weights: dict[str, float], author_of: dict[str, str],
                     cap_pct: float, iterations: int = 20) -> dict[str, float]:
    """Water-filling: no account may hold more than `cap_pct` of the tier.

    Excess above the cap is shaved and the remaining accounts absorb it
    proportionally, repeated until the constraint holds (or we run out of
    iterations, which happens only when one account is nearly everything).
    """
    if not weights or cap_pct <= 0 or cap_pct >= 1:
        return dict(weights)

    out = dict(weights)
    for _ in range(iterations):
        by_author: dict[str, float] = defaultdict(float)
        for pid, w in out.items():
            by_author[author_of[pid]] += w
        total = sum(by_author.values())
        if total <= 0:
            return out
        cap = total * cap_pct
        over = {a: w for a, w in by_author.items() if w > cap * 1.0001}
        if not over:
            return out
        for pid, w in list(out.items()):
            a = author_of[pid]
            if a in over and by_author[a] > 0:
                out[pid] = w * (cap / by_author[a])
    return out


# ----------------------------------------------------------------------
def stance_shares(weights: dict[str, float],
                  extractions: dict[str, Extraction]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for pid, w in weights.items():
        totals[extractions[pid].stance] += w
    grand = sum(totals.values())
    if grand <= 0:
        return {}
    return {k: v / grand for k, v in sorted(totals.items(), key=lambda kv: -kv[1])}


def implied_probability(
    weights: dict[str, float], extractions: dict[str, Extraction],
    anchors: dict[str, float],
) -> tuple[Optional[float], Optional[float], Optional[float], float]:
    """Return (blended, explicit, from_stance, explicit_coverage).

    Two independent estimates, combined by how much of the voice actually
    stated a number. When a lot of people quote a figure we believe the
    figures; when nobody does we fall back on where their side sits on the
    axis. Mixing them any other way would double-count the same belief.
    """
    total = sum(weights.values())
    if total <= 0:
        return None, None, None, 0.0

    num = den = 0.0
    for pid, w in weights.items():
        p = extractions[pid].probability
        if p is not None:
            num += w * p
            den += w
    explicit = (num / den) if den > 0 else None
    coverage = den / total

    shares = stance_shares(weights, extractions)
    anchored = {s: v for s, v in shares.items() if s in anchors}
    denom = sum(anchored.values())
    from_stance = (
        sum(anchors[s] * v for s, v in anchored.items()) / denom if denom > 0 else None
    )

    if explicit is None:
        blended = from_stance
    elif from_stance is None:
        blended = explicit
    else:
        blended = coverage * explicit + (1.0 - coverage) * from_stance
    return blended, explicit, from_stance, coverage


def agreement(shares: dict[str, float]) -> float:
    """1.0 when a tier speaks with one voice, 0.0 when uniformly split."""
    vals = [v for v in shares.values() if v > 0]
    if len(vals) <= 1:
        return 1.0
    entropy = -sum(v * math.log(v) for v in vals)
    return max(0.0, 1.0 - entropy / math.log(len(vals)))


def confidence(*, n_authors: int, agree: float, weights: dict[str, float],
               extractions: dict[str, Extraction]) -> float:
    sample = min(1.0, n_authors / 25.0)
    total = sum(weights.values())
    if total > 0:
        quality = sum(
            w * REASONING_QUALITY.get(extractions[pid].reasoning_kind, 0.5)
            for pid, w in weights.items()
        ) / total
    else:
        quality = 0.0
    return round(0.40 * sample + 0.35 * agree + 0.25 * quality, 3)
