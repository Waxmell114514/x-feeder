"""Stage 3 - decide which tier each venue belongs to.

The X-era version of this stage had to classify accounts, which was both
expensive and unreliable. Public sources make it almost free, because the
venue is the unit: r/AskEconomics is an expert community whoever posts in
it, reuters.com is a newsroom, federalreserve.gov is the institution
itself. A judgement made once about a venue holds for every document it
ever produces.

Ordered by cost and reliability, cheapest first:

  1. the config's channel allowlists - free, exact, and the only thing that
     should ever put a venue in `official`;
  2. the tier carried by the query that found it - free, and written by a
     human in the issue file;
  3. a keyword heuristic on the venue's own name - free;
  4. one Jev Choice for whatever is left, cached forever.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from ..config import normalise_channel
from ..jev import questions as Q
from ..models import ChannelTier, Document
from ..store import Store


def run_tier(cfg, store: Store, documents: list[Document], jev,
             log=print) -> dict:
    known = store.get_channel_tiers()
    allow = cfg.channel_table()

    by_channel: dict[str, list[Document]] = defaultdict(list)
    for doc in documents:
        by_channel[doc.channel].append(doc)

    decided: list[ChannelTier] = []
    unresolved: list[str] = []
    counts: Counter = Counter()

    for channel, docs in by_channel.items():
        if channel in known and known[channel].method in ("allowlist", "manual", "jev"):
            counts[known[channel].tier] += 1
            continue

        pinned = _allowlisted(channel, allow)
        if pinned:
            decided.append(ChannelTier(channel=channel, tier=pinned,
                                       method="allowlist", confidence=1.0,
                                       reason="named in the config allowlist"))
            counts[pinned] += 1
            continue

        hint = next((d.tier_hint for d in docs if d.tier_hint), "")
        if hint in cfg.tiers:
            decided.append(ChannelTier(
                channel=channel, tier=hint, method="query_prior", confidence=0.9,
                reason="the issue file searched this venue as this tier"))
            counts[hint] += 1
            continue

        guess = _heuristic(cfg, channel)
        if guess:
            tier, why = guess
            decided.append(ChannelTier(channel=channel, tier=tier,
                                       method="heuristic", confidence=0.65,
                                       reason=why))
            counts[tier] += 1
            continue

        unresolved.append(channel)

    if unresolved:
        resolved = _ask_jev(cfg, by_channel, unresolved, jev, log)
        for item in resolved:
            counts[item.tier] += 1
        decided.extend(resolved)

    store.upsert_channel_tiers(decided)
    return {"new": len(decided), "counts": dict(counts),
            "asked": len(unresolved), "channels": len(by_channel)}


def _allowlisted(channel: str, allow: dict[str, str]) -> str:
    key = normalise_channel(channel)
    if key in allow:
        return allow[key]
    # A domain allowlist entry covers its subdomains: an entry for
    # "reuters.com" should match "feeds.reuters.com".
    for listed, tier in allow.items():
        if "." in listed and key.endswith("." + listed):
            return tier
    return ""


def _heuristic(cfg, channel: str) -> tuple[str, str] | None:
    name = normalise_channel(channel)
    for tier in ("official", "pro_media", "expert"):
        rule = cfg.tiers.get(tier)
        if not rule:
            continue
        for keyword in rule.keywords:
            if keyword.lower() in name:
                return tier, f"venue name contains {keyword!r}"
    # Government and inter-governmental domains are not a judgement call.
    if name.endswith((".gov", ".gov.uk", ".gov.cn", ".mil")) or name.endswith(".int"):
        return "official", "government domain"
    return None


def _ask_jev(cfg, by_channel, channels: list[str], jev, log) -> list[ChannelTier]:
    floor = cfg.jev.tier_confidence_floor
    items = []
    for channel in channels:
        docs = by_channel[channel]
        samples = [d.title or d.body[:160] for d in docs[:5]]
        kind = docs[0].channel_kind if docs else "forum"
        items.append((channel, Q.tier_state(channel, kind, samples),
                      Q.tier_questions()))

    errors: list[str] = []
    responses = jev.ask_many(
        items, on_error=lambda key, e: errors.append(f"{key}: {e}"))
    for message in errors[:3]:
        log(f"  ! tiering failed for {message}")

    out: list[ChannelTier] = []
    for channel in channels:
        response = responses.get(channel)
        if response is None:
            out.append(ChannelTier(
                channel=channel, tier="crowd", method="heuristic", confidence=0.2,
                reason="not judged; defaulted to the least authoritative tier"))
            continue
        tier, _, confidence = response.choice("tier")
        if tier not in cfg.tiers or confidence < floor:
            # A venue we cannot place goes to `crowd`, never upward. A wrong
            # promotion into `official` corrupts the whole reading; a wrong
            # demotion costs one venue's authority.
            out.append(ChannelTier(
                channel=channel, tier="crowd", method="jev",
                confidence=round(confidence, 3),
                reason=f"unplaced (best guess {tier or 'none'} at "
                       f"{confidence:.0%}, floor {floor:.0%})"))
            continue
        out.append(ChannelTier(channel=channel, tier=tier, method="jev",
                               confidence=round(confidence, 3),
                               reason="judged from the venue and its recent items"))
    return out
