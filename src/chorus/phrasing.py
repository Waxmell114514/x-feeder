"""Every sentence the report can say.

Jev returns typed decisions and cannot write a word, so this file replaces
what used to be a language model's job. That sounds like a downgrade and
mostly is not, for a reason worth stating: the sentences in a monitoring
report are the same six sentences every time, and the only things that vary
are numbers that were measured rather than phrased. A template cannot round
"53%" into "a clear majority", cannot promote the most vivid post into the
headline, and cannot describe a bloc that does not exist.

What is genuinely lost is the part a model was good at: saying what a bloc
argues, in the reader's language. This system gives that up and replaces it
with quotation. A delegate's reasons are phrases lifted verbatim out of the
documents assigned to it, in whatever language they were written. That is
narrower and less pretty, and it is checkable.

Chinese is the default output language because the numbers are language-
independent and the quoted material stays in its original language either
way.
"""
from __future__ import annotations

from typing import Optional

ZH = "zh"


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0%}"


# ----------------------------------------------------------------------
def tier_headline(*, tier_label: str, stance_label: str, share: float,
                  n_speakers: int, n_channels: int, probability: Optional[float],
                  split: Optional[list[tuple[str, float]]] = None,
                  lang: str = ZH) -> str:
    """One line for a whole tier."""
    if lang == ZH:
        p = f"，隐含概率 {_pct(probability)}" if probability is not None else ""
        where = f"（{n_speakers} 个发言者 / {n_channels} 个场所）"
        if split:
            parts = "、".join(f"{label} {_pct(s)}" for label, s in split[:3])
            return f"{tier_label}：意见分裂，{parts}{where}{p}。"
        return f"{tier_label}：{_pct(share)} 的加权声量倾向「{stance_label}」{where}{p}。"

    p = f", implied {_pct(probability)}" if probability is not None else ""
    where = f" ({n_speakers} speakers / {n_channels} venues)"
    if split:
        parts = ", ".join(f"{label} {_pct(s)}" for label, s in split[:3])
        return f"{tier_label}: split - {parts}{where}{p}."
    return (f"{tier_label}: {_pct(share)} of weighted voice leans "
            f"'{stance_label}'{where}{p}.")


def delegate_verdict(stance_label: str, lang: str = ZH) -> str:
    if lang == ZH:
        return f"我们相信{stance_label}。"
    return f"We hold that: {stance_label}."


def delegate_share_note(*, share: float, n_docs: int, n_speakers: int,
                        lang: str = ZH) -> str:
    if lang == ZH:
        return f"{_pct(share)} 的声量 · {n_docs} 篇 / {n_speakers} 个发言者"
    return f"{_pct(share)} of the tier · {n_docs} documents / {n_speakers} speakers"


def unassigned_note(share: float, lang: str = ZH) -> str:
    if lang == ZH:
        return (f"{_pct(share)} 的声量没有归入任何意见领袖"
                f"——他们在说这个面板没有覆盖的东西。")
    return (f"{_pct(share)} of this tier's voice joined no leader - they are "
            f"arguing something the panel does not cover.")


# ----------------------------------------------------------------------
def divergence_note(*, higher_label: str, lower_label: str, delta: float,
                    higher_p: float, lower_p: float, lang: str = ZH) -> str:
    if lang == ZH:
        return (f"{higher_label} 比 {lower_label} 高 {_pct(delta)}"
                f"（{_pct(higher_p)} vs {_pct(lower_p)}）")
    return (f"{higher_label} sits {_pct(delta)} above {lower_label} "
            f"({_pct(higher_p)} vs {_pct(lower_p)})")


def official_gap(*, gap: float, crowd_p: float, official_p: float,
                 lang: str = ZH) -> str:
    if lang == ZH:
        direction = "高于" if gap > 0 else "低于"
        return (f"大众读数{direction}官方口径 {_pct(abs(gap))}"
                f"（{_pct(crowd_p)} vs {_pct(official_p)}）")
    return (f"The public reading is {_pct(abs(gap))} "
            f"{'above' if gap > 0 else 'below'} official guidance "
            f"({_pct(crowd_p)} vs {_pct(official_p)})")


def global_headline(*, blended: Optional[float], top_divergence: Optional[str],
                    dominant_label: str, n_docs: int, lang: str = ZH) -> str:
    """The one line at the top.

    When the tiers disagree, the disagreement is the headline - that is the
    whole reason they are kept apart.
    """
    if lang == ZH:
        if top_divergence:
            head = f"各层级不一致：{top_divergence}"
            return head + (f"；综合隐含概率 {_pct(blended)}。" if blended is not None
                           else "。")
        if blended is not None:
            return f"综合隐含概率 {_pct(blended)}，各层级读数一致。"
        return f"{n_docs} 篇公开发言中，主流立场是「{dominant_label}」。"

    if top_divergence:
        head = f"The tiers disagree: {top_divergence}"
        return head + (f"; blended reading {_pct(blended)}." if blended is not None
                       else ".")
    if blended is not None:
        return f"Blended reading {_pct(blended)}, with the tiers in agreement."
    return f"Across {n_docs} public documents the leading position is '{dominant_label}'."


# ----------------------------------------------------------------------
def shift_note(*, before: float, after: float, lang: str = ZH) -> str:
    arrow = "↑" if after > before else "↓"
    if lang == ZH:
        return f"综合读数 {arrow}{_pct(abs(after - before))}：{_pct(before)} → {_pct(after)}"
    return (f"Blended reading {arrow}{_pct(abs(after - before))}: "
            f"{_pct(before)} -> {_pct(after)}")


def flip_note(*, tier_label: str, before_label: str, after_label: str,
              lang: str = ZH) -> str:
    if lang == ZH:
        return f"{tier_label} 立场翻转：{before_label} → {after_label}"
    return f"{tier_label} flipped: {before_label} -> {after_label}"


def new_bloc_note(*, tier_label: str, name: str, share: float,
                  lang: str = ZH) -> str:
    if lang == ZH:
        return f"{tier_label} 出现新论点「{name}」（{_pct(share)}）"
    return f"New bloc in {tier_label}: {name} ({_pct(share)})"


def volume_note(*, tier_label: str, ratio: float, lang: str = ZH) -> str:
    if lang == ZH:
        return f"{tier_label} 讨论量放大 {ratio:.1f}×"
    return f"{tier_label} volume x{ratio:.1f}"


def coverage_note(*, n_docs: int, n_speakers: int, n_channels: int,
                  sources: dict[str, int], lang: str = ZH) -> str:
    mix = "、".join(f"{k} {v}" for k, v in sorted(sources.items(), key=lambda kv: -kv[1]))
    if lang == ZH:
        return (f"样本：{n_docs} 篇 / {n_speakers} 个发言者 / {n_channels} 个场所"
                f"（{mix}）")
    mix = ", ".join(f"{k} {v}" for k, v in sorted(sources.items(), key=lambda kv: -kv[1]))
    return (f"Sample: {n_docs} documents / {n_speakers} speakers / "
            f"{n_channels} venues ({mix})")


def thin_tier_note(*, tier_label: str, n_docs: int, lang: str = ZH) -> str:
    if lang == ZH:
        return f"{tier_label} 只有 {n_docs} 篇，读数不可靠"
    return f"{tier_label} has only {n_docs} documents; its reading is not reliable"


def stub_note(lang: str = ZH) -> str:
    if lang == ZH:
        return ("离线模式：没有调用 Jev，所有判断来自关键词替身。"
                "统计是真的，判断是粗糙的。")
    return ("Offline: Jev was not called; every judgement came from the keyword "
            "stand-in. The statistics are real, the judgements are crude.")
