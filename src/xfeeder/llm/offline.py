"""Deterministic, no-network stand-ins for the two model stages.

Purpose: `xfeeder demo` must run end to end with no API key, so that the
pipeline, the weighting, the clustering, the report and the alert logic can
all be inspected and tested before anyone spends a cent. The quality of the
language is obviously far below the real synthesiser - the numbers, however,
are computed by exactly the same code path, because the model never
produces numbers.
"""
from __future__ import annotations

import collections
import re
from typing import Optional

from .. import textutil
from .prompts import (
    AuthorClass, CohortHeadline, DelegateDraft, ExtractionBatch,
    GlobalSynthesis, PostExtraction,
)

_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_ODDS = re.compile(r"\b(\d{1,2})\s*(?:in|/)\s*10\b")
# A percentage in a macro post is far more often an inflation rate or a
# policy rate than a probability. Only read one as a probability when the
# post frames it as odds. Conservative on purpose: a missed number costs a
# little coverage, an invented one corrupts the tier's headline reading.
_PROB_CUE = re.compile(
    r"(chance|odds|probabilit|implie[sd]|imply|priced|pricing|bets?|"
    r"make it|coin flip|概率|可能性|定价|几成|赔率|胜率)",
    re.IGNORECASE,
)
# ...and vetoed outright when the number is sitting next to a word that
# means it is a rate, not a likelihood.
_NOT_PROB = re.compile(
    r"(inflation|cpi|pce|core|wage|yield|growth|unemployment|payroll|rent|"
    r"return|通胀|利率|收益率|工资|涨幅|增速)",
    re.IGNORECASE,
)
_PROB_WINDOW = 45
_HEDGE_ZH = ("可能", "也许", "大概", "或许", "不确定", "?", "？")
# Words that, appearing just before a keyword, invert it. Crude, but it is
# the difference between reading "never tightened" as hawkish and dovish.
_NEG_EN = re.compile(
    r"\b(not|no|never|without|hardly|unlikely|nobody|none)\b|n't|far from",
    re.IGNORECASE,
)
_NEG_ZH = ("不", "没", "无", "别", "未", "难以")
_NEG_WINDOW = 28
_HEDGE_EN = ("maybe", "perhaps", "not sure", "could", "might")
_STRONG = ("绝对", "肯定", "必然", "一定", "definitely", "certainly", "no doubt", "guaranteed")


def classify_authors(rows: list[dict]) -> list[AuthorClass]:
    out = []
    for r in rows:
        bio = (r.get("description") or "").lower()
        followers = r.get("followers", 0)
        lang = r.get("lang", "en")
        if any(k in bio for k in ("official account", "central bank", "federal reserve",
                                  "ministry", "bureau of")):
            cohort, conf = "official", 0.7
        elif any(k in bio for k in ("reporter", "journalist", "correspondent",
                                    "bureau chief", "editor at", "记者", "财经媒体")):
            cohort, conf = "pro_media", 0.6
        elif followers >= 20_000:
            cohort, conf = ("cn_kol" if lang == "zh" else "en_kol"), 0.5
        else:
            cohort, conf = "crowd", 0.5
        out.append(AuthorClass(index=r["index"], cohort=cohort, confidence=conf,
                               reason="offline heuristic"))
    return out


def _read_probability(text: str) -> Optional[float]:
    """Read a probability only where the post frames a number as odds."""
    for m in _PCT.finditer(text):
        lo = max(0, m.start() - _PROB_WINDOW)
        hi = min(len(text), m.end() + _PROB_WINDOW)
        window = text[lo:hi]
        if not _PROB_CUE.search(window) or _NOT_PROB.search(window):
            continue
        v = float(m.group(1)) / 100.0
        if 0.0 <= v <= 1.0:
            return v
    m2 = _ODDS.search(text)
    if m2:
        return int(m2.group(1)) / 10.0
    return None


def _negated(text: str, at: int) -> bool:
    """Is there a negation marker in the ~28 characters before `at`?"""
    window = text[max(0, at - _NEG_WINDOW):at]
    # Word boundaries matter: "another" contains "not", "cannot" contains "no".
    return bool(_NEG_EN.search(window)) or any(n in window for n in _NEG_ZH)


def _match_score(text: str, low: str, keywords: list[str]) -> tuple[int, list[str]]:
    """Length-weighted keyword score, skipping negated occurrences.

    Longest match wins, so a specific phrase beats a substring of it:
    "不会加息" (hold) must outrank the "加息" (hike) inside it.
    """
    score, hits = 0, []
    for k in keywords:
        hay, needle = (low, k.lower()) if k.lower() in low else (text, k)
        at = hay.find(needle)
        if at < 0:
            continue
        if _negated(hay, at):
            continue
        score += len(k)
        hits.append(k)
    return score, hits


