"""Self-contained HTML report (no external assets, opens from disk)."""
from __future__ import annotations

import datetime as dt
import pathlib
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..models import Alert, Snapshot
from ..pipeline.synthesize import cohort_label

TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["pct"] = lambda v: "—" if v is None else f"{v * 100:.0f}%"
    return env


def render(snap: Snapshot, issue, alerts: Optional[list[Alert]] = None,
           lang: str = "zh", history: Optional[list[Snapshot]] = None,
           out_path: str | pathlib.Path = "out/report.html") -> pathlib.Path:
    tmpl = _env().get_template("report.html")

    cohorts = [
        {
            "key": key,
            "label": cohort_label(key, lang),
            "v": v,
            "stances": [
                {"label": issue.stance_label(s, lang), "share": share}
                for s, share in v.stance_shares.items()
            ],
        }
        for key, v in snap.cohorts.items()
    ]

    series = []
    for s in (history or []):
        row = {"ts": s.ts.strftime("%m-%d %H:%M"), "blended": s.blended_probability}
        for k, v in s.cohorts.items():
            row[k] = v.probability
        series.append(row)

    html = tmpl.render(
        snap=snap, issue=issue, lang=lang, zh=(lang == "zh"),
        cohorts=cohorts, alerts=alerts or [], series=series,
        generated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        spark=_sparkline(series),
    )
    path = pathlib.Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _sparkline(series: list[dict]) -> str:
    """Inline SVG polyline of the blended reading; no chart library."""
    pts = [(i, r["blended"]) for i, r in enumerate(series) if r["blended"] is not None]
    if len(pts) < 3:
        return ""      # two points is a line segment, not a trend
    w, h, pad = 640, 90, 8
    xs = [p[0] for p in pts]
    x0, x1 = min(xs), max(xs)
    span = (x1 - x0) or 1
    coords = " ".join(
        f"{pad + (x - x0) / span * (w - 2 * pad):.1f},"
        f"{h - pad - v * (h - 2 * pad):.1f}"
        for x, v in pts
    )
    last = pts[-1]
    cx = pad + (last[0] - x0) / span * (w - 2 * pad)
    cy = h - pad - last[1] * (h - 2 * pad)
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="spark">'
        f'<polyline points="{coords}" fill="none" stroke="currentColor" '
        f'stroke-width="2" vector-effect="non-scaling-stroke"/>'
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.5" fill="currentColor"/></svg>'
    )
