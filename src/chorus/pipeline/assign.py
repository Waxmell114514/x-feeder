"""Stage 6 - sort each document to the opinion leader it speaks for.

This is the stage the whole rebuild was for. One Jev Choice over the panel,
per document, and the answer comes back with a probability for every
leader and a confidence over the distribution. It is the cheapest and
fastest thing in the pipeline: a typed decision, in a couple of hundred
milliseconds, for a fraction of a cent.

Three rules, all enforced here rather than hoped for in the wording:

**A document is only offered the leaders on its own side.** A bull and a
bear are never the same bloc however similar their prose, so the Choice is
scoped to the leaders whose stance matches the reading. This both preserves
the hard split the previous design relied on and makes the question easier:
fewer options, all genuinely competing.

**Which bloc and whether any bloc are two different questions.** A Choice
is relative - it settles which option fits best even when none fits at all.
So a separate Noul asks whether the document argues any of the listed
positions, and a document joins a bloc only if both agree. Someone arguing
something nobody else is arguing is reported as unassigned, not filed under
the nearest neighbour.

**Unassigned voice is reported, not hidden.** `TierVerdict.unassigned_share`
is how much of a tier fit no leader on the panel. A panel that leaves a
third of the room unrepresented is a bad panel, and the report should say
so instead of quietly rounding it away.
"""
from __future__ import annotations

from collections import defaultdict

from ..jev import questions as Q
from ..models import Document, Panel, PanelAssignment, Reading
from ..store import Store
from .panel import panel_version


def run_assign(cfg, store: Store, issue, documents: list[Document],
               readings: dict[str, Reading], panel: Panel, jev,
               log=print, force: bool = False) -> dict:
    version = panel_version(panel)
    if not panel.leaders:
        log("  no panel, nothing to assign")
        return {"new": 0, "assigned": 0, "unassigned": 0, "reused": 0}

    by_stance: dict[str, list] = defaultdict(list)
    for leader in panel.leaders:
        by_stance[leader.stance].append(leader)

    existing = store.get_assignments(issue.id)
    todo: list[Document] = []
    for doc in documents:
        reading = readings.get(doc.id)
        if reading is None or not reading.relevant:
            continue
        if not by_stance.get(reading.stance):
            continue                      # no leader holds this side; unassigned
        old = existing.get(doc.id)
        if force or old is None or old.panel_version != version:
            todo.append(doc)

    if not todo:
        log(f"  all {len(existing)} assignments still valid for this panel")
        return {"new": 0, "assigned": sum(1 for a in existing.values() if a.leader_id),
                "unassigned": sum(1 for a in existing.values() if not a.leader_id),
                "reused": len(existing)}

    items = []
    for doc in todo:
        leaders = by_stance[readings[doc.id].stance]
        payload = {"channel": doc.channel, "body": doc.own_text,
                   "context": doc.context}
        items.append((
            doc.id,
            Q.assign_state(issue, payload, max_chars=cfg.jev.max_state_chars),
            Q.assign_questions(leaders),
        ))

    failures: list[str] = []
    responses = jev.ask_many(
        items, on_error=lambda key, e: failures.append(f"{key}: {e}"))
    for message in failures[:3]:
        log(f"  ! assignment failed for {message}")

    out: list[PanelAssignment] = []
    for doc in todo:
        response = responses.get(doc.id)
        if response is None:
            continue
        leaders = by_stance[readings[doc.id].stance]
        out.append(_assignment(cfg, issue, doc, response, leaders, version))

    store.upsert_assignments(out)
    assigned = sum(1 for a in out if a.leader_id)
    log(f"  assigned {assigned}/{len(out)} documents to a leader "
        f"({len(out) - assigned} argue something the panel does not cover)")
    return {"new": len(out), "assigned": assigned,
            "unassigned": len(out) - assigned, "reused": len(existing)}


def _assignment(cfg, issue, doc, response, leaders, version: str) -> PanelAssignment:
    floor = cfg.jev.assign_confidence_floor
    belongs = response.noul("belongs")

    if len(leaders) == 1:
        # One leader on this side: there is nothing to choose between, so
        # the Noul carries the whole decision.
        leader_id = leaders[0].id if belongs >= floor else ""
        return PanelAssignment(
            doc_id=doc.id, issue_id=issue.id, leader_id=leader_id,
            probabilities={leaders[0].id: round(belongs, 4)},
            confidence=round(belongs, 3), panel_version=version,
        )

    choice, probabilities, confidence = response.choice("leader")
    valid = {leader.id for leader in leaders}
    keep = choice in valid and confidence >= floor and belongs >= floor
    return PanelAssignment(
        doc_id=doc.id,
        issue_id=issue.id,
        leader_id=choice if keep else "",
        probabilities={k: round(v, 4) for k, v in probabilities.items()
                       if k in valid},
        confidence=round(min(confidence, belongs), 3),
        panel_version=version,
    )
