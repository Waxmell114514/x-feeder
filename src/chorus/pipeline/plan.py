"""Stage 1 - decide what to search for.

You give this system a question. It has to turn that into the handful of
strings that will actually be typed into Reddit, Hacker News and Google
News, and that step decides more about the answer than anything downstream:
a search that misses half the argument produces a confident report about
the half it found.

So the planner is built out of two halves that play to opposite strengths.

  **Code proposes.** Candidate terms come from the issue file, from the
  words of the question itself, and - on a second pass - from the documents
  a first probe actually returned. Nothing is invented: Jev writes no text,
  and neither does this module.

  **Jev disposes.** Every candidate gets two judgements: would documents
  matching this term be about the question, and is the term too broad, too
  narrow, or well scoped. All of them are asked in one request, which is
  what makes judging thirty candidates cost a fraction of a cent.

One rule is worth stating on its own, because getting it wrong silently
poisons everything:

    **Search terms are never taken from one side of the axis alone.**

If you search "rate hike" and not "on hold", you will find a crowd that
believes in rate hikes. Stance vocabulary enters the plan only in balanced
sets - the same number of surviving terms for every side - or not at all.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Iterable, Optional

from .. import textutil
from ..jev import questions as Q
from ..models import PlannedQuery, SearchPlan, TermJudgement

# Jev is asked about this many candidates in one request. The limit is about
# keeping the state small enough to stay accurate, not about the API.
BATCH = 20
MAX_TERMS_PER_QUERY = 6

_QUOTED = re.compile(r"[\"“”']([^\"“”']{3,60})[\"“”']")
_ACRONYM = re.compile(r"\b([A-Z]{2,6})\b")
_PROPER = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b")


# ======================================================================
# Candidates
# ======================================================================
def candidate_terms(issue, corpus: Optional[list[str]] = None,
                    limit_corpus: int = 14) -> list[tuple[str, str]]:
    """-> [(term, origin)], deduplicated, in the order they should be judged.

    `origin` matters later: terms that came from one stance's vocabulary are
    balanced against the other stances before the plan is built.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(term: str, origin: str) -> None:
        term = " ".join(str(term).split()).strip(" ,.;:!?\"'")
        key = term.lower()
        if len(term) < 3 or key in seen:
            return
        # "Will hiring fall in 2027?" starts with a capital letter that is
        # not a name. A one-word function word is never a search term.
        if " " not in term and key in textutil.STOPWORDS:
            return
        seen.add(key)
        out.append((term, origin))

    for term in issue.entities:
        add(term, "issue")
    for term in issue.terms:
        add(term, "issue")

    prose = f"{issue.question} {issue.background}"
    for m in _QUOTED.finditer(prose):
        add(m.group(1), "question")
    for m in _ACRONYM.finditer(prose):
        add(m.group(1), "question")
    for m in _PROPER.finditer(prose):
        add(m.group(1), "question")

    for option in issue.axis:
        if option.anchor is None:          # "unclear" is not a thing to search for
            continue
        for keyword in option.keywords[:6]:
            add(keyword, f"stance:{option.id}")

    if corpus:
        ignore = {t.lower() for t, _ in out}
        for phrase, _ in textutil.mine_phrases(
            corpus, min_docs=2, max_words=3, limit=limit_corpus * 2, ignore=ignore
        )[:limit_corpus]:
            add(phrase, "corpus")

    return out


# ======================================================================
# Judgement
# ======================================================================
def judge_terms(jev, issue, candidates: list[tuple[str, str]],
                log=print) -> list[TermJudgement]:
    if jev.offline:
        return _unjudged(candidates)

    floor = jev.cfg.jev.term_on_topic_floor
    out: list[TermJudgement] = []

    for start in range(0, len(candidates), BATCH):
        chunk = candidates[start:start + BATCH]
        terms = [t for t, _ in chunk]
        response = jev.ask(Q.term_state(issue, terms), Q.term_questions(terms))
        for i, (term, origin) in enumerate(chunk):
            on_topic = response.noul(f"t{i}_on_topic")
            breadth, _, breadth_conf = response.choice(f"t{i}_breadth")
            kept = on_topic >= floor and breadth == "well_scoped"
            out.append(TermJudgement(
                term=term,
                on_topic=round(on_topic, 3),
                breadth=breadth or "well_scoped",
                breadth_confidence=round(breadth_conf, 3),
                kept=kept,
                reason=_reason(on_topic, breadth, floor),
                origin="corpus" if origin == "corpus" else "issue",
            ))
    return out


