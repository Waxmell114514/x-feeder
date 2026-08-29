"""Stage 7 - what changed, and is it worth waking someone for.

For a monitor, the level is background; the *move* and the *split* are the
signal. Five rules, all computed from two consecutive snapshots:

  consensus_shift        the blended reading moved more than the threshold
  stance_flip            a tier's dominant stance changed side
  divergence             two tiers are further apart than the threshold
  official_contradiction officialdom and the crowd point opposite ways -
                         the single most useful pattern this system can see
  new_argument           a bloc that did not exist last time is now material
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Optional

from .. import COHORT_LABELS_ZH
from ..http import request
from ..models import Alert, Snapshot

SEVERITY_ORDER = {"info": 0, "warn": 1, "critical": 2}


def _label(cohort: str, lang: str) -> str:
    if lang == "zh":
        return COHORT_LABELS_ZH.get(cohort, cohort)
    return cohort


def compute_alerts(cfg, issue, current: Snapshot,
                   previous: Optional[Snapshot], lang: str = "zh") -> list[Alert]:
    th = cfg.thresholds
    out: list[Alert] = []
    now = current.ts

    def add(kind, severity, title, detail="", evidence=None):
        out.append(Alert(issue_id=current.issue_id, ts=now, kind=kind,
                         severity=severity, title=title, detail=detail,
                         evidence=evidence or []))

    # ---- movement -----------------------------------------------------
    if previous is not None:
        if (current.blended_probability is not None
                and previous.blended_probability is not None):
            d = current.blended_probability - previous.blended_probability
            if abs(d) >= th.consensus_shift_alert:
                arrow = "↑" if d > 0 else "↓"
                title = (f"综合读数 {arrow}{abs(d):.0%}："
                         f"{previous.blended_probability:.0%} → {current.blended_probability:.0%}"
                         if lang == "zh" else
                         f"Blended reading {arrow}{abs(d):.0%}: "
                         f"{previous.blended_probability:.0%} -> {current.blended_probability:.0%}")
                add("consensus_shift", "critical" if abs(d) >= 2 * th.consensus_shift_alert
                    else "warn", title)

        for cohort, v in current.cohorts.items():
            old = previous.cohorts.get(cohort)
            if old is None:
                continue
            if old.dominant_stance != v.dominant_stance:
                title = (f"{_label(cohort, lang)} 立场翻转："
                         f"{issue.stance_label(old.dominant_stance, lang)} → "
                         f"{issue.stance_label(v.dominant_stance, lang)}"
                         if lang == "zh" else
                         f"{cohort} flipped: {old.dominant_stance} -> {v.dominant_stance}")
                add("stance_flip", "critical", title, detail=v.headline)

            old_names = {d.name for d in old.delegates}
            for d in v.delegates:
                if d.name not in old_names and d.share >= 0.15:
                    title = (f"{_label(cohort, lang)} 出现新论点「{d.name}」"
                             f"（{d.share:.0%}）" if lang == "zh" else
                             f"New bloc in {cohort}: {d.name} ({d.share:.0%})")
                    add("new_argument", "warn", title, detail=d.verdict,
                        evidence=[q.url for q in d.quotes])

            if old.n_posts and v.n_posts / max(1, old.n_posts) >= th.volume_spike_ratio:
                title = (f"{_label(cohort, lang)} 讨论量放大 "
                         f"{v.n_posts / max(1, old.n_posts):.1f}×"
                         if lang == "zh" else
                         f"{cohort} volume x{v.n_posts / max(1, old.n_posts):.1f}")
                add("volume_spike", "info", title)

    # ---- structure ----------------------------------------------------
    for d in current.divergences:
        add("divergence", "warn" if d.delta < 2 * th.divergence_alert else "critical",
            d.note)

    official = current.cohorts.get("official")
    crowd = current.cohorts.get("crowd")
    if official and crowd and official.probability is not None \
            and crowd.probability is not None:
        gap = crowd.probability - official.probability
        if abs(gap) >= th.divergence_alert:
            direction = "高于" if gap > 0 else "低于"
            title = (f"大众读数{direction}官方指引 {abs(gap):.0%}"
                     f"（{crowd.probability:.0%} vs {official.probability:.0%}）"
                     if lang == "zh" else
                     f"Crowd is {abs(gap):.0%} "
                     f"{'above' if gap > 0 else 'below'} official guidance "
                     f"({crowd.probability:.0%} vs {official.probability:.0%})")
            add("official_contradiction", "critical", title,
                detail=(crowd.headline or "") + " || " + (official.headline or ""))

    order = {"critical": 0, "warn": 1, "info": 2}
    out.sort(key=lambda a: order[a.severity])
    return out


# ----------------------------------------------------------------------
def dispatch(cfg, alerts: list[Alert], issue_title: str, log=print) -> int:
    if not cfg.alerts.enabled or not alerts:
        return 0
    url = os.environ.get(cfg.alerts.webhook_url_env, "")
    if not url:
        log(f"  ! alerts enabled but {cfg.alerts.webhook_url_env} is unset")
        return 0

    floor = SEVERITY_ORDER.get(cfg.alerts.min_severity, 1)
    send = [a for a in alerts if SEVERITY_ORDER[a.severity] >= floor]
    if not send:
        return 0

    icon = {"critical": "🔴", "warn": "🟠", "info": "🔵"}
    lines = [f"*{issue_title}*"]
    lines += [f"{icon[a.severity]} {a.title}" + (f"\n    {a.detail}" if a.detail else "")
              for a in send]
    try:
        request("POST", url, json_body={"text": "\n".join(lines)}, retries=2)
    except Exception as e:                                        # noqa: BLE001
        log(f"  ! webhook failed: {e}")
        return 0
    return len(send)
