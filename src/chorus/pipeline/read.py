"""Stage 4 - reduce every document to its position on the issue.

One request per document, carrying five or six questions that are evaluated
in parallel against a state that is ingested once. That is the shape this
API rewards: asking "is it relevant", "which side", "whose view is it",
"how firmly" and "is it sarcastic" separately costs barely more than asking
any one of them, and each answer is a cleaner judgement than a rubric that
tried to fold them together.

Two things the code does and the model does not:

**Finding the numbers.** A regular expression collects every percentage in
the document and hands them to Jev as a closed set, with the words around
each one. Jev picks which - if any - is a likelihood of the event. Asking
it to *extract* a number would invite the failure mode the previous version
of this project actually hit: reading "unchanged at 4.25-4.50%" as a 4.5%
chance of a hike, and destroying the official tier's reading with it.

**Deciding what a sarcasm score means.** Jev reports P(the text means the
opposite of what it says). The old pipeline asked its model to record the
*intended* stance, which means a coin-flip judgement could silently flip a
document to the other side. Here sarcasm never moves a document across the
axis; it only reduces how much the document is allowed to count.
"""
from __future__ import annotations

from ..jev import QUESTION_VERSION
from ..jev import questions as Q
from ..models import Document, Reading
from ..store import Store

# A stated number this far from its own side's anchor is one of the two
# readings being wrong. The side is the more robust of the two, so the
# number is dropped - "I lean hike but it's only 35%" survives, "definitely
# a hike, maybe 3%" does not.
COHERENCE_GAP = 0.6


def run_read(cfg, store: Store, issue, documents: list[Document], jev,
             log=print, force: bool = False) -> dict:
    existing = store.get_readings(issue.id)
    todo = [
        d for d in documents
        if force or d.id not in existing
        or existing[d.id].reader_version != QUESTION_VERSION
    ]
    if not todo:
        log(f"  all {len(documents)} documents already read ({QUESTION_VERSION})")
        return {"new": 0, "reused": len(documents), "relevant": 0, "failed": 0}

    log(f"  reading {len(todo)} documents"
        + ("" if jev.live else "  [stub: no Jev key]"))

    items = []
    for doc in todo:
        payload = {"channel": doc.channel, "channel_kind": doc.channel_kind,
                   "body": doc.own_text, "context": doc.context}
        candidates = Q.number_candidates(doc.body)
        items.append((
            doc.id,
            Q.read_state(issue, payload, max_chars=cfg.jev.max_state_chars),
            Q.read_questions(issue, number_candidates=candidates),
        ))

    failures: list[str] = []
    responses = jev.ask_many(
        items, on_error=lambda key, e: failures.append(f"{key}: {e}"))
    for message in failures[:3]:
        log(f"  ! read failed for {message}")

    readings = []
    for doc in todo:
        response = responses.get(doc.id)
        if response is None:
            continue
        readings.append(_to_reading(cfg, issue, doc, response))

    store.upsert_readings(readings)
    relevant = sum(1 for r in readings if r.relevant)
    log(f"  read {len(readings)} ({relevant} bear on the question"
        + (f", {len(failures)} failed)" if failures else ")"))
    return {"new": len(readings), "reused": len(documents) - len(todo),
            "relevant": relevant, "failed": len(failures)}


def _to_reading(cfg, issue, doc: Document, response) -> Reading:
    valid = set(issue.stance_ids()) | {"unclear"}
    anchors = issue.anchors()

    relevance = response.noul("relevant")
    stance, probabilities, stance_confidence = response.choice("stance")
    if stance not in valid:
        stance = "unclear"
    # Two ways the answer can fail to be an answer, and they are different
    # questions. A flat distribution means the model had nothing to go on.
    # A near-tie at the top means it had plenty and the sides are level -
    # which on a four-option axis can still look concentrated. Recording
    # the winner of either would turn a shrug into a vote.
    if stance_confidence < cfg.jev.stance_confidence_floor:
        stance = "unclear"
    elif _margin(probabilities) < cfg.jev.stance_margin_floor:
        stance = "unclear"

    speaks_for, _, provenance_confidence = response.choice("speaks_for")
    if speaks_for not in ("own_view", "market_pricing", "official_guidance",
                          "reported_data", "unclear"):
        speaks_for = "own_view"

    # "The author is relaying an event that already happened, and takes no
    # side on the question" is what `reported_data` means in the question
    # this answer came from. On a question about something that has not
    # happened yet, the code honours that instead of quietly reading the
    # report as a forecast: "Fed hikes rates" is news about last week, not
    # a view about next month. The document stays in the sample as
    # undecided, and still counts as volume.
    # ...but only when the model was sure it is a report. Erasing a real
    # position on an uncertain guess is the same failure the sarcasm rule
    # refuses to make, in the other direction.
    if (issue.forward_looking and speaks_for == "reported_data"
            and provenance_confidence >= cfg.jev.provenance_confidence_floor):
        stance = "unclear"

    number = _stated_number(response, doc)
    anchor = anchors.get(stance)
    if number is not None and anchor is not None and abs(number - anchor) > COHERENCE_GAP:
        number = None

    # Relevance decides whether a document is in the sample. Stance decides
    # which side it is on, and "no side" is one of the answers.
    #
    # Conflating the two deletes exactly the documents that matter most: an
    # FOMC statement that says a decision has not been made scores 0.96 on
    # relevance and takes no side on purpose, and "officialdom has given no
    # guidance" is the fact the whole official-vs-public signal rests on. A
    # media report that the experts are split is the same shape. They stay
    # in, counted under `unclear`, which carries no anchor and so moves no
    # probability - it only shows how much of the room is still waiting.
    return Reading(
        doc_id=doc.id,
        issue_id=issue.id,
        relevant=relevance >= cfg.jev.relevance_floor,
        relevance=round(relevance, 3),
        stance=stance,
        stance_probabilities={k: round(v, 4) for k, v in probabilities.items()},
        stance_confidence=round(stance_confidence, 3),
        speaks_for=speaks_for,
        intensity=round(response.unit_score("intensity"), 3),
        sarcastic=response.noul("sarcastic") >= cfg.jev.sarcasm_floor,
        is_question=_is_question(doc),
        stated_probability=number,
        reader_version=QUESTION_VERSION,
    )


def _margin(probabilities: dict[str, float]) -> float:
    """How far the leading option is ahead of the next one."""
    if len(probabilities) < 2:
        return 1.0
    ranked = sorted(probabilities.values(), reverse=True)
    return ranked[0] - ranked[1]


def _is_question(doc: Document) -> bool:
    """A title that ends in a question mark is asking, not asserting."""
    head = (doc.title or doc.body).strip()
    return head.endswith(("?", "？"))


def _stated_number(response, doc: Document) -> float | None:
    """Map Jev's pick back onto the number the regex found."""
    if "stated_number" not in response:
        return None
    pick, _, _ = response.choice("stated_number")
    if not pick or pick == "none" or not pick.startswith("n"):
        return None
    try:
        index = int(pick[1:])
    except ValueError:
        return None
    candidates = Q.number_candidates(doc.body)
    if index >= len(candidates):
        return None
    value = candidates[index]["value"]
    return round(value, 4) if 0.0 <= value <= 1.0 else None
