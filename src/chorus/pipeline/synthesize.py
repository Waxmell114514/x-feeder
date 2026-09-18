"""Stage 7 - weigh everything and assemble the snapshot.

For each tier: weight every document, cap the loudest account and the
loudest venue, total up the sides, and turn each opinion leader into a
*delegate* - the leader as measured inside that tier, with a share, a
speaker count, verbatim reasons and quotable sources.

Then, across tiers, compute where they disagree. For a monitor the
disagreement between officialdom and the public is the signal, and
averaging the tiers together destroys exactly the thing worth watching. The
blended number exists because people ask for one; the divergence table
next to it is what the system is actually for.

Nothing in this file calls a model. Everything here is arithmetic over
judgements already made, and prose already written in `phrasing.py`.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from typing import Optional

from .. import TIERS, phrasing, textutil, tier_label
from ..models import (
    Delegate, Document, Divergence, Leader, Panel, PanelAssignment, Quote,
    Reading, Snapshot, TierVerdict,
)
from ..store import Store
from . import weighting as W

MAX_QUOTES = 4
MAX_REASONS = 3


def run_synthesize(cfg, store: Store, issue, documents: list[Document],
                   readings: dict[str, Reading], panel: Panel,
                   assignments: dict[str, PanelAssignment], log=print) -> Snapshot:
    lang = issue.output_lang or cfg.output_lang
    now = dt.datetime.now(dt.timezone.utc)

    tiers_of = store.get_channel_tiers()
    dup_sizes = store.duplicate_group_sizes()
    doc_map = {d.id: d for d in documents}

    usable = [d for d in documents
              if d.id in readings and readings[d.id].relevant]
    by_tier: dict[str, list[str]] = defaultdict(list)
    for doc in usable:
        assigned = tiers_of.get(doc.channel)
        by_tier[assigned.tier if assigned else "crowd"].append(doc.id)

    log(f"  {len(usable)}/{len(documents)} documents bear on the question")

    verdicts: dict[str, TierVerdict] = {}
    # Always report tiers in authority order, not in whatever order the
    # documents happened to arrive - a report whose rows move between runs
    # is unreadable.
    for tier in [t for t in TIERS if t in by_tier]:
        verdict = _tier_verdict(
            cfg=cfg, issue=issue, tier=tier, doc_ids=by_tier[tier],
            doc_map=doc_map, readings=readings, assignments=assignments,
            panel=panel, dup_sizes=dup_sizes, now=now, lang=lang, log=log,
        )
        if verdict is not None:
            verdicts[tier] = verdict

    blended = _blend(cfg, verdicts)
    divergences = _divergences(cfg, verdicts, lang)
    sources = Counter(doc_map[d].source for ids in by_tier.values() for d in ids)

    snap = Snapshot(
        issue_id=issue.id, ts=now, window_hours=issue.window_hours,
        tiers=verdicts, panel=list(panel.leaders),
        blended_probability=blended, divergences=divergences,
        n_docs=len(usable),
        n_speakers=len({doc_map[d].speaker for ids in by_tier.values() for d in ids}),
        n_channels=len({doc_map[d].channel for ids in by_tier.values() for d in ids}),
        sources=dict(sources),
    )
    snap.global_headline = _headline(issue, snap, divergences, lang)
    snap.notes = _notes(cfg, issue, snap, verdicts, lang)
    return snap


# ======================================================================
def _tier_verdict(*, cfg, issue, tier, doc_ids, doc_map, readings, assignments,
                  panel, dup_sizes, now, lang, log) -> Optional[TierVerdict]:
    rule = cfg.tiers.get(tier)
    if rule is None or not doc_ids:
        return None

    raw: dict[str, float] = {}
    for doc_id in doc_ids:
        doc = doc_map[doc_id]
        raw[doc_id] = W.document_weight(
            doc=doc, reading=readings[doc_id], rule=rule, weighting=cfg.weighting,
            now=now, half_life_hours=issue.half_life_hours,
            dup_size=dup_sizes.get(textutil.text_hash(doc.body), 1),
        )

    weights = W.apply_cap(raw, lambda d: doc_map[d].speaker,
                          cfg.weighting.speaker_cap_pct)
    weights = W.apply_cap(weights, lambda d: doc_map[d].channel,
                          cfg.weighting.channel_cap_pct)
    total = sum(weights.values())
    if total <= 0:
        return None

    shares = W.stance_shares(weights, readings)
    blended, explicit, from_stance, coverage = W.implied_probability(
        weights, readings, issue.anchors())
    decided = sum(v for s, v in shares.items() if s in issue.anchors())
    if decided < cfg.thresholds.min_decided_share:
        # The arithmetic is sound and the number is still meaningless: it
        # describes the sliver of the tier that committed, while the report
        # would show it as the tier's reading. Keep both components on the
        # record and publish no headline number.
        blended = None
    agree = W.agreement(shares, issue.anchors())
    speakers = {doc_map[d].speaker for d in doc_ids}
    channels = {doc_map[d].channel for d in doc_ids}
    confidence = W.confidence(n_speakers=len(speakers), n_channels=len(channels),
                              agree=agree, weights=weights, readings=readings)

    delegates, unassigned, undecided = _delegates(
        cfg=cfg, issue=issue, tier=tier, doc_ids=doc_ids, doc_map=doc_map,
        readings=readings, weights=weights, assignments=assignments,
        panel=panel, total=total, lang=lang,
    )

    dominant = _dominant(shares, issue)
    dominant_share = shares.get(dominant, 0.0)
    split = None
    if dominant_share < 0.55 and len(shares) > 1:
        split = [(issue.stance_label(s, lang), v) for s, v in shares.items()]

    verdict = TierVerdict(
        issue_id=issue.id, tier=tier, stance_shares=shares,
        dominant_stance=dominant, probability=blended,
        probability_explicit=explicit, probability_from_stance=from_stance,
        explicit_coverage=coverage, agreement=agree, confidence=confidence,
        n_docs=len(doc_ids), n_speakers=len(speakers), n_channels=len(channels),
        weight=total, unassigned_share=unassigned, undecided_share=undecided,
        delegates=delegates,
    )
    verdict.headline = phrasing.tier_headline(
        tier_label=tier_label(tier, lang),
        stance_label=issue.stance_label(dominant, lang),
        share=dominant_share, n_speakers=len(speakers), n_channels=len(channels),
        probability=blended, split=split, lang=lang,
    )
    log(f"  [{tier:9}] {len(doc_ids):4} docs / {len(speakers):3} speakers"
        f" -> {len(delegates)} delegate(s)"
        + (f", p={blended:.0%}" if blended is not None else ""))
    return verdict


# ----------------------------------------------------------------------
def _dominant(shares: dict[str, float], issue) -> str:
    """The leading *side*, which is never "no side".

    A tier can be mostly undecided - that is what `unclear`'s share is for -
    but reporting "the leading position is: no position" tells a reader
    nothing, and it would flip on and off between runs and fire a stance
    flip every time it did.
    """
    anchored = {s: v for s, v in shares.items() if s in issue.anchors()}
    if anchored:
        return max(anchored, key=lambda k: anchored[k])
    return max(shares, key=lambda k: shares[k]) if shares else "unclear"


def _delegates(*, cfg, issue, tier, doc_ids, doc_map, readings, weights,
               assignments, panel, total, lang
               ) -> tuple[list[Delegate], float, float]:
    anchors = issue.anchors()
    by_leader: dict[str, list[str]] = defaultdict(list)
    unassigned_weight = 0.0
    undecided_weight = 0.0
    for doc_id in doc_ids:
        weight = weights.get(doc_id, 0.0)
        reading = readings.get(doc_id)
        if reading is None or reading.stance not in anchors:
            # Took no side. No panel could have represented it, so it is not
            # counted against the panel.
            undecided_weight += weight
            continue
        assignment = assignments.get(doc_id)
        if assignment is None or not assignment.leader_id:
            unassigned_weight += weight
            continue
        by_leader[assignment.leader_id].append(doc_id)

    leaders = panel.by_id()
    out: list[Delegate] = []
    for leader_id, members in by_leader.items():
        leader = leaders.get(leader_id)
        if leader is None:
            continue
        members.sort(key=lambda d: -weights.get(d, 0.0))
        weight = sum(weights.get(d, 0.0) for d in members)
        share = weight / total if total else 0.0
        if share < cfg.thresholds.min_leader_share:
            unassigned_weight += weight
            continue
        out.append(_delegate(issue, tier, leader, members, doc_map, readings,
                             weights, assignments, share, lang))

    out.sort(key=lambda d: -d.weight)
    dropped = out[cfg.thresholds.max_delegates_per_tier:]
    unassigned_weight += sum(d.weight for d in dropped)
    out = out[: cfg.thresholds.max_delegates_per_tier]
    if not total:
        return out, 0.0, 0.0
    return out, unassigned_weight / total, undecided_weight / total


def _delegate(issue, tier, leader: Leader, members: list[str], doc_map, readings,
              weights, assignments, share: float, lang: str) -> Delegate:
    speakers = {doc_map[d].speaker for d in members}
    scored = [(doc_map[d], weights.get(d, 0.0)) for d in members]
    confidences = [assignments[d].confidence for d in members if d in assignments]

    return Delegate(
        id=f"{tier}:{leader.id}",
        issue_id=issue.id,
        tier=tier,
        leader_id=leader.id,
        name=leader.name(lang),
        verdict=phrasing.delegate_verdict(issue.stance_label(leader.stance, lang), lang),
        stance=leader.stance,
        rationale=_reasons(leader, [doc_map[d] for d in members]),
        caveat=leader.flip_on,
        probability=_mean_probability(members, readings, weights),
        share=share,
        weight=sum(weights.get(d, 0.0) for d in members),
        n_docs=len(members),
        n_speakers=len(speakers),
        assign_confidence=round(sum(confidences) / len(confidences), 3)
                          if confidences else 0.0,
        quotes=_quotes(scored),
    )


def _reasons(leader: Leader, docs: list[Document]) -> list[str]:
    """What this bloc argues, in its own words.

    Phrases are mined from the documents assigned to the leader and ranked
    by how many of them use each phrase. They are verbatim - this is the
    replacement for the sentence a language model used to write, and the
    trade is fluency for the ability to check it.
    """
    texts = textutil.mining_corpus(docs)
    mined = textutil.mine_phrases(
        texts, min_docs=max(2, len(texts) // 4) if len(texts) > 4 else 1,
        max_words=5, min_words=2, limit=MAX_REASONS * 6,
    )

    # Overlapping n-grams of one sentence are one reason, not three. Take a
    # phrase only when it says something the ones already taken do not.
    out: list[str] = []
    taken: list[set[str]] = []
    for phrase, _ in mined:
        words = set(phrase.split())
        if any(len(words & seen) / max(1, min(len(words), len(seen))) > 0.5
               for seen in taken):
            continue
        taken.append(words)
        out.append(phrase)
        if len(out) >= MAX_REASONS:
            break

    if not out and leader.origin == "mined":
        out = [leader.label]
    return out


def _quotes(scored: list[tuple[Document, float]]) -> list[Quote]:
    """Heaviest first, one per distinct text and one per speaker.

    Without the dedup a syndicated wire story fills every slot and the
    reader is shown the same paragraph four times as the bloc's voices.
    """
    seen_text: set[str] = set()
    seen_speaker: set[str] = set()
    out: list[Quote] = []
    for doc, weight in scored:
        key = textutil.text_hash(doc.body)
        if key in seen_text or doc.speaker in seen_speaker:
            continue
        seen_text.add(key)
        seen_speaker.add(doc.speaker)
        out.append(Quote(
            doc_id=doc.id, speaker=doc.author or doc.channel, channel=doc.channel,
            text=textutil.truncate(textutil.clean(doc.body), 260),
            url=doc.url, weight=round(weight, 4),
        ))
        if len(out) >= MAX_QUOTES:
            break
    return out


def _mean_probability(members: list[str], readings: dict[str, Reading],
                      weights: dict[str, float]) -> Optional[float]:
    """The number this bloc puts on the outcome, when its members give one.

    Weighted by the same weights as everything else, and None - not zero,
    and not the stance anchor - when nobody in the bloc stated a figure.
    """
    num = den = 0.0
    for doc_id in members:
        reading = readings.get(doc_id)
        if reading is None or reading.stated_probability is None:
            continue
        w = weights.get(doc_id, 0.0)
        num += w * reading.stated_probability
        den += w
    return round(num / den, 4) if den > 0 else None


# ======================================================================
def _blend(cfg, verdicts: dict[str, TierVerdict]) -> Optional[float]:
    """Weighted blend across tiers, renormalised over tiers that have data."""
    weights = cfg.blend_weights()
    num = den = 0.0
    for tier, v in verdicts.items():
        if v.probability is None:
            continue
        w = weights.get(tier, 0.0)
        num += w * v.probability
        den += w
    return num / den if den > 0 else None


def _divergences(cfg, verdicts: dict[str, TierVerdict], lang: str) -> list[Divergence]:
    have = [(t, v) for t, v in verdicts.items() if v.probability is not None]
    out: list[Divergence] = []
    for i, (t1, v1) in enumerate(have):
        for t2, v2 in have[i + 1:]:
            delta = abs(v1.probability - v2.probability)
            if delta < cfg.thresholds.divergence_alert:
                continue
            higher, lower = ((t1, v1), (t2, v2)) if v1.probability > v2.probability \
                else ((t2, v2), (t1, v1))
            out.append(Divergence(
                pair=(higher[0], lower[0]), delta=delta,
                note=phrasing.divergence_note(
                    higher_label=tier_label(higher[0], lang),
                    lower_label=tier_label(lower[0], lang),
                    delta=delta, higher_p=higher[1].probability,
                    lower_p=lower[1].probability, lang=lang),
            ))
    out.sort(key=lambda d: -d.delta)
    return out


def _headline(issue, snap: Snapshot, divergences, lang: str) -> str:
    dominant = "unclear"
    if snap.tiers:
        counts: Counter = Counter()
        for v in snap.tiers.values():
            counts[v.dominant_stance] += v.weight
        dominant = counts.most_common(1)[0][0]
    
    read_tiers = [t for t, v in snap.tiers.items() if v.probability is not None]
    return phrasing.global_headline(
        blended=snap.blended_probability,
        top_divergence=divergences[0].note if divergences else None,
        dominant_label=issue.stance_label(dominant, lang),
        n_docs=snap.n_docs, n_readings=len(read_tiers),
        only_tier=tier_label(read_tiers[0], lang) if len(read_tiers) == 1 else "",
        lang=lang,
    )


def _notes(cfg, issue, snap: Snapshot, verdicts, lang: str) -> list[str]:
    notes = [phrasing.coverage_note(
        n_docs=snap.n_docs, n_speakers=snap.n_speakers,
        n_channels=snap.n_channels, sources=snap.sources, lang=lang)]

    official = verdicts.get("official")
    crowd = verdicts.get("crowd")
    if official and crowd and official.probability is not None \
            and crowd.probability is not None:
        gap = crowd.probability - official.probability
        if abs(gap) >= cfg.thresholds.divergence_alert:
            notes.append(phrasing.official_gap(
                gap=gap, crowd_p=crowd.probability,
                official_p=official.probability, lang=lang))

    for tier, v in verdicts.items():
        decided = sum(s for k, s in v.stance_shares.items() if k in issue.anchors())
        if v.probability is None and decided < cfg.thresholds.min_decided_share \
                and v.n_docs >= 4:
            notes.append(phrasing.no_reading_note(
                tier_label=tier_label(tier, lang), decided=decided, lang=lang))
        if v.n_docs < 4:
            notes.append(phrasing.thin_tier_note(
                tier_label=tier_label(tier, lang), n_docs=v.n_docs, lang=lang))
        elif v.unassigned_share >= 0.35:
            separator = "：" if lang == "zh" else ": "
            notes.append(tier_label(tier, lang) + separator
                         + phrasing.unassigned_note(v.unassigned_share, lang))
        elif v.undecided_share >= 0.5:
            separator = "：" if lang == "zh" else ": "
            notes.append(tier_label(tier, lang) + separator
                         + phrasing.undecided_note(v.undecided_share, lang))
    return notes
