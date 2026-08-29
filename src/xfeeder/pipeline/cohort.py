"""Stage 2 - decide which tier each account belongs to.

Ordered by cost and reliability, cheapest first:

  1. explicit allowlist of handles (config/cohorts) - free, exact, and the
     only thing that should ever put an account in `official`;
  2. the cohort prior carried by the query that found the post - free, and
     strong for `from:` queries;
  3. bio keyword + follower/language heuristics - free;
  4. a model call for whatever is left - accurate but paid.

Accounts are classified once and cached; only new handles cost anything on
subsequent runs.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .. import textutil
from ..llm import LLMClient, LLMUnavailable
from ..llm import offline as offline_stub
from ..llm.prompts import AuthorClassBatch, CLASSIFY_SYSTEM, classify_user_prompt
from ..models import Author, CohortAssignment, Post
from ..store import Store


def _allowlist(cfg) -> dict[str, str]:
    table: dict[str, str] = {}
    for name, rule in cfg.cohorts.items():
        for handle in rule.handles:
            table[handle.lstrip("@").lower()] = name
    return table


def dominant_lang(posts: list[Post]) -> str:
    if not posts:
        return "und"
    counts = Counter(p.lang or textutil.detect_lang(p.text) for p in posts)
    return counts.most_common(1)[0][0]


def heuristic(author: Author, lang: str, cfg) -> tuple[str, float, str] | None:
    bio = (author.description or "").lower()

    for name in ("official", "pro_media"):
        rule = cfg.cohorts.get(name)
        if rule and any(k.lower() in bio for k in rule.keywords):
            hit = next(k for k in rule.keywords if k.lower() in bio)
            return name, 0.65, f"bio keyword {hit!r}"

    kol = "cn_kol" if lang == "zh" else "en_kol"
    rule = cfg.cohorts.get(kol)
    if rule and rule.min_followers and author.followers >= rule.min_followers:
        return kol, 0.6, f"{author.followers} followers, posts in {lang}"

    crowd = cfg.cohorts.get("crowd")
    if crowd and crowd.max_followers and author.followers <= crowd.max_followers:
        return "crowd", 0.6, f"{author.followers} followers"
    return None


def run_classify(cfg, store: Store, posts: list[Post], llm: LLMClient | None = None,
                 log=print) -> dict:
    authors = store.get_authors({p.author_id for p in posts})
    known = store.get_cohorts()
    allow = _allowlist(cfg)

    posts_by_author: dict[str, list[Post]] = defaultdict(list)
    for p in posts:
        posts_by_author[p.author_id].append(p)

    assignments: list[CohortAssignment] = []
    unresolved: list[tuple[Author, str, Post]] = []
    counts = Counter()

    for aid, author in authors.items():
        if aid in known and known[aid].method in ("allowlist", "manual", "llm"):
            counts[known[aid].cohort] += 1
            continue

        mine = posts_by_author.get(aid, [])
        lang = dominant_lang(mine)

        handle = author.handle.lstrip("@").lower()
        if handle in allow:
            assignments.append(CohortAssignment(
                author_id=aid, cohort=allow[handle], method="allowlist",
                confidence=1.0, reason="configured handle"))
            counts[allow[handle]] += 1
            continue

        # Prior from the query that found them: only trusted for the tiers
        # a `from:`-style query can actually pin down.
        hint = next((p.raw.get("cohort_hint") for p in mine if p.raw.get("cohort_hint")), None)
        if hint in ("official", "pro_media") and any(
            f"from:{handle}" in q.query.lower()
            for q in _issue_queries(cfg) if q.cohort == hint
        ):
            assignments.append(CohortAssignment(
                author_id=aid, cohort=hint, method="allowlist", confidence=0.95,
                reason="matched a from: query for this tier"))
            counts[hint] += 1
            continue

        got = heuristic(author, lang, cfg)
        if got and got[1] >= 0.6 and got[0] in ("crowd",):
            cohort, conf, why = got
            assignments.append(CohortAssignment(
                author_id=aid, cohort=cohort, method="heuristic",
                confidence=conf, reason=why))
            counts[cohort] += 1
            continue

        unresolved.append((author, lang, mine[0] if mine else None))

    if unresolved:
        resolved = _classify_with_model(cfg, unresolved, llm, log)
        for a in resolved:
            counts[a.cohort] += 1
        assignments.extend(resolved)

    store.upsert_cohorts(assignments)
    return {"new": len(assignments), "counts": dict(counts),
            "model_calls": len(unresolved)}


def _issue_queries(cfg):
    for issue in cfg.issues.values():
        yield from issue.queries


def _classify_with_model(cfg, unresolved, llm, log) -> list[CohortAssignment]:
    rows = []
    for i, (author, lang, sample) in enumerate(unresolved):
        rows.append({
            "index": i, "handle": author.handle, "name": author.name,
            "followers": author.followers, "verified": author.verified,
            "description": author.description, "lang": lang,
            "age_days": int(author.account_age_days) if author.account_age_days else "?",
            "sample": sample.text if sample else "",
        })

    items = []
    batch_size = 40
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        try:
            if llm is None:
                raise LLMUnavailable("no llm configured")
            result = llm.structured(
                system=CLASSIFY_SYSTEM,
                user=classify_user_prompt(chunk),
                schema=AuthorClassBatch,
                model=cfg.llm.extract_model,
                effort="low",
                max_tokens=8000,
            )
            got = result.items
        except LLMUnavailable:
            got = offline_stub.classify_authors(chunk)
        except Exception as e:                                    # noqa: BLE001
            log(f"  ! classifier fell back to heuristics: {e}")
            got = offline_stub.classify_authors(chunk)
        by_index = {g.index: g for g in got}
        for r in chunk:
            g = by_index.get(r["index"])
            author = unresolved[r["index"]][0]
            if g is None:
                items.append(CohortAssignment(
                    author_id=author.id, cohort="crowd", method="heuristic",
                    confidence=0.3, reason="classifier returned nothing"))
            else:
                items.append(CohortAssignment(
                    author_id=author.id, cohort=g.cohort, method="llm",
                    confidence=g.confidence, reason=g.reason))
    return items