def extract(rows: list[dict], issue) -> ExtractionBatch:
    items = []
    for r in rows:
        text = r["text"]
        low = textutil.normalise(text)
        scores, hits = {}, {}
        for opt in issue.axis:
            scores[opt.id], hits[opt.id] = _match_score(text, low, opt.keywords or [])
        stance = max(scores, key=lambda k: scores[k]) if scores else "unclear"
        if not scores or scores[stance] == 0:
            stance = "unclear"

        prob = _read_probability(text)

        intensity = 0.5
        if any(h in text.lower() for h in _HEDGE_EN) or any(h in text for h in _HEDGE_ZH):
            intensity = 0.3
        if any(s in text.lower() for s in _STRONG):
            intensity = 0.9

        items.append(PostExtraction(
            index=r["index"],
            relevant=stance != "unclear" or prob is not None,
            stance=stance,
            probability=prob,
            key_claim=_canonical_claim(issue, stance, hits.get(stance, []), text),
            reasoning_kind="market_pricing" if prob is not None else "opinion",
            intensity=intensity,
            is_question=text.strip().endswith(("?", "？")),
        ))
    return ExtractionBatch(items=items)


def _canonical_claim(issue, stance: str, hits: list[str], text: str) -> str:
    """Stand-in for the model's canonicalised one-sentence claim.

    The real extractor writes a de-stylised sentence so that paraphrases
    collide during clustering. Offline we approximate that with the stance
    label plus the argument words the post actually used - crude, but it
    clusters by argument instead of by writing style, which is the property
    the pipeline depends on.
    """
    label = issue.stance_label(stance, "zh")
    topic = _topic_words(text)
    reason = "、".join(sorted(set(hits))[:2])
    parts = [p for p in (label, topic, reason) if p]
    return "：".join(parts[:2]) + (f"（{reason}）" if reason and topic else "")


_TOPIC_HINTS = {
    "通胀": ("通胀", "cpi", "inflation", "物价", "菜价", "涨价", "core", "pce", "price",
             "rent", "groceries", "receipt", "insurance", "cost", "expensive",
             "生活成本", "贵"),
    "就业与工资": ("就业", "工资", "labour", "labor", "jobs", "payroll", "employment", "wage"),
    "信贷与流动性": ("信贷", "credit", "liquidity", "流动性", "refinance", "mortgage",
                     "housing", "bank"),
    "市场定价": ("定价", "futures", "swaps", "priced", "pricing", "fedwatch", "odds",
                 "market", "positioning", "短仓", "定价加息"),
    "官方表态": ("powell", "鲍威尔", "committee", "statement", "guidance", "dots",
                 "telegraph", "官方", "声明"),
}


def _topic_words(text: str) -> str:
    low = text.lower()
    for topic, keys in _TOPIC_HINTS.items():
        if any(k in low or k in text for k in keys):
            return topic
    return ""


def delegate(*, stance_label: str, claims: list[str], quote_ids: list[str],
             lang: str = "zh") -> DelegateDraft:
    counter = collections.Counter(claims)
    top = [c for c, _ in counter.most_common(3)]
    # Distinguish blocs that share a stance by what they argue from.
    discriminator = ""
    if top and "：" in top[0]:
        discriminator = top[0].split("：", 1)[1].split("（")[0].strip()
    suffix = f"·{discriminator}" if discriminator else ""
    if lang == "zh":
        return DelegateDraft(
            name=f"{stance_label}派{suffix}",
            verdict=f"我们相信{stance_label}。",
            rationale=top,
            caveat="（离线模式未生成）",
            quote_ids=quote_ids[:2],
        )
    return DelegateDraft(
        name=f"The {stance_label} bloc{(' / ' + discriminator) if discriminator else ''}",
        verdict=f"We believe: {stance_label}.",
        rationale=top,
        caveat="(not generated in offline mode)",
        quote_ids=quote_ids[:2],
    )


def cohort_headline(*, cohort_label: str, stance_label: str, share: float,
                    n_authors: int, probability: Optional[float],
                    lang: str = "zh") -> CohortHeadline:
    p = f"，隐含概率 {probability:.0%}" if probability is not None else ""
    if lang == "zh":
        return CohortHeadline(
            headline=f"{cohort_label}：{share:.0%} 的加权声量倾向「{stance_label}」"
                     f"（{n_authors} 个账号）{p}。"
        )
    pe = f", implied {probability:.0%}" if probability is not None else ""
    return CohortHeadline(
        headline=f"{cohort_label}: {share:.0%} of weighted voice leans "
                 f"'{stance_label}' ({n_authors} accounts){pe}."
    )


def global_synthesis(*, lines: list[str], divergences: list[str],
                     lang: str = "zh") -> GlobalSynthesis:
    if lang == "zh":
        return GlobalSynthesis(
            headline="离线模式：以下为纯统计结果，未经语言合成。",
            reading=lines[:4],
            watch_for=divergences[:3] or ["各层级之间未见显著分歧"],
        )
    return GlobalSynthesis(
        headline="Offline mode: statistics only, no language synthesis.",
        reading=lines[:4],
        watch_for=divergences[:3] or ["no material divergence between tiers"],
    )