def _unjudged(candidates: list[tuple[str, str]],
              corpus_allowance: int = 6) -> list[TermJudgement]:
    """What the plan looks like with no model to judge it.

    There is no honest way to guess whether a term is well scoped without
    something that understands the question, so the stand-in does not try.
    It keeps what a human wrote in the issue file, keeps the most-used terms
    mined from the corpus, and says so in every row. A demo plan is a
    plausible plan, not a judged one.
    """
    out: list[TermJudgement] = []
    corpus_kept = 0
    for term, origin in candidates:
        from_corpus = origin == "corpus"
        if from_corpus:
            corpus_kept += 1
            kept = corpus_kept <= corpus_allowance
            reason = ("stand-in: kept as one of the most-used phrases"
                      if kept else "stand-in: less used than the ones kept")
        else:
            kept = True
            reason = "stand-in: kept because the issue file proposed it"
        out.append(TermJudgement(
            term=term, on_topic=0.5, breadth="well_scoped", breadth_confidence=0.0,
            kept=kept, reason=reason,
            origin="corpus" if from_corpus else "issue",
        ))
    return out


def _reason(on_topic: float, breadth: str, floor: float) -> str:
    if on_topic < floor:
        return f"off topic ({on_topic:.2f} < {floor:.2f})"
    if breadth == "too_broad":
        return "would mostly return unrelated documents"
    if breadth == "too_narrow":
        return "almost nothing would match it"
    return "kept"


def balance_stance_terms(judgements: list[TermJudgement],
                         origins: dict[str, str], issue) -> list[TermJudgement]:
    """Keep stance vocabulary only in equal measure across the sides.

    A term drawn from the `hike` keyword list is a fine search term; taking
    three of those and none from `hold` is not a search, it is a thesis. So
    each anchored stance contributes the same number of terms - the smallest
    number any of them managed - and the rest are dropped with that recorded
    as the reason.
    """
    stance_ids = [o.id for o in issue.axis if o.anchor is not None]
    if len(stance_ids) < 2:
        return judgements

    kept_by_stance: dict[str, list[TermJudgement]] = defaultdict(list)
    for j in judgements:
        origin = origins.get(j.term.lower(), "")
        if j.kept and origin.startswith("stance:"):
            kept_by_stance[origin.split(":", 1)[1]].append(j)

    if not kept_by_stance:
        return judgements

    for ids in kept_by_stance.values():
        ids.sort(key=lambda j: -j.on_topic)
    allowance = min(len(kept_by_stance.get(s, [])) for s in stance_ids)
    empty = [s for s in stance_ids if not kept_by_stance.get(s)]

    for stance, items in kept_by_stance.items():
        for j in items[allowance:]:
            j.kept = False
            j.reason = (
                "dropped to keep the sides balanced: "
                f"only {allowance} term(s) survived for every side"
                if allowance else
                "dropped to keep the sides balanced: "
                f"{', '.join(empty)} has no usable search term, so no side "
                f"contributes vocabulary"
            )
    return judgements


# ======================================================================
# Queries
# ======================================================================
def build_plan(cfg, issue, jev, *, corpus: Optional[list[str]] = None,
               log=print) -> SearchPlan:
    candidates = candidate_terms(issue, corpus)
    origins = {term.lower(): origin for term, origin in candidates}
    log(f"  {len(candidates)} candidate terms "
        f"({sum(1 for _, o in candidates if o == 'corpus')} mined from documents)")

    judgements = judge_terms(jev, issue, candidates, log=log)
    before = {j.term for j in judgements if j.kept}
    judgements = balance_stance_terms(judgements, origins, issue)
    dropped = before - {j.term for j in judgements if j.kept}
    if dropped:
        # This is a coverage decision the operator should get to argue with,
        # so it is said out loud rather than left in the table.
        log(f"  ! {len(dropped)} stance term(s) dropped to keep the sides "
            f"balanced; the plan searches neutral vocabulary only")
        log(f"    fix by giving the thin side better `keywords`, or by "
            f"removing a side nobody is actually arguing")

    kept = [j for j in judgements if j.kept]
    kept.sort(key=lambda j: -j.on_topic)
    terms = [j.term for j in kept]
    if not terms:
        # Never leave the issue unsearchable: fall back to what the human
        # wrote, and say so, rather than silently fetching nothing.
        terms = (issue.entities + issue.terms)[:MAX_TERMS_PER_QUERY]
        log("  ! no term passed judgement; falling back to the issue's own terms")

    queries = build_queries(cfg, issue, terms)
    log(f"  {len(kept)}/{len(judgements)} terms kept -> {len(queries)} queries")
    return SearchPlan(
        issue_id=issue.id,
        built_at=dt.datetime.now(dt.timezone.utc),
        terms=judgements,
        queries=queries,
        note="stub judgement (no Jev key)" if jev.offline else "",
    )


