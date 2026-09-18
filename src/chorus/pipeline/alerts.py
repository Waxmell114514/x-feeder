"""Stage 8 - what changed, and is it worth waking someone for.

For a monitor, the level is background; the *move* and the *split* are the
signal. Six rules, all computed from two consecutive snapshots:

  consensus_shift        the blended reading moved more than the threshold
  stance_flip            a tier's leading position changed side
  divergence             two tiers are further apart than the threshold
  official_contradiction officialdom and the public point opposite ways -
                         the single most useful pattern this system can see
  new_argument           a bloc that was marginal last time is now material
  volume_spike           a tier is suddenly talking much more

`new_argument` is compared by leader id rather than by display name, which
is the quiet benefit of a panel that is declared instead of re-discovered:
"this bloc is new" is now a fact about the data, not an artefact of a model
choosing different words for the same faction two runs in a row.
"""
from __future__ import annotations

import os
from typing import Optional

from .. import phrasing, tier_label
from ..http import request
from ..models import Alert, Snapshot

SEVERITY_ORDER = {"info": 0, "warn": 1, "critical": 2}


def compute_alerts(cfg, issue, current: Snapshot, previous: Optional[Snapshot],
                   lang: str = "zh") -> list[Alert]:
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
            delta = current.blended_probability - previous.blended_probability
            if abs(delta) >= th.consensus_shift_alert:
                add("consensus_shift",
                    "critical" if abs(delta) >= 2 * th.consensus_shift_alert else "warn",
                    phrasing.shift_note(before=previous.blended_probability,
                                        after=current.blended_probability, lang=lang))

        for tier, v in current.tiers.items():
            old = previous.tiers.get(tier)
            if old is None:
                continue
            if old.dominant_stance != v.dominant_stance:
                add("stance_flip", "critical",
                    phrasing.flip_note(
                        tier_label=tier_label(tier, lang),
                        before_label=issue.stance_label(old.dominant_stance, lang),
                        after_label=issue.stance_label(v.dominant_stance, lang),
                        lang=lang),
                    detail=v.headline)

            known = {d.leader_id for d in old.delegates}
            for d in v.delegates:
                if d.leader_id not in known and d.share >= 0.15:
                    add("new_argument", "warn",
                        phrasing.new_bloc_note(tier_label=tier_label(tier, lang),
                                               name=d.name, share=d.share, lang=lang),
                        detail=" / ".join(d.rationale[:2]),
                        evidence=[q.url for q in d.quotes if q.url])

            if old.n_docs and v.n_docs / max(1, old.n_docs) >= th.volume_spike_ratio:
                add("volume_spike", "info",
                    phrasing.volume_note(tier_label=tier_label(tier, lang),
                                         ratio=v.n_docs / max(1, old.n_docs),
                                         lang=lang))

    # ---- structure ----------------------------------------------------
    for d in current.divergences:
        add("divergence",
            "warn" if d.delta < 2 * th.divergence_alert else "critical", d.note)

    official = current.tiers.get("official")
    crowd = current.tiers.get("crowd")
    if official and crowd and official.probability is not None \
            and crowd.probability is not None:
        gap = crowd.probability - official.probability
        if abs(gap) >= th.divergence_alert:
            add("official_contradiction", "critical",
                phrasing.official_gap(gap=gap, crowd_p=crowd.probability,
                                      official_p=official.probability, lang=lang),
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
