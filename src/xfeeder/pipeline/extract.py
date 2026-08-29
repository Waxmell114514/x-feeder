"""Stage 3 - reduce every post to its position on the issue.

This is the token-hungry stage, so it is built to never pay twice:

  * extractions are stored per (post, issue) and keyed by prompt version -
    a re-run only pays for genuinely new posts;
  * posts are sent in batches, with the long frozen rubric in a cached
    system block so only the posts themselves are billed at full rate;
  * batches run concurrently.

Failure of one batch never fails the run: that batch falls back to the
offline keyword extractor and is flagged, so a rate limit degrades quality
rather than losing the snapshot.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from ..llm import LLMClient, LLMUnavailable
from ..llm import offline as offline_stub
from ..llm.client import PROMPT_VERSION
from ..llm.prompts import (
    ExtractionBatch, extract_system_prompt, extract_user_prompt,
)
from ..models import Extraction, Post
from ..store import Store


def run_extract(cfg, store: Store, issue_id: str, posts: list[Post],
                llm: LLMClient | None = None, log=print, force: bool = False) -> dict:
    issue = cfg.issue(issue_id)
    existing = store.get_extractions(issue_id)

    todo = [
        p for p in posts
        if force or p.id not in existing
        or existing[p.id].extractor_version != PROMPT_VERSION
    ]
    if not todo:
        log(f"  all {len(posts)} posts already extracted (v{PROMPT_VERSION})")
        return {"new": 0, "reused": len(posts), "degraded": 0}

    authors = store.get_authors({p.author_id for p in todo})
    cohorts = store.get_cohorts()
    system = extract_system_prompt(issue, lang=issue.output_lang or cfg.output_lang)

    size = max(1, cfg.llm.extract_batch_size)
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    log(f"  extracting {len(todo)} posts in {len(batches)} batches")

    def do_batch(batch: list[Post]) -> tuple[list[Extraction], bool]:
        rows = []
        for i, p in enumerate(batch):
            a = authors.get(p.author_id)
            rows.append({
                "index": i,
                "handle": a.handle if a else p.author_id,
                "text": p.text,
                "lang": p.lang or "",
                "cohort": cohorts[p.author_id].cohort if p.author_id in cohorts else "?",
                "is_reply": p.is_reply,
            })
        degraded = False
        try:
            if llm is None:
                raise LLMUnavailable("no llm configured")
            result = llm.structured(
                system=system,
                user=extract_user_prompt(rows),
                schema=ExtractionBatch,
                model=cfg.llm.extract_model,
                effort=cfg.llm.extract_effort,
                max_tokens=16000,
            )
        except LLMUnavailable:
            result = offline_stub.extract(rows, issue)
            degraded = True
        except Exception as e:                                    # noqa: BLE001
            log(f"  ! batch failed ({e}); falling back to keyword extractor")
            result = offline_stub.extract(rows, issue)
            degraded = True
        return _to_extractions(result, batch, issue), degraded

    out: list[Extraction] = []
    degraded_batches = 0
    workers = max(1, cfg.llm.max_concurrency)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for items, degraded in pool.map(do_batch, batches):
            out.extend(items)
            degraded_batches += int(degraded)

    store.upsert_extractions(out)
    relevant = sum(1 for e in out if e.relevant)
    log(f"  extracted {len(out)} ({relevant} relevant)"
        + (f", {degraded_batches} batch(es) degraded" if degraded_batches else ""))
    return {"new": len(out), "reused": len(posts) - len(todo),
            "relevant": relevant, "degraded": degraded_batches}


def _to_extractions(result: ExtractionBatch, batch: list[Post], issue) -> list[Extraction]:
    valid = set(issue.stance_ids())
    anchors = issue.anchors()
    by_index = {item.index: item for item in result.items}
    out = []
    for i, post in enumerate(batch):
        item = by_index.get(i)
        if item is None:
            out.append(Extraction(
                post_id=post.id, issue_id=issue.id, relevant=False,
                stance="unclear", key_claim="", extractor_version=PROMPT_VERSION,
            ))
            continue
        stance = item.stance if item.stance in valid else "unclear"
        prob = item.probability
        if prob is not None and not (0.0 <= prob <= 1.0):
            prob = None
        # Coherence gate: a post that argues for a hike cannot also put the
        # odds of a hike at 3%. One of the two readings is wrong, and the
        # stance is the more robust of the two, so the number is dropped.
        # Only gross contradictions are caught - "I lean hike but it's only
        # 35%" is a real position and survives.
        anchor = anchors.get(stance)
        if prob is not None and anchor is not None and abs(prob - anchor) > 0.6:
            prob = None
        out.append(Extraction(
            post_id=post.id,
            issue_id=issue.id,
            relevant=bool(item.relevant),
            stance=stance,
            probability=prob,
            horizon=item.horizon,
            key_claim=item.key_claim.strip(),
            reasoning_kind=item.reasoning_kind,
            intensity=item.intensity,
            is_question=item.is_question,
            is_sarcastic=item.is_sarcastic,
            extractor_version=PROMPT_VERSION,
        ))
    return out
