"""Terminal report."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import tier_label
from ..models import Alert, Snapshot

BAR = "█"


def _bar(value: float, width: int = 18) -> str:
    filled = int(round(value * width))
    return BAR * filled + "·" * (width - filled)


def render(snap: Snapshot, issue, alerts: list[Alert] | None = None,
           lang: str = "zh", console: Console | None = None) -> None:
    console = console or Console()
    zh = lang == "zh"

    head = Text(snap.global_headline or "", style="bold")
    sub = f"{issue.title_zh or issue.title}   ·   {snap.ts:%Y-%m-%d %H:%M UTC}   ·   "
    sub += (f"{snap.n_docs} 篇 / {snap.n_speakers} 个发言者 / "
            f"{snap.n_channels} 个场所，窗口 {snap.window_hours}h"
            if zh else
            f"{snap.n_docs} documents / {snap.n_speakers} speakers / "
            f"{snap.n_channels} venues, {snap.window_hours}h window")
    if snap.blended_probability is not None:
        sub += ("   ·   综合隐含概率 " if zh else "   ·   blended ") + \
               f"{snap.blended_probability:.0%}"
    console.print(Panel(head, subtitle=sub, border_style="cyan"))

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("层级" if zh else "Tier", width=12)
    table.add_column("隐含" if zh else "Implied", width=7, justify="right")
    table.add_column("", width=18)
    table.add_column("样本" if zh else "Sample", width=12, justify="right")
    table.add_column("一致度" if zh else "Agree", width=7, justify="right")
    table.add_column("置信" if zh else "Conf", width=6, justify="right")

    for tier, v in snap.tiers.items():
        p = f"{v.probability:.0%}" if v.probability is not None else "—"
        table.add_row(
            tier_label(tier, lang), p,
            _bar(v.probability) if v.probability is not None else "",
            f"{v.n_docs}/{v.n_speakers}",
            f"{v.agreement:.0%}", f"{v.confidence:.0%}",
        )
    console.print(table)
    console.print()

    for tier, v in snap.tiers.items():
        console.print(Text(tier_label(tier, lang), style="bold cyan"),
                      Text(f"  {v.headline}", style="italic"))
        for d in v.delegates:
            console.print(
                f"    [bold]{d.name}[/bold] "
                f"[dim]({d.share:.0%} {'的声量' if zh else 'of tier'} · "
                f"{d.n_docs} {'篇' if zh else 'docs'} / {d.n_speakers} "
                f"{'个发言者' if zh else 'speakers'}"
                + (f" · {'组内均值' if zh else 'mean'} {d.probability:.0%}"
                   if d.probability is not None else "")
                + ")[/dim]"
            )
            console.print(f"      → [bold]{d.verdict}[/bold]")
            for r in d.rationale:
                console.print(f"        · {r}")
            if d.caveat:
                console.print(f"        [dim]{'反转条件' if zh else 'would flip on'}: "
                              f"{d.caveat}[/dim]")
            for q in d.quotes[:2]:
                console.print(f"        [dim]{q.speaker} ({q.channel}): "
                              f"{q.text[:110].replace(chr(10), ' ')}[/dim]")
        if v.unassigned_share >= 0.15:
            console.print(f"    [dim]{'表了态但无代表' if zh else 'took a side, no bloc'}: "
                          f"{v.unassigned_share:.0%}[/dim]")
        if v.undecided_share >= 0.15:
            console.print(f"    [dim]{'未表态' if zh else 'no side taken'}: "
                          f"{v.undecided_share:.0%}[/dim]")
        console.print()

    if snap.notes:
        console.print(Panel("\n".join(f"· {n}" for n in snap.notes),
                            title="解读" if zh else "Reading",
                            border_style="dim"))

    if alerts:
        colour = {"critical": "red", "warn": "yellow", "info": "blue"}
        body = "\n".join(
            f"[{colour[a.severity]}]●[/{colour[a.severity]}] {a.title}"
            + (f"\n   [dim]{a.detail[:160]}[/dim]" if a.detail else "")
            for a in alerts
        )
        console.print(Panel(body, title="信号" if zh else "Signals",
                            border_style="red"))
