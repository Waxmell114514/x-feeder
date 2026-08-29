"""xfeeder command line.

    xfeeder demo                     end to end on bundled fixtures, no keys
    xfeeder run     --issue fed-rate one full cycle: ingest -> report
    xfeeder ingest  --issue fed-rate collect only
    xfeeder extract --issue fed-rate re-read stored posts
    xfeeder report  --issue fed-rate re-render the latest snapshot
    xfeeder watch   --issue fed-rate loop forever, alert on change

Every stage is separately runnable so a failure costs one stage, not a run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import shutil
import sys
import time

from rich.console import Console

from . import __version__
from .config import Config, load_config
from .llm import LLMClient
from .models import Snapshot
from .pipeline import alerts as alerts_mod
from .pipeline.cohort import run_classify
from .pipeline.extract import run_extract
from .pipeline.ingest import run_ingest, window_posts
from .pipeline.synthesize import run_synthesize
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
                      f"Run [bold]xfeeder init[/bold] to scaffold one.")
        raise SystemExit(2)
    cfg = load_config(path)
    if getattr(args, "offline", False):
        cfg.llm.offline = True
    return cfg


def _llm(cfg) -> LLMClient | None:
    client = LLMClient(cfg)
    if cfg.llm.offline:
        console.print("[yellow]offline mode: statistics are real, prose is templated[/yellow]")
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


# ----------------------------------------------------------------------
def cmd_init(args) -> int:
    target = pathlib.Path(args.dir)
    src = _asset("config")
    if not src.exists():
        console.print(f"[red]bundled config templates not found at {src}[/red]")
        return 1
    target.mkdir(parents=True, exist_ok=True)
    for f in src.rglob("*"):
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
    log("  1. put your handles into config/config.yaml (the allowlists)")
    log("  2. export X_BEARER_TOKEN and ANTHROPIC_API_KEY")
    log("  3. xfeeder run --issue <id>")
    return 0


def cmd_issues(args) -> int:
    cfg = _load(args)
    for iid, issue in sorted(cfg.issues.items()):
        log(f"[bold]{iid}[/bold]  {issue.title_zh or issue.title}")
        log(f"    {issue.question}")
        log(f"    stances: {', '.join(issue.stance_ids())}  "
            f"window {issue.window_hours}h  queries {len(issue.queries)}")
    return 0


def cmd_stats(args) -> int:
    cfg = _load(args)
    with Store(cfg.db_path) as store:
        for k, v in store.stats().items():
            log(f"  {k:>12}: {v}")
    return 0


# ----------------------------------------------------------------------
def _pipeline(cfg: Config, store: Store, issue_id: str, *, do_ingest: bool = True,
              force_extract: bool = False) -> Snapshot:
    llm = _llm(cfg)
    issue = cfg.issue(issue_id)

    if do_ingest:
        log("[bold]1/5 ingest[/bold]")
        run_ingest(cfg, store, issue_id, log=log)

    posts = window_posts(cfg, store, issue_id, log=log)
    log(f"\n[bold]2/5 classify[/bold]  ({len(posts)} posts in window)")
    info = run_classify(cfg, store, posts, llm=llm, log=log)
    log("  " + ", ".join(f"{k}={v}" for k, v in sorted(info["counts"].items())))

    log("\n[bold]3/5 extract[/bold]")
    run_extract(cfg, store, issue_id, posts, llm=llm, log=log, force=force_extract)

    log("\n[bold]4/5 synthesize[/bold]")
    snap = run_synthesize(cfg, store, issue_id, posts, llm=llm, log=log)
    store.add_snapshot(snap)

    log("\n[bold]5/5 signals[/bold]")
    history = store.latest_snapshots(issue_id, limit=2)
    previous = history[1] if len(history) > 1 else None
    found = alerts_mod.compute_alerts(cfg, issue, snap, previous,
                                      lang=issue.output_lang or cfg.output_lang)
    store.add_alerts(found)
    sent = alerts_mod.dispatch(cfg, found, issue.title_zh or issue.title, log=log)
    log(f"  {len(found)} signal(s), {sent} dispatched")

    if llm and not cfg.llm.offline:
        u = llm.usage
        log(f"\n[dim]model: {u['calls']} calls, {u['cached']} served from disk cache, "
            f"~${llm.cost_estimate():.3f}[/dim]")
    return snap, found


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
                                force_extract=args.force_extract)
        _emit(cfg, store, issue_id, snap, args, found)
    return 0


def cmd_ingest(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    with Store(cfg.db_path) as store:
        since = None
        if args.hours:
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.hours)
        info = run_ingest(cfg, store, issue_id, since=since, log=log)
    log(f"\n{info['fetched']} fetched, {info['new']} new")
    return 0


def cmd_classify(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    with Store(cfg.db_path) as store:
        posts = window_posts(cfg, store, issue_id)
        info = run_classify(cfg, store, posts, llm=_llm(cfg), log=log)
    log(f"\n{info['new']} newly classified: "
        + ", ".join(f"{k}={v}" for k, v in sorted(info["counts"].items())))
    return 0


def cmd_extract(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    with Store(cfg.db_path) as store:
        posts = window_posts(cfg, store, issue_id)
        run_extract(cfg, store, issue_id, posts, llm=_llm(cfg), log=log,
                    force=args.force)
    return 0


def cmd_synthesize(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    with Store(cfg.db_path) as store:
        posts = window_posts(cfg, store, issue_id)
        snap = run_synthesize(cfg, store, issue_id, posts, llm=_llm(cfg), log=log)
        store.add_snapshot(snap)
        _emit(cfg, store, issue_id, snap, args)
    return 0


def cmd_report(args) -> int:
    cfg = _load(args)
    issue_id = _resolve_issue(cfg, args.issue)
    with Store(cfg.db_path) as store:
        snaps = store.latest_snapshots(issue_id, limit=1)
        if not snaps:
            console.print("[red]no snapshot yet[/red]; run "
                          f"[bold]xfeeder run --issue {issue_id}[/bold] first")
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
    """End to end on the bundled fixture. No API keys, no network."""
    cfg_path = _asset("config", "demo.yaml")
    if not cfg_path.exists():
        console.print(f"[red]demo config missing at {cfg_path}[/red]")
        return 1
    cfg = load_config(cfg_path)
    cfg.llm.offline = not args.live
    fixture = pathlib.Path(cfg.source.fixture_path)
    if not fixture.is_absolute():
        cfg.source.fixture_path = str(_asset(*fixture.parts))
    workdir = pathlib.Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    cfg.db_path = str(workdir / "demo.db")
    cfg.llm.cache_dir = str(workdir / "llm-cache")
    cfg.output_dir = str(workdir)
    if args.fresh and pathlib.Path(cfg.db_path).exists():
        pathlib.Path(cfg.db_path).unlink()

    issue_id = next(iter(cfg.issues))
    log(f"[bold cyan]xfeeder demo[/bold cyan] · issue [bold]{issue_id}[/bold] · "
        f"{'live models' if args.live else 'offline'}\n")
    with Store(cfg.db_path) as store:
        snap, found = _pipeline(cfg, store, issue_id)
        args.html_path = str(workdir / f"{issue_id}.html")
        _emit(cfg, store, issue_id, snap, args, found)
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xfeeder",
        description="Turn X/Twitter noise into a handful of synthetic opinion "
                    "leaders per issue.",
    )
    p.add_argument("--version", action="version", version=f"xfeeder {__version__}")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--offline", action="store_true",
                   help="never call a model; statistics only")

    # The same two flags are accepted after the subcommand as well, because
    # that is where people naturally type them. SUPPRESS keeps an unused
    # subparser copy from clobbering a value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS)
    common.add_argument("--offline", action="store_true",
                        default=argparse.SUPPRESS)

    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_, parents=[common])
        s.set_defaults(func=fn)
        return s

    s = add("init", cmd_init, "scaffold config files")
    s.add_argument("--dir", default="config")
    s.add_argument("--force", action="store_true")

    add("issues", cmd_issues, "list configured issues")
    add("stats", cmd_stats, "database counters")

    for name, fn, helptext in [
        ("run", cmd_run, "full cycle: ingest, classify, extract, synthesize, report"),
        ("synthesize", cmd_synthesize, "re-synthesize from stored extractions"),
        ("report", cmd_report, "re-render the most recent snapshot"),
    ]:
        s = add(name, fn, helptext)
        s.add_argument("--issue")
        s.add_argument("--html", action="store_true", help="also write an HTML report")
        s.add_argument("--html-path", dest="html_path")
        if name == "run":
            s.add_argument("--no-ingest", action="store_true",
                           help="use what is already stored")
            s.add_argument("--force-extract", action="store_true")

    s = add("ingest", cmd_ingest, "collect posts only")
    s.add_argument("--issue")
    s.add_argument("--hours", type=int, help="override the issue's window")

    s = add("classify", cmd_classify, "assign accounts to tiers")
    s.add_argument("--issue")

    s = add("extract", cmd_extract, "read stored posts into stances")
    s.add_argument("--issue")
    s.add_argument("--force", action="store_true", help="re-extract everything")

    s = add("watch", cmd_watch, "run on a loop and alert on change")
    s.add_argument("--issue")
    s.add_argument("--interval", type=int, default=1800)
    s.add_argument("--once", action="store_true")
    s.add_argument("--html", action="store_true")
    s.add_argument("--html-path", dest="html_path")

    s = add("demo", cmd_demo, "end-to-end on bundled fixture data, no keys needed")
    s.add_argument("--workdir", default=".xfeeder/demo")
    s.add_argument("--live", action="store_true", help="use real models")
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
