"""Stage 5 - the panel of virtual opinion leaders.

This is where the novel's idea has to survive contact with a model that
cannot write. The X-era pipeline clustered documents and then asked an LLM
to name each cluster and speak for it. Jev names nothing, so the panel has
to exist *before* the sorting - and that turns out to be the better design
for a monitor anyway:

  * a declared panel makes snapshots comparable. Blocs that are re-invented
    every run cannot be tracked over time, and "the consensus moved" is the
    only thing a monitor is really for;
  * a panel written down in the issue file is a claim a human can argue
    with, which a cluster label produced at 3am cannot be.

So the panel comes from one of two places:

  **Declared** - written into the issue file. This is the steady state.

  **Mined** - for the first run on a new issue, when you do not yet know
  what the arguments are. Candidate positions are phrases lifted verbatim
  from the documents, ranked by how many *documents* use them, deduplicated
  by meaning, and then put to Jev one by one: is this a position a person
  could hold? Nothing is generated. A mined panel is a draft; `chorus panel
  --write` prints it in the form the issue file wants.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import defaultdict
from typing import Optional

import numpy as np

from .. import textutil
from ..jev import QUESTION_VERSION
from ..jev import questions as Q
from ..jev.embeddings import embed
from ..models import Document, Leader, Panel, Reading
from ..store import Store
from .cluster import agglomerate

MAX_CANDIDATES_PER_STANCE = 8


# ======================================================================
def declared_panel(issue) -> Optional[Panel]:
    if not issue.panel:
        return None
    leaders = [
        Leader(id=spec.id, label=spec.label, label_zh=spec.label_zh,
               stance=spec.stance, description=spec.description or spec.label,
               flip_on=spec.flip_on, origin="declared")
        for spec in issue.panel
    ]
    return Panel(issue_id=issue.id, leaders=leaders, origin="declared",
                 built_at=dt.datetime.now(dt.timezone.utc),
                 note="declared in the issue file")


def load_or_build(cfg, store: Store, issue, documents: list[Document],
                  readings: dict[str, Reading], jev, *, rebuild: bool = False,
                  log=print) -> Panel:
    """Declared beats stored beats mined."""
    declared = declared_panel(issue)
    if declared is not None:
        log(f"  panel: {len(declared.leaders)} leaders declared in the issue file")
        store.save_panel(declared)
        return declared

    if not rebuild:
        stored = store.get_panel(issue.id)
        if stored and stored.leaders:
            when = (f" from {stored.built_at:%Y-%m-%d %H:%M}"
                    if stored.built_at else "")
            log(f"  panel: reusing the {len(stored.leaders)} mined leaders{when} "
                f"(chorus panel --rebuild to mine again)")
            return stored

    panel = mine_panel(cfg, issue, documents, readings, jev, log=log)
    store.save_panel(panel)
    return panel


# ======================================================================
def mine_panel(cfg, issue, documents: list[Document],
               readings: dict[str, Reading], jev, log=print) -> Panel:
    by_stance: dict[str, list[Document]] = defaultdict(list)
    speakers: dict[str, set[str]] = defaultdict(set)
    for doc in documents:
        reading = readings.get(doc.id)
        if reading is None or not reading.relevant or reading.stance == "unclear":
            continue
        by_stance[reading.stance].append(doc)
        speakers[reading.stance].add(doc.speaker)

    if not by_stance:
        log("  ! nothing relevant to mine a panel from")
        return Panel(issue_id=issue.id, origin="mined",
                     built_at=dt.datetime.now(dt.timezone.utc),
                     note="no relevant documents")

    allowance = _allocate(cfg.thresholds.panel_size, speakers)
    ignore = {t.lower() for t in issue.terms + issue.entities}

    corpora = {stance: textutil.mining_corpus(docs)
               for stance, docs in by_stance.items()}

    candidates: list[tuple[str, str, int]] = []      # (stance, phrase, texts)
    for stance, texts in corpora.items():
        n = allowance.get(stance, 0)
        if n <= 0 or not texts:
            continue
        # A position has to be held by more than one distinct text to be a
        # position at all. The floor only relaxes when a side is so small
        # that requiring a second text would leave it unrepresented.
        floor = cfg.thresholds.min_phrase_docs
        mined = textutil.mine_phrases(
            texts,
            min_docs=floor if len(texts) >= 2 * floor else 1,
            max_words=4, min_words=2,
            limit=MAX_CANDIDATES_PER_STANCE * 4, ignore=ignore, with_docs=True,
        )
        mined = _distinctive(mined, stance, corpora)
        mined = _same_documents(mined)
        phrases = [(phrase, count) for phrase, count, _ in mined]
        for phrase, count in _dedupe(cfg, phrases)[:MAX_CANDIDATES_PER_STANCE]:
            candidates.append((stance, phrase, count))

    if not candidates:
        log("  ! no phrase appeared in enough documents to be a position")
        return Panel(issue_id=issue.id, origin="mined",
                     built_at=dt.datetime.now(dt.timezone.utc),
                     note="no repeated phrases")

    coherent = _judge_coherence(cfg, issue, jev, candidates)

    leaders: list[Leader] = []
    used: dict[str, int] = defaultdict(int)
    for stance, phrase, count, score in coherent:
        if used[stance] >= allowance.get(stance, 0):
            continue
        used[stance] += 1
        leaders.append(_leader(issue, stance, phrase, count, score))

    log(f"  panel: mined {len(leaders)} leaders from "
        f"{sum(len(v) for v in by_stance.values())} documents "
        f"({len(candidates)} candidate positions judged)")
    return Panel(
        issue_id=issue.id, leaders=leaders, origin="mined",
        built_at=dt.datetime.now(dt.timezone.utc),
        note="mined from the corpus; run `chorus panel --write` to freeze it",
    )


# ----------------------------------------------------------------------
def _allocate(panel_size: int, speakers: dict[str, set[str]]) -> dict[str, int]:
    """How many leaders each side of the axis gets.

    Shares are counted in distinct speakers, not documents, so a side does
    not get more seats because one of its members posts more often.
    """
    totals = {s: len(v) for s, v in speakers.items() if v}
    grand = sum(totals.values())
    if not grand:
        return {}
    out: dict[str, int] = {}
    for stance, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        share = n / grand
        seats = int(round(panel_size * share))
        if share >= 0.10:
            seats = max(1, seats)
        out[stance] = seats
    # Trim from the largest side first if rounding overshot the budget.
    while sum(out.values()) > panel_size:
        widest = max(out, key=lambda k: out[k])
        if out[widest] <= 1:
            break
        out[widest] -= 1
    return out


def _distinctive(phrases: list[tuple[str, int]], stance: str,
                 corpora: dict[str, list[str]],
                 floor: float = 0.6) -> list[tuple[str, int]]:
    """Keep the phrases that mark one side of the axis.

    "hold rates steady" turns up inside documents arguing for a hike - "the
    people telling you they will hold rates steady are wrong" - and a naive
    miner will hand the hike bloc a label that says the opposite of what it
    believes. A phrase earns a seat on one side only if that is mostly where
    it is used.
    """
    out = []
    for phrase, count, docs in phrases:
        elsewhere = sum(
            sum(1 for text in texts if phrase in text.lower())
            for other, texts in corpora.items() if other != stance
        )
        if count / max(1.0, count + elsewhere) >= floor:
            out.append((phrase, count, docs))
    return out


def _same_documents(phrases, overlap: float = 0.7):
    """Two phrases used by the same documents are one argument.

    "futures imply a 38%" and "chance of a hike" share no words at all and
    are the same bloc - they are two windows over one sentence that the same
    people wrote. Word similarity cannot see that; the document sets can.
    """
    out = []
    for phrase, count, docs in phrases:
        duplicate = False
        for _, kept_count, kept_docs in out:
            union = len(docs | kept_docs)
            if union and len(docs & kept_docs) / union >= overlap:
                duplicate = True
                break
        if not duplicate:
            out.append((phrase, count, docs))
    return out


def _dedupe(cfg, phrases: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Merge candidate phrases that mean the same thing.

    "core services inflation" and "services inflation still hot" are one
    position wearing two coats. Clustering the phrases - not the documents -
    keeps the panel from spending two seats on one argument.
    """
    if len(phrases) < 2:
        return phrases

    # Cheap pass first: two phrases that share most of their words are the
    # same phrase with a different window over it ("imply 38% chance" and
    # "38% chance of hike"). No embedding is going to tell you more about
    # that than the words already do.
    lexical: list[tuple[str, int]] = []
    taken: list[set[str]] = []
    for phrase, count in sorted(phrases, key=lambda kv: (-kv[1], -len(kv[0]))):
        words = set(phrase.split())
        if any(len(words & seen) / max(1, min(len(words), len(seen))) > 0.5
               for seen in taken):
            continue
        taken.append(words)
        lexical.append((phrase, count))
    phrases = lexical
    if len(phrases) < 2:
        return phrases

    vectors = embed([p for p, _ in phrases], cfg)
    groups = agglomerate(np.asarray(vectors), cfg.thresholds.phrase_similarity)
    out = []
    for group in groups:
        best = max(group, key=lambda i: (phrases[i][1], len(phrases[i][0])))
        out.append(phrases[best])
    out.sort(key=lambda kv: -kv[1])
    return out


