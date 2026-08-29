"""Stages 4-6 - weight, cluster, and give the clusters a voice.

This is where the novel's idea actually lands. For each source tier we:

  1. weight every post (weighting.py),
  2. group posts by the argument they make (cluster.py),
  3. hand each group of any size to the model and get back a *delegate* -
     a synthetic opinion leader who speaks for that measured share,
  4. reduce the tier's delegates to one headline,

and then, across tiers, compute where they disagree - because for a monitor
the disagreement between officialdom and the crowd is the signal, and
averaging the tiers together would destroy exactly the thing worth watching.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Optional

from .. import COHORTS, COHORT_LABELS_ZH, textutil
from ..llm import LLMClient, LLMUnavailable
from ..llm import offline as offline_stub
from ..llm.embeddings import embed
from ..llm.prompts import (
    COHORT_HEADLINE_SYSTEM, DELEGATE_SYSTEM_TMPL, GLOBAL_SYSTEM, LANG_NAME,
    CohortHeadline, DelegateDraft, GlobalSynthesis, cohort_headline_prompt,
    delegate_user_prompt, global_prompt,
)
from ..models import (
    CohortVerdict, Delegate, Divergence, Extraction, OpinionCluster, Post,
    Quote, Snapshot,
)
from ..store import Store
from . import weighting as W
from .cluster import build_clusters


def cohort_label(cohort: str, lang: str) -> str:
    if lang == "zh":
        return COHORT_LABELS_ZH.get(cohort, cohort)
    return {
        "official": "Official", "pro_media": "Professional media",
        "en_kol": "English-language KOLs", "cn_kol": "Chinese-language KOLs",
        "crowd": "Retail crowd",
    }.get(cohort, cohort)


# ======================================================================
def run_synthesize(cfg, store: Store, issue_id: str, posts: list[Post],
                   llm: LLMClient | None = None, log=print) -> Snapshot:
    issue = cfg.issue(issue_id)
    lang = issue.output_lang or cfg.output_lang
    now = dt.datetime.now(dt.timezone.utc)

    extractions = store.get_extractions(issue_id)
    authors = store.get_authors({p.author_id for p in posts})
    cohorts = store.get_cohorts()
    dup_sizes = store.duplicate_group_sizes()

    post_map = {p.id: p for p in posts}
    handles = {a.id: a.handle for a in authors.values()}

    usable = [
        p for p in posts
        if p.id in extractions and extractions[p.id].relevant
        and p.author_id in authors
    ]
    by_cohort: dict[str, list[str]] = defaultdict(list)
    for p in usable:
        c = cohorts[p.author_id].cohort if p.author_id in cohorts else "crowd"
        by_cohort[c].append(p.id)

    log(f"  {len(usable)}/{len(posts)} posts are relevant and attributable")

    verdicts: dict[str, CohortVerdict] = {}
    # Always report tiers in authority order, not in whatever order posts
    # happened to arrive - a report whose rows move between runs is unreadable.
    for cohort in [c for c in COHORTS if c in by_cohort]:
        pids = by_cohort[cohort]
        v = _cohort_verdict(
            cfg=cfg, issue=issue, cohort=cohort, pids=pids, post_map=post_map,
            authors=authors, extractions=extractions, dup_sizes=dup_sizes,
            handles=handles, now=now, llm=llm, lang=lang, log=log,
        )
        if v is not None:
            verdicts[cohort] = v

    blended = _blend(cfg, verdicts)
    divergences = _divergences(cfg, verdicts, lang)
    previous = store.latest_snapshots(issue_id, limit=1)
    deltas = _describe_deltas(previous[0] if previous else None, verdicts, blended, lang)

    snap = Snapshot(
        issue_id=issue_id, ts=now, window_hours=issue.window_hours,
        cohorts=verdicts, blended_probability=blended,
        divergences=divergences,
        n_posts=len(usable),
        n_authors=len({p.author_id for p in usable}),
    )
    snap.global_headline, notes = _global(cfg, issue, snap, divergences, deltas,
                                          llm, lang, log)
    snap.notes = notes
    return snap


# ======================================================================
def _cohort_verdict(*, cfg, issue, cohort, pids, post_map, authors, extractions,
                    dup_sizes, handles, now, llm, lang, log) -> Optional[CohortVerdict]:
    rule = cfg.cohorts.get(cohort)
    if rule is None or not pids:
        return None

    raw_weights: dict[str, float] = {}
    for pid in pids:
        post = post_map[pid]
        author = authors[post.author_id]
        raw_weights[pid] = W.post_weight(
            post=post, author=author, extraction=extractions[pid], rule=rule,
            weighting=cfg.weighting, now=now, half_life_hours=issue.half_life_hours,
            dup_size=dup_sizes.get(textutil.text_hash(post.text), 1),
            engagement_weighting=rule.engagement_weighting,
        )

    author_of = {pid: post_map[pid].author_id for pid in pids}
    weights = W.apply_author_cap(raw_weights, author_of, cfg.weighting.author_cap_pct)
    total = sum(weights.values())
    if total <= 0:
        return None

    shares = W.stance_shares(weights, extractions)
    blended, explicit, from_stance, coverage = W.implied_probability(
        weights, extractions, issue.anchors()
    )
    agree = W.agreement(shares)
    n_authors = len(set(author_of.values()))
    conf = W.confidence(n_authors=n_authors, agree=agree, weights=weights,
                        extractions=extractions)

    clusters = build_clusters(
        issue_id=issue.id, cohort=cohort, post_ids=pids, posts=post_map,
        extractions=extractions, weights=weights, handles=handles,
        embed_fn=lambda texts: embed(texts, cfg),
        threshold=cfg.thresholds.cluster_similarity,
    )

    keep = [c for c in clusters if c.share >= cfg.thresholds.min_cluster_share]
    keep = keep[: cfg.thresholds.max_delegates_per_cohort]
    if not keep and clusters:
        keep = clusters[:1]        # always give a tier at least one voice

    delegates = [
        _make_delegate(cfg, issue, c, post_map, handles, llm, lang, log)
        for c in keep
    ]
    delegates = [d for d in delegates if d is not None]

    dominant = max(shares, key=lambda k: shares[k]) if shares else "unclear"
    verdict = CohortVerdict(
        issue_id=issue.id, cohort=cohort, stance_shares=shares,
        dominant_stance=dominant, probability=blended,
        probability_explicit=explicit, probability_from_stance=from_stance,
        explicit_coverage=coverage, agreement=agree, confidence=conf,
        n_posts=len(pids), n_authors=n_authors, weight=total, delegates=delegates,
    )
    verdict.headline = _cohort_headline(cfg, issue, verdict, llm, lang, log)
    log(f"  [{cohort:9}] {len(pids):4} posts / {n_authors:3} accounts"
        f" -> {len(delegates)} delegate(s)"
        + (f", p={blended:.0%}" if blended is not None else ""))
    return verdict


def _make_delegate(cfg, issue, cluster: OpinionCluster, post_map, handles,
                   llm, lang, log) -> Optional[Delegate]:
    stance_label = issue.stance_label(cluster.stance, lang)
    stats = {
        "cohort_label": cohort_label(cluster.cohort, lang),
        "stance_label": stance_label,
        "n_posts": cluster.n_posts,
        "n_authors": cluster.n_authors,
        "share": cluster.share,
        "mean_probability": cluster.mean_probability,
    }
    quotes = [
        {"post_id": q.post_id, "handle": q.handle,
         "text": textutil.truncate(textutil.clean(q.text), 260), "weight": q.weight}
        for q in cluster.top_quotes
    ]
    lang_name = LANG_NAME.get(lang, "English")

    try:
        if llm is None:
            raise LLMUnavailable("no llm configured")
        draft = llm.structured(
            system=DELEGATE_SYSTEM_TMPL.format(lang_name=lang_name),
            user=delegate_user_prompt(issue=issue, cluster_stats=stats,
                                      claims=cluster.exemplar_claims, quotes=quotes),
            schema=DelegateDraft,
            model=cfg.llm.synthesize_model,
            effort=cfg.llm.synthesize_effort,
            max_tokens=4000,
        )
    except LLMUnavailable:
        draft = offline_stub.delegate(
            stance_label=stance_label, claims=cluster.exemplar_claims,
            quote_ids=[q.post_id for q in cluster.top_quotes], lang=lang)
    except Exception as e:                                        # noqa: BLE001
        log(f"  ! delegate synthesis failed ({e}); using template")
        draft = offline_stub.delegate(
            stance_label=stance_label, claims=cluster.exemplar_claims,
            quote_ids=[q.post_id for q in cluster.top_quotes], lang=lang)

    # Guard: the model may only cite ids it was shown.
    allowed = {q.post_id for q in cluster.top_quotes}
    cited = [pid for pid in draft.quote_ids if pid in allowed]
    if not cited:
        cited = [q.post_id for q in cluster.top_quotes[:2]]
    quote_by_id = {q.post_id: q for q in cluster.top_quotes}

    return Delegate(
        id=f"{cluster.id}-d",
        issue_id=issue.id,
        cohort=cluster.cohort,
        name=draft.name.strip()[:40] or stance_label,
        verdict=draft.verdict.strip(),
        stance=cluster.stance,
        rationale=[r.strip() for r in draft.rationale if r.strip()][:4],
        caveat=draft.caveat.strip(),
        probability=cluster.mean_probability,
        share=cluster.share,
        weight=cluster.weight,
        n_posts=cluster.n_posts,
        n_authors=cluster.n_authors,
        quotes=[quote_by_id[pid] for pid in cited if pid in quote_by_id],
        cluster_id=cluster.id,
    )


def _cohort_headline(cfg, issue, verdict: CohortVerdict, llm, lang, log) -> str:
    label = cohort_label(verdict.cohort, lang)
    stats = {
        "n_posts": verdict.n_posts, "n_authors": verdict.n_authors,
        "stance_shares": {issue.stance_label(k, lang): v
                          for k, v in verdict.stance_shares.items()},
        "probability": verdict.probability, "agreement": verdict.agreement,
    }
    delegates = [{"name": d.name, "share": d.share, "n_authors": d.n_authors,
                  "verdict": d.verdict} for d in verdict.delegates]
    try:
        if llm is None:
            raise LLMUnavailable("no llm configured")
        out = llm.structured(
            system=COHORT_HEADLINE_SYSTEM.format(lang_name=LANG_NAME.get(lang, "English")),
            user=cohort_headline_prompt(issue=issue, cohort_label=label,
                                        stats=stats, delegates=delegates),
            schema=CohortHeadline,
            model=cfg.llm.synthesize_model,
            effort="low",
            max_tokens=1500,
        )
        return out.headline.strip()
    except LLMUnavailable:
        pass
    except Exception as e:                                        # noqa: BLE001
        log(f"  ! cohort headline failed ({e})")
    return offline_stub.cohort_headline(
        cohort_label=label,
        stance_label=issue.stance_label(verdict.dominant_stance, lang),
        share=verdict.stance_shares.get(verdict.dominant_stance, 0.0),
        n_authors=verdict.n_authors, probability=verdict.probability, lang=lang,
    ).headline


# ======================================================================
def _blend(cfg, verdicts: dict[str, CohortVerdict]) -> Optional[float]:
    """Weighted blend across tiers, renormalised over tiers that have data."""
    weights = cfg.blend_weights()
    num = den = 0.0
    for cohort, v in verdicts.items():
        if v.probability is None:
            continue
        w = weights.get(cohort, 0.0)
        num += w * v.probability
        den += w
    return num / den if den > 0 else None


def _divergences(cfg, verdicts: dict[str, CohortVerdict], lang: str) -> list[Divergence]:
    have = [(c, v) for c, v in verdicts.items() if v.probability is not None]
    out: list[Divergence] = []
    for i, (c1, v1) in enumerate(have):
        for c2, v2 in have[i + 1:]:
            delta = abs(v1.probability - v2.probability)
            if delta >= cfg.thresholds.divergence_alert:
                higher, lower = ((c1, v1), (c2, v2)) if v1.probability > v2.probability \
                    else ((c2, v2), (c1, v1))
                if lang == "zh":
                    note = (f"{cohort_label(higher[0], lang)} 比 "
                            f"{cohort_label(lower[0], lang)} 高 {delta:.0%}"
                            f"（{higher[1].probability:.0%} vs {lower[1].probability:.0%}）")
                else:
                    note = (f"{cohort_label(higher[0], lang)} sits {delta:.0%} above "
                            f"{cohort_label(lower[0], lang)} "
                            f"({higher[1].probability:.0%} vs {lower[1].probability:.0%})")
                out.append(Divergence(pair=(higher[0], lower[0]), delta=delta, note=note))
    out.sort(key=lambda d: -d.delta)
    return out


def _describe_deltas(prev: Optional[Snapshot], verdicts: dict[str, CohortVerdict],
                     blended: Optional[float], lang: str) -> list[str]:
    if prev is None:
        return []
    out = []
    if prev.blended_probability is not None and blended is not None:
        d = blended - prev.blended_probability
        if abs(d) >= 0.01:
            arrow = "↑" if d > 0 else "↓"
            out.append(
                f"blended {arrow} {abs(d):.0%} (from {prev.blended_probability:.0%} "
                f"to {blended:.0%})"
            )
    for cohort, v in verdicts.items():
        old = prev.cohorts.get(cohort)
        if old is None:
            out.append(f"{cohort}: new tier, no prior reading")
            continue
        if old.probability is not None and v.probability is not None:
            d = v.probability - old.probability
            if abs(d) >= 0.03:
                out.append(f"{cohort}: {old.probability:.0%} -> {v.probability:.0%}")
        if old.dominant_stance != v.dominant_stance:
            out.append(f"{cohort}: dominant stance flipped "
                       f"{old.dominant_stance} -> {v.dominant_stance}")
        old_names = {d.name for d in old.delegates}
        for d in v.delegates:
            if d.name not in old_names and d.share >= 0.15:
                out.append(f"{cohort}: new bloc {d.name!r} at {d.share:.0%}")
    return out


def _global(cfg, issue, snap: Snapshot, divergences, deltas, llm, lang, log):
    blocks = []
    for cohort, v in snap.cohorts.items():
        p = f"{v.probability:.0%}" if v.probability is not None else "n/a"
        blocks.append(
            f"- {cohort_label(cohort, lang)}: {v.headline}\n"
            f"    implied {p} | agreement {v.agreement:.0%} | confidence {v.confidence:.0%}"
            f" | {v.n_posts} posts / {v.n_authors} accounts"
        )
    div_lines = [f"- {d.note}" for d in divergences]
    delta_lines = [f"- {d}" for d in deltas]

    try:
        if llm is None:
            raise LLMUnavailable("no llm configured")
        out = llm.structured(
            system=GLOBAL_SYSTEM.format(lang_name=LANG_NAME.get(lang, "English")),
            user=global_prompt(issue=issue, cohort_blocks=blocks,
                               divergences=div_lines, deltas=delta_lines,
                               blended=snap.blended_probability),
            schema=GlobalSynthesis,
            model=cfg.llm.synthesize_model,
            effort=cfg.llm.synthesize_effort,
            max_tokens=4000,
        )
        notes = [f"读数：{r}" if lang == "zh" else f"Reading: {r}" for r in out.reading]
        notes += [f"关注：{w}" if lang == "zh" else f"Watch: {w}" for w in out.watch_for]
        return out.headline.strip(), notes
    except LLMUnavailable:
        pass
    except Exception as e:                                        # noqa: BLE001
        log(f"  ! global synthesis failed ({e})")

    stub = offline_stub.global_synthesis(
        lines=[v.headline for v in snap.cohorts.values() if v.headline],
        divergences=[d.note for d in divergences], lang=lang)
    return stub.headline, list(stub.reading) + list(stub.watch_for)
