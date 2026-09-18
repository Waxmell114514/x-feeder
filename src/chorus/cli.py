"""chorus command line.

    chorus demo                      end to end on bundled data, no keys
    chorus new --id ai-jobs --question "..."   scaffold an issue
    chorus plan   --issue ai-jobs    decide what to search for, and show it
    chorus run    --issue ai-jobs    one full cycle: plan -> ... -> report
    chorus panel  --issue ai-jobs    mine or show the opinion leaders
    chorus report --issue ai-jobs    re-render the latest snapshot
    chorus watch  --issue ai-jobs    loop, and alert on change

Every stage is separately runnable, and every stage is idempotent, so a
failure costs one stage rather than a run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import shutil
import sys
import time

import yaml
from rich.console import Console

from . import __version__, phrasing
from .config import Config, load_config
from .jev import JevClient
from .models import Snapshot
from .pipeline import alerts as alerts_mod
from .pipeline import plan as plan_mod
from .pipeline import panel as panel_mod
from .pipeline.assign import run_assign
from .pipeline.ingest import run_ingest, window_documents
from .pipeline.read import run_read
from .pipeline.synthesize import run_synthesize
from .pipeline.tier import run_tier
from .render import html as html_render
from .render import terminal as term_render
from .store import Store

console = Console()
PKG_ROOT = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PKG_ROOT.parent.parent


def _asset(*parts: str) -> pathlib.Path:
    """Find a bundled file (config templates, fixtures).

    Works from a source checkout and an editable install; a plain wheel
    install has no source tree, so fall back to the working directory and
    say clearly what is missing rather than half-running.
    """
    rel = pathlib.Path(*parts)
    for root in (REPO_ROOT, pathlib.Path.cwd()):
        candidate = root / rel
        if candidate.exists():
            return candidate
    return REPO_ROOT / rel


def log(msg: str = "") -> None:
    console.print(msg, highlight=False)


# ----------------------------------------------------------------------
def _load(args) -> Config:
    path = pathlib.Path(args.config)
    if not path.exists():
        console.print(f"[red]config not found:[/red] {path}\n"
                      f"Run [bold]chorus init[/bold] to scaffold one.")
        raise SystemExit(2)
    cfg = load_config(path)
    if getattr(args, "offline", False):
        cfg.jev.offline = True
    return cfg


def _jev(cfg) -> JevClient:
    client = JevClient(cfg)
    if client.offline:
        console.print(f"[yellow]offline: {client.why_offline()}. "
                      f"Judgements come from the keyword stand-in; the "
                      f"statistics are still real.[/yellow]")
    return client


def _resolve_issue(cfg: Config, requested: str | None) -> str:
    if requested:
        cfg.issue(requested)
        return requested
    if len(cfg.issues) == 1:
        return next(iter(cfg.issues))
    console.print("[red]--issue is required[/red]; loaded: "
                  + ", ".join(sorted(cfg.issues)))
    raise SystemExit(2)


def _plan_for(cfg, store: Store, issue, jev, *, rebuild: bool = False,
              expand: bool = False) -> "plan_mod.SearchPlan":
    """The stored plan, or a fresh one. Planning is cheap but not free, and
    a plan that changes every run makes two snapshots incomparable."""
    stored = store.get_plan(issue.id)
    if stored and stored.queries and not rebuild and not expand:
        return stored

    corpus = None
    if expand:
        # Second pass: let the documents already collected suggest terms the
        # issue file never thought of.
        documents = window_documents(cfg, store, issue, stored)
        corpus = [d.body for d in documents[:400]]
        log(f"  expanding from {len(corpus)} collected documents")
    fresh = plan_mod.build_plan(cfg, issue, jev, corpus=corpus, log=log)
    store.save_plan(fresh)
    return fresh


# ----------------------------------------------------------------------
def cmd_init(args) -> int:
    target = pathlib.Path(args.dir)
    src = _asset("config")
    if not src.exists():
        console.print(f"[red]bundled config templates not found at {src}[/red]")
        return 1
    target.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src)
        dest = target / rel
        if f.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest = dest.with_name(dest.name.replace(".example", ""))
        if dest.exists() and not args.force:
            log(f"  skip (exists) {dest}")
            continue
        shutil.copyfile(f, dest)
        log(f"  wrote {dest}")
    log("\nNext:")
    log("  1. export TYPESAFE_API_KEY=...        (https://console.typesafe.ai)")
    log("  2. edit config/config.yaml: the channel allowlists decide the tiers")
    log("  3. chorus plan --issue <id>           see what it will search for")
    log("  4. chorus run  --issue <id> --html")
    return 0


def cmd_new(args) -> int:
    """Scaffold an issue file from a yes/no question."""
    issue_id = args.id
    target = pathlib.Path(args.dir) / "issues" / f"{issue_id}.yaml"
    if target.exists() and not args.force:
        console.print(f"[red]{target} already exists[/red] (use --force)")
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)

    doc = {
        "issues": [{
            "id": issue_id,
            "title": args.title or args.question[:70],
            "title_zh": args.title_zh or "",
            "question": args.question,
            "background": args.background or "",
            "window_hours": args.hours,
            "half_life_hours": args.hours / 2,
            "output_lang": args.lang,
            "axis": [
                {"id": "yes", "label": "yes", "label_zh": "会", "anchor": 0.9,
                 "description": "The text argues that the answer is yes.",
                 "keywords": []},
                {"id": "no", "label": "no", "label_zh": "不会", "anchor": 0.1,
                 "description": "The text argues that the answer is no.",
                 "keywords": []},
                {"id": "unclear", "label": "no clear position",
                 "label_zh": "态度不明"},
            ],
            "terms": args.term or [],
            "entities": args.entity or [],
            "sources": {
                "reddit": {"enabled": True, "search_all": True,
                           "subreddits": {"crowd": [], "expert": []}},
                "gnews": {"enabled": True},
                "hackernews": {"enabled": False},
                "feeds": [],
            },
        }]
    }
    target.write_text(
        "# Written by `chorus new`. Two things are worth your attention before\n"
        "# the first run:\n"
        "#   * `axis` - the answers a document can take. Edit the labels and\n"
        "#     the anchors; the anchor is where that side sits on the 0-1 axis.\n"
        "#   * `sources` - where to look. A named subreddit under a tier both\n"
        "#     searches it and pins what it returns to that tier.\n"
        + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=88),
        encoding="utf-8",
    )
    log(f"  wrote {target}")
    log(f"\nNext: [bold]chorus plan --issue {issue_id}[/bold] to see what it "
        f"would search for.")
    return 0


def cmd_issues(args) -> int:
    cfg = _load(args)
    for iid, issue in sorted(cfg.issues.items()):
        log(f"[bold]{iid}[/bold]  {issue.title_zh or issue.title}")
        log(f"    {issue.question.strip()[:120]}")
        log(f"    stances: {', '.join(issue.stance_ids())}  "
            f"window {issue.window_hours}h  "
            f"panel: {len(issue.panel) or 'mined'}")
    return 0


def cmd_stats(args) -> int:
    cfg = _load(args)
    with Store(cfg.db_path) as store:
        for k, v in store.stats().items():
            log(f"  {k:>12}: {v}")
    return 0


def cmd_plan(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    issue = cfg.issue(issue_id)
    jev = _jev(cfg)
    with Store(cfg.db_path) as store:
        plan = _plan_for(cfg, store, issue, jev, rebuild=args.rebuild,
                         expand=args.expand)
        log(f"\n[bold]{issue.title_zh or issue.title}[/bold]")
        log(f"[dim]{issue.question.strip()}[/dim]\n")
        log("[bold]terms[/bold]")
        for t in sorted(plan.terms, key=lambda x: (not x.kept, -x.on_topic)):
            mark = "[green]✓[/green]" if t.kept else "[dim]✗[/dim]"
            log(f"  {mark} {t.term:<34} on-topic {t.on_topic:.2f}  "
                f"{t.breadth:<12} [dim]{t.reason}[/dim]")
        log("\n[bold]queries[/bold]")
        for q in plan.queries:
            where = q.channel or "(site-wide)"
            log(f"  [{q.source:11}] {q.tier or '—':10} {where}")
            log(f"      {q.query or '(whole feed)'}")
            if q.note:
                log(f"      [dim]{q.note}[/dim]")
        _cost(jev)
    return 0


def cmd_panel(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    issue = cfg.issue(issue_id)
    jev = _jev(cfg)
    with Store(cfg.db_path) as store:
        plan = store.get_plan(issue_id)
        documents = window_documents(cfg, store, issue, plan, log=log)
        readings = store.get_readings(issue_id)
        panel = panel_mod.load_or_build(cfg, store, issue, documents, readings,
                                        jev, rebuild=args.rebuild, log=log)
        log(f"\n[bold]{len(panel.leaders)} leaders[/bold] "
            f"({panel.origin})\n")
        for leader in panel.leaders:
            log(f"  [bold]{leader.label_zh or leader.label}[/bold]  "
                f"[dim]stance={leader.stance}[/dim]")
            log(f"      {leader.description}")
            for line in leader.evidence:
                log(f"      [dim]{line}[/dim]")
        if panel.origin == "mined" and panel.leaders:
            snippet = _panel_yaml(panel)
            log("\n[dim]Paste this into the issue file to freeze the panel "
                "(snapshots are only comparable across runs once you do):[/dim]\n")
            console.print(snippet, highlight=False)
            if args.write:
                out = pathlib.Path(cfg.output_dir) / f"{issue_id}-panel.yaml"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(snippet, encoding="utf-8")
                log(f"\n[green]written:[/green] {out}")
        _cost(jev)
    return 0


def _panel_yaml(panel) -> str:
    rows = [{
        "id": leader.id,
        "label": leader.label,
        "label_zh": leader.label_zh,
        "stance": leader.stance,
        "description": leader.description,
        "flip_on": "",
    } for leader in panel.leaders]
    return yaml.safe_dump({"panel": rows}, allow_unicode=True, sort_keys=False,
                          width=88)


# ----------------------------------------------------------------------
def _pipeline(cfg: Config, store: Store, issue_id: str, *, do_ingest: bool = True,
              force_read: bool = False, rebuild_panel: bool = False,
              expand_plan: bool = False):
    issue = cfg.issue(issue_id)
    jev = _jev(cfg)

    log("[bold]1/6 plan[/bold]")
    plan = _plan_for(cfg, store, issue, jev, expand=expand_plan)
    log(f"  {len(plan.kept_terms())} terms, {len(plan.queries)} queries "
        f"[dim](chorus plan --issue {issue_id} to inspect)[/dim]")

    if do_ingest:
        log("\n[bold]2/6 ingest[/bold]")
        run_ingest(cfg, store, issue, plan, log=log)

    documents = window_documents(cfg, store, issue, plan, log=log)

    log(f"\n[bold]3/6 tier[/bold]  ({len(documents)} documents in window)")
    info = run_tier(cfg, store, documents, jev, log=log)
    log("  " + ", ".join(f"{k}={v}" for k, v in sorted(info["counts"].items()))
        + f"   [dim]{info['asked']} venue(s) judged by Jev[/dim]")

    log("\n[bold]4/6 read[/bold]")
    run_read(cfg, store, issue, documents, jev, log=log, force=force_read)
    readings = store.get_readings(issue_id)

    log("\n[bold]5/6 panel + assign[/bold]")
    panel = panel_mod.load_or_build(cfg, store, issue, documents, readings, jev,
                                    rebuild=rebuild_panel, log=log)
    run_assign(cfg, store, issue, documents, readings, panel, jev, log=log)
    assignments = store.get_assignments(issue_id)

    log("\n[bold]6/6 synthesize[/bold]")
    snap = run_synthesize(cfg, store, issue, documents, readings, panel,
                          assignments, log=log)
    if jev.offline:
        snap.notes.append(phrasing.stub_note(issue.output_lang or cfg.output_lang))
    store.add_snapshot(snap)

    history = store.latest_snapshots(issue_id, limit=2)
    previous = history[1] if len(history) > 1 else None
    found = alerts_mod.compute_alerts(cfg, issue, snap, previous,
                                      lang=issue.output_lang or cfg.output_lang)
    store.add_alerts(found)
    sent = alerts_mod.dispatch(cfg, found, issue.title_zh or issue.title, log=log)
    log(f"  {len(found)} signal(s), {sent} dispatched")

    _cost(jev)
    return snap, found


def _cost(jev) -> None:
    u = jev.usage
    if jev.offline:
        log(f"\n[dim]jev: offline stub, {u['questions']} questions answered "
            f"locally, $0.00[/dim]")
        return
    log(f"\n[dim]jev: {u['requests']} requests / {u['questions']} questions, "
        f"{u['cached']} served from disk cache, "
        f"{u['input_tokens']:,} input tokens, ~${jev.cost_estimate():.4f}[/dim]")


def _emit(cfg, store, issue_id, snap, args, found=None) -> None:
    issue = cfg.issue(issue_id)
    lang = issue.output_lang or cfg.output_lang
    if found is None:
        # Re-rendering an existing snapshot: keep only the alerts raised for it.
        found = [a for a in store.recent_alerts(issue_id, limit=60)
                 if abs((a.ts - snap.ts).total_seconds()) < 1]
    log()
    term_render.render(snap, issue, alerts=found, lang=lang, console=console)

    if getattr(args, "html", False) or getattr(args, "html_path", None):
        out = getattr(args, "html_path", None) or \
            str(pathlib.Path(cfg.output_dir) / f"{issue_id}.html")
        path = html_render.render(
            snap, issue, alerts=found, lang=lang,
            history=store.snapshot_series(issue_id, limit=60), out_path=out)
        log(f"\n[green]HTML report:[/green] {path.resolve()}")


def cmd_run(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    with Store(cfg.db_path) as store:
        snap, found = _pipeline(cfg, store, issue_id, do_ingest=not args.no_ingest,
                                force_read=args.force_read,
                                rebuild_panel=args.rebuild_panel,
                                expand_plan=args.expand_plan)
        _emit(cfg, store, issue_id, snap, args, found)
    return 0


def cmd_ingest(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    issue = cfg.issue(issue_id)
    jev = _jev(cfg)
    with Store(cfg.db_path) as store:
        plan = _plan_for(cfg, store, issue, jev)
        since = None
        if args.hours:
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.hours)
        info = run_ingest(cfg, store, issue, plan, since=since, log=log)
    log(f"\n{info['fetched']} fetched, {info['new']} new  "
        + ", ".join(f"{k}={v}" for k, v in sorted(info["per_source"].items())))
    return 0


def cmd_tier(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    issue = cfg.issue(issue_id)
    with Store(cfg.db_path) as store:
        documents = window_documents(cfg, store, issue, store.get_plan(issue_id))
        info = run_tier(cfg, store, documents, _jev(cfg), log=log)
        for channel, row in sorted(store.get_channel_tiers().items()):
            log(f"  {row.tier:10} {channel:34} [dim]{row.method}: {row.reason}[/dim]")
    log(f"\n{info['new']} newly tiered across {info['channels']} venues")
    return 0


def cmd_read(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    issue = cfg.issue(issue_id)
    jev = _jev(cfg)
    with Store(cfg.db_path) as store:
        documents = window_documents(cfg, store, issue, store.get_plan(issue_id))
        run_read(cfg, store, issue, documents, jev, log=log, force=args.force)
        _cost(jev)
    return 0


def cmd_assign(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    issue = cfg.issue(issue_id)
    jev = _jev(cfg)
    with Store(cfg.db_path) as store:
        documents = window_documents(cfg, store, issue, store.get_plan(issue_id))
        readings = store.get_readings(issue_id)
        panel = panel_mod.load_or_build(cfg, store, issue, documents, readings,
                                        jev, log=log)
        run_assign(cfg, store, issue, documents, readings, panel, jev, log=log,
                   force=args.force)
        _cost(jev)
    return 0


def cmd_report(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    with Store(cfg.db_path) as store:
        snaps = store.latest_snapshots(issue_id, limit=1)
        if not snaps:
            console.print("[red]no snapshot yet[/red]; run "
                          f"[bold]chorus run --issue {issue_id}[/bold] first")
            return 2
        _emit(cfg, store, issue_id, snaps[0], args)
    return 0


def cmd_watch(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    interval = args.interval
    log(f"[bold]watching[/bold] {issue_id} every {interval}s — ctrl-c to stop\n")
    n = 0
    while True:
        n += 1
        log(f"[dim]{'─' * 60}\ncycle {n} · {dt.datetime.now():%H:%M:%S}[/dim]")
        try:
            with Store(cfg.db_path) as store:
                snap, found = _pipeline(cfg, store, issue_id)
                _emit(cfg, store, issue_id, snap, args, found)
        except KeyboardInterrupt:
            return 0
        except Exception as e:                                    # noqa: BLE001
            console.print(f"[red]cycle failed:[/red] {e}")
        if args.once:
            return 0
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return 0


def cmd_demo(args) -> int:
    """End to end on the bundled fixture. No API key, no network."""
    cfg_path = _asset("config", "demo.yaml")
    if not cfg_path.exists():
        console.print(f"[red]demo config missing at {cfg_path}[/red]")
        return 1
    cfg = load_config(cfg_path)
    cfg.jev.offline = not args.live
    fixture = pathlib.Path(cfg.source.fixture_path)
    if not fixture.is_absolute():
        cfg.source.fixture_path = str(_asset(*fixture.parts))
    workdir = pathlib.Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    cfg.db_path = str(workdir / "demo.db")
    cfg.jev.cache_dir = str(workdir / "jev-cache")
    cfg.output_dir = str(workdir)
    if args.fresh and pathlib.Path(cfg.db_path).exists():
        pathlib.Path(cfg.db_path).unlink()

    issue_id = next(iter(cfg.issues))
    log(f"[bold cyan]chorus demo[/bold cyan] · issue [bold]{issue_id}[/bold] · "
        f"{'live Jev' if args.live else 'offline'}\n")
    with Store(cfg.db_path) as store:
        snap, found = _pipeline(cfg, store, issue_id,
                                rebuild_panel=args.fresh)
        args.html_path = str(workdir / f"{issue_id}.html")
        _emit(cfg, store, issue_id, snap, args, found)
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chorus",
        description="Turn public opinion on one question into a handful of "
                    "virtual opinion leaders.",
    )
    p.add_argument("--version", action="version", version=f"chorus {__version__}")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--offline", action="store_true",
                   help="never call Jev; keyword stand-in only")

    # The same two flags are accepted after the subcommand as well, because
    # that is where people naturally type them. SUPPRESS keeps an unused
    # subparser copy from clobbering a value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS)
    common.add_argument("--offline", action="store_true", default=argparse.SUPPRESS)

    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_, parents=[common])
        s.set_defaults(func=fn)
        return s

    s = add("init", cmd_init, "scaffold config files")
    s.add_argument("--dir", default="config")
    s.add_argument("--force", action="store_true")

    s = add("new", cmd_new, "scaffold an issue from a yes/no question")
    s.add_argument("--id", required=True)
    s.add_argument("--question", required=True)
    s.add_argument("--title")
    s.add_argument("--title-zh", dest="title_zh")
    s.add_argument("--background", default="")
    s.add_argument("--term", action="append", help="a seed search term (repeatable)")
    s.add_argument("--entity", action="append", help="a name that anchors the search")
    s.add_argument("--hours", type=int, default=48)
    s.add_argument("--lang", default="zh")
    s.add_argument("--dir", default="config")
    s.add_argument("--force", action="store_true")

    add("issues", cmd_issues, "list configured issues")
    add("stats", cmd_stats, "database counters")

    s = add("plan", cmd_plan, "decide and show which keywords to search for")
    s.add_argument("--issue")
    s.add_argument("--rebuild", action="store_true", help="re-judge every term")
    s.add_argument("--expand", action="store_true",
                   help="also mine terms from documents already collected")

    s = add("panel", cmd_panel, "show, mine or freeze the opinion leaders")
    s.add_argument("--issue")
    s.add_argument("--rebuild", action="store_true", help="mine the panel again")
    s.add_argument("--write", action="store_true",
                   help="write the panel snippet to the output directory")

    for name, fn, helptext in [
        ("run", cmd_run, "full cycle: plan, ingest, tier, read, assign, report"),
        ("report", cmd_report, "re-render the most recent snapshot"),
    ]:
        s = add(name, fn, helptext)
        s.add_argument("--issue")
        s.add_argument("--html", action="store_true", help="also write an HTML report")
        s.add_argument("--html-path", dest="html_path")
        if name == "run":
            s.add_argument("--no-ingest", action="store_true",
                           help="use what is already stored")
            s.add_argument("--force-read", action="store_true")
            s.add_argument("--rebuild-panel", action="store_true")
            s.add_argument("--expand-plan", action="store_true")

    s = add("ingest", cmd_ingest, "collect documents only")
    s.add_argument("--issue")
    s.add_argument("--hours", type=int, help="override the issue's window")

    s = add("tier", cmd_tier, "assign venues to source tiers")
    s.add_argument("--issue")

    s = add("read", cmd_read, "read stored documents into positions")
    s.add_argument("--issue")
    s.add_argument("--force", action="store_true", help="re-read everything")

    s = add("assign", cmd_assign, "sort documents to opinion leaders")
    s.add_argument("--issue")
    s.add_argument("--force", action="store_true")

    s = add("watch", cmd_watch, "run on a loop and alert on change")
    s.add_argument("--issue")
    s.add_argument("--interval", type=int, default=1800)
    s.add_argument("--once", action="store_true")
    s.add_argument("--html", action="store_true")
    s.add_argument("--html-path", dest="html_path")

    s = add("demo", cmd_demo, "end-to-end on bundled data, no keys needed")
    s.add_argument("--workdir", default=".chorus/demo")
    s.add_argument("--live", action="store_true", help="use the real Jev API")
    s.add_argument("--fresh", action="store_true", help="drop the demo database first")
    s.add_argument("--html", action="store_true", default=True)
    s.add_argument("--html-path", dest="html_path")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
