"""How much voice each document gets.

This module is the political constitution of the system, and it is worth
being explicit about what it encodes.

The premise is that everyone speaks and the machine understands all of
them. A naive implementation betrays that premise twice over: a language
model asked to "summarise these 3,000 posts" reports the most VIVID
opinion, not the most COMMON one; and raw engagement weighting hands the
outcome to whoever bought the most reach. So:

  * Weight is computed here, arithmetically. Jev never sees a share, a
    count or a total, and could not produce one if it did - it answers
    typed questions about one document at a time.
  * Authority is a property of the VENUE, not of a personal audience.
    Public sources do not expose follower counts, and rather than invent a
    proxy for one, this system says the honest thing: inside a tier, every
    venue counts the same unless the config says otherwise.
  * Engagement enters through a log with a hard cap, so a post with 40,000
    upvotes is worth a few ordinary posts - not a few thousand.
  * Identical text is damped as 1/sqrt(n). On public sources this matters
    more than it did on X, because syndication is the normal case: one wire
    story appears verbatim under thirty mastheads and is one voice, not
    thirty.
  * No single account may exceed `speaker_cap_pct` of its tier, and no
    single venue more than `channel_cap_pct`, both enforced by water-
    filling. One person cannot be a majority of the public, and neither can
    one subreddit.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Callable, Optional

from ..config import normalise_channel
from ..models import Document, Reading

# How much a claim's provenance is worth when scoring a tier's confidence.
# This is about how much the reading can be trusted, not about whether the
# speaker is right.
PROVENANCE_QUALITY = {
    "reported_data": 1.0,
    "official_guidance": 1.0,
    "market_pricing": 0.9,
    "own_view": 0.6,
    "unclear": 0.3,
}


def venue_authority(doc: Document, rule) -> float:
    """Per-venue multiplier inside a tier. Default 1.0: one venue, one weight."""
    table = getattr(rule, "channel_authority", None) or {}
    if not table:
        return 1.0
    key = normalise_channel(doc.channel)
    for listed, value in table.items():
        listed = normalise_channel(listed)
        if key == listed or ("." in listed and key.endswith("." + listed)):
            return max(0.0, float(value))
    return 1.0


def reach(doc: Document, engagement_weighting: float, cap: float) -> float:
    """Log-scaled, capped amplification.

    Comments count double an upvote: on a forum, replying is a stronger
    signal that a post moved someone than voting on it.
    """
    e = doc.engagement
    raw = e.score + 2 * e.comments + 3 * e.reposts
    boost = math.log10(1.0 + max(0, raw)) / 2.0
    return min(1.0 + engagement_weighting * boost, cap)


def recency(doc: Document, now: dt.datetime, half_life_hours: float) -> float:
    created = doc.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    age_h = max(0.0, (now - created).total_seconds() / 3600.0)
    return 0.5 ** (age_h / max(0.5, half_life_hours))


def assertion_quality(reading: Reading) -> float:
    """How much of a position this document actually is.

    Sarcasm discounts rather than inverts. Jev reports P(the text means the
    opposite of what it says); flipping a document's side on a 0.7 is how
    you get a confident report built on coin flips, so an ironic post
    counts for less and stays where it is.
    """
    q = 0.35 + 0.65 * max(0.0, min(1.0, reading.intensity))
    if reading.is_question:
        q *= 0.5
    if reading.sarcastic:
        q *= 0.6
    # A stance the model itself could barely separate is worth less than one
    # it was sure of. This is the one place a confidence enters the weights.
    q *= 0.5 + 0.5 * max(0.0, min(1.0, reading.stance_confidence))
    return q


def substance(doc: Document, weighting) -> float:
    """A headline is not an argument.

    Google News gives titles, not bodies. Those documents are worth having -
    they are what the press is saying - but a 12-word headline should not
    outweigh a reasoned comment, so short documents are discounted.
    """
    if len(doc.body) >= weighting.thin_document_chars:
        return 1.0
    return weighting.thin_document_penalty


def document_weight(
    *, doc: Document, reading: Reading, rule, weighting, now: dt.datetime,
    half_life_hours: float, dup_size: int = 1,
) -> float:
    """The whole weight of one document. The tier's rule is the only place
    engagement weighting comes from, so a caller cannot pass one that
    disagrees with the tier it is weighing."""
    w = venue_authority(doc, rule)
    w *= reach(doc, rule.engagement_weighting, weighting.engagement_cap)
    w *= recency(doc, now, half_life_hours)
    w *= assertion_quality(reading)
    w *= substance(doc, weighting)
    if weighting.duplicate_damping and dup_size > 1:
        w /= math.sqrt(dup_size)
    return max(0.0, w)


def apply_cap(weights: dict[str, float], key_of: Callable[[str], str],
              cap_pct: float, iterations: int = 50) -> dict[str, float]:
    """Water-filling: no key may hold more than `cap_pct` of the total.

    Weight above the cap is taken from the holder and absorbed by everyone
    else in proportion, so the total is unchanged and only the *shape* of
    the distribution moves. Used twice - once per speaker, once per venue.

    Shaving without redistributing would look similar and behave worse: the
    total falls with every pass, so the cap falls too, and a tier with few
    holders decays towards zero instead of converging. When the cap is
    arithmetically unreachable - four speakers cannot each hold 5% - the
    floor of 1/holders turns it into an equal split, which is the most the
    constraint can honestly ask for.
    """
    if not weights or cap_pct <= 0 or cap_pct >= 1:
        return dict(weights)

    out = dict(weights)
    keys = {doc_id: key_of(doc_id) for doc_id in out}
    holders = len(set(keys.values()))
    if holders <= 1:
        return out
    cap_pct = max(cap_pct, 1.0 / holders)

    total = sum(out.values())
    if total <= 0:
        return out
    cap = total * cap_pct

    for _ in range(iterations):
        totals: dict[str, float] = defaultdict(float)
        for doc_id, w in out.items():
            totals[keys[doc_id]] += w

        over = {k: v for k, v in totals.items() if v > cap * 1.0001}
        if not over:
            return out

        excess = sum(v - cap for v in over.values())
        under = sum(v for k, v in totals.items() if k not in over)
        if under <= 0:
            return out

        lift = (under + excess) / under
        for doc_id, w in out.items():
            key = keys[doc_id]
            if key in over:
                out[doc_id] = w * (cap / totals[key])
            else:
                out[doc_id] = w * lift
    return out


# ----------------------------------------------------------------------
def stance_shares(weights: dict[str, float],
                  readings: dict[str, Reading]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for doc_id, w in weights.items():
        totals[readings[doc_id].stance] += w
    grand = sum(totals.values())
    if grand <= 0:
        return {}
    return {k: v / grand for k, v in sorted(totals.items(), key=lambda kv: -kv[1])}


def implied_probability(
    weights: dict[str, float], readings: dict[str, Reading],
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
    for doc_id, w in weights.items():
        p = readings[doc_id].stated_probability
        if p is not None:
            num += w * p
            den += w
    explicit = (num / den) if den > 0 else None
    coverage = den / total

    shares = stance_shares(weights, readings)
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


def agreement(shares: dict[str, float],
              anchors: Optional[dict[str, float]] = None) -> float:
    """1.0 when the tier speaks with one voice, 0.0 when uniformly split.

    Measured over the sides only. "No side" is not a competing position: a
    tier where everyone who spoke said the same thing and the rest said
    nothing is unanimous, and reporting it as 12% agreement because most of
    the room stayed quiet describes the wrong thing. How much of the room
    stayed quiet is `undecided_share`, which is a separate number on
    purpose.
    """
    if anchors is not None:
        shares = {k: v for k, v in shares.items() if k in anchors}
        total = sum(shares.values())
        if total <= 0:
            return 0.0
        shares = {k: v / total for k, v in shares.items()}
    vals = [v for v in shares.values() if v > 0]
    if not vals:
        return 0.0
    if len(vals) == 1:
        return 1.0
    entropy = -sum(v * math.log(v) for v in vals)
    return max(0.0, 1.0 - entropy / math.log(len(vals)))


def confidence(*, n_speakers: int, n_channels: int, agree: float,
               weights: dict[str, float], readings: dict[str, Reading]) -> float:
    """How much to trust a tier's reading.

    Sample size counts speakers and venues separately on purpose: forty
    posts from one subreddit is not the same evidence as forty posts from
    eight, however many accounts were involved.
    """
    sample = min(1.0, n_speakers / 25.0)
    spread = min(1.0, n_channels / 5.0)
    total = sum(weights.values())
    if total > 0:
        quality = sum(
            w * PROVENANCE_QUALITY.get(readings[doc_id].speaks_for, 0.5)
            for doc_id, w in weights.items()
        ) / total
    else:
        quality = 0.0
    return round(0.30 * sample + 0.15 * spread + 0.35 * agree + 0.20 * quality, 3)