def build_queries(cfg, issue, terms: list[str]) -> list[PlannedQuery]:
    top = terms[:MAX_TERMS_PER_QUERY]
    out: list[PlannedQuery] = []
    src = issue.sources

    if src.reddit.enabled and top:
        opts = {
            "sort": src.reddit.sort,
            "time": _reddit_window(issue.window_hours, src.reddit.time),
            "include_comments": src.reddit.include_comments,
        }
        for tier, subs in src.reddit.subreddits.items():
            for sub in subs:
                out.append(PlannedQuery(
                    source="reddit", tier=tier, query=_or_query(top),
                    channel=sub, terms=top, max_results=src.reddit.max_results,
                    tag=_tag(issue, "reddit", sub), options=dict(opts),
                    note=f"r/{sub}, tier pinned by the issue file",
                ))
        if src.reddit.search_all:
            out.append(PlannedQuery(
                source="reddit", tier="", query=_or_query(top), terms=top,
                max_results=src.reddit.max_results,
                tag=_tag(issue, "reddit", "all"), options=dict(opts),
                note="site-wide search; tier decided per subreddit",
            ))

    if src.hackernews.enabled and top:
        # Algolia has no OR: each term is its own cheap request.
        for term in top[:3]:
            out.append(PlannedQuery(
                source="hackernews", tier=src.hackernews.tier, query=term,
                terms=[term], max_results=src.hackernews.max_results,
                tag=_tag(issue, "hn", term),
                options={"tags": "story"},
            ))
        if src.hackernews.include_comments and top:
            out.append(PlannedQuery(
                source="hackernews", tier=src.hackernews.tier, query=top[0],
                terms=[top[0]], max_results=src.hackernews.max_results,
                tag=_tag(issue, "hn", f"{top[0]}-comments"),
                options={"tags": "comment"},
            ))

    if src.v2ex.enabled and top:
        # sov2ex is a single-field full-text search: OR syntax does nothing,
        # and several words narrow rather than widen. One query per term.
        for term in top[:3]:
            out.append(PlannedQuery(
                source="v2ex", tier=src.v2ex.tier, query=term, terms=[term],
                max_results=src.v2ex.max_results,
                tag=_tag(issue, "v2ex", term),
                options={"node": src.v2ex.node} if src.v2ex.node else {},
                note="topics only; the argument in the replies is not indexed",
            ))

    if src.gnews.enabled and top:
        days = max(1, min(30, round(issue.window_hours / 24)))
        out.append(PlannedQuery(
            source="gnews", tier=src.gnews.default_tier,
            query=f"{_or_query(top)} when:{days}d", terms=top,
            max_results=src.gnews.max_results, tag=_tag(issue, "gnews", "search"),
            options={"hl": src.gnews.hl, "gl": src.gnews.gl, "ceid": src.gnews.ceid},
            note="headlines only; bodies are not in the feed",
        ))

    for feed in src.feeds:
        out.append(PlannedQuery(
            source="rss", tier=feed.tier, query="", channel=feed.url,
            terms=top, max_results=feed.max_results,
            tag=_tag(issue, "rss", feed.channel or feed.url),
            options={"url": feed.url, "channel": feed.channel,
                     "match_terms": feed.match_terms},
            note="whole feed, filtered locally",
        ))
    return out


def _or_query(terms: Iterable[str]) -> str:
    parts = []
    for term in terms:
        term = term.strip()
        parts.append(f'"{term}"' if " " in term else term)
    return "(" + " OR ".join(parts) + ")" if len(parts) > 1 else "".join(parts)


def _reddit_window(window_hours: int, configured: str) -> str:
    if configured and configured != "auto":
        return configured
    if window_hours <= 24:
        return "day"
    if window_hours <= 24 * 7:
        return "week"
    return "month"


def _tag(issue, source: str, slug: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(slug).lower()).strip("-")[:32]
    return f"{issue.id}:{source}:{slug or 'all'}"