def _judge_coherence(cfg, issue, jev, candidates: list[tuple[str, str, int]]):
    """-> [(stance, phrase, docs, coherence)] worth putting on a panel."""
    if jev.offline:
        # No judge available. Every phrase that several documents used goes
        # through, which is what the miner already guarantees - a demo panel
        # is a plausible panel, not a judged one.
        return sorted([(stance, phrase, count, 0.5)
                       for stance, phrase, count in candidates],
                      key=lambda row: -row[2])

    phrases = [p for _, p, _ in candidates]
    kept = []
    for start in range(0, len(phrases), 20):
        chunk = phrases[start:start + 20]
        response = jev.ask(Q.position_state(issue, chunk),
                           Q.position_questions(chunk))
        for i, phrase in enumerate(chunk):
            stance, _, count = candidates[start + i]
            score = response.noul(f"p{i}_coherent")
            if score >= cfg.jev.relevance_floor:
                kept.append((stance, phrase, count, round(score, 3)))
    # Most-used phrase first, so the seats go to the arguments most people
    # are actually making.
    kept.sort(key=lambda row: (-row[2], -row[3]))
    return kept


def _leader(issue, stance: str, phrase: str, count: int, score: float) -> Leader:
    label_en = issue.stance_label(stance, "en")
    label_zh = issue.stance_label(stance, "zh")
    return Leader(
        id=f"{stance}--{_slug(phrase)}",
        label=phrase,
        # The frame is translated; the phrase is not. It is verbatim source
        # text, and nothing in this system is allowed to rewrite it.
        label_zh=f"{label_zh}·{phrase}",
        stance=stance,
        description=(
            f'Argues for "{label_en}", reasoning from: {phrase}.'
        ),
        origin="mined",
        evidence=[f"{phrase} (in {count} documents, coherence {score:.2f})"],
    )


def _slug(phrase: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")[:40] or "x"


def panel_version(panel: Panel) -> str:
    """Identity of a panel, for cache keys and for stored assignments.

    Changing a leader's wording changes the question Jev is asked, so every
    assignment made under the old wording has to be re-judged; changing only
    the display label does not.
    """
    blob = "|".join(
        f"{leader.id}::{leader.description}" for leader in sorted(
            panel.leaders, key=lambda x: x.id)
    )
    return hashlib.sha1(f"{QUESTION_VERSION}|{blob}".encode()).hexdigest()[:12]
