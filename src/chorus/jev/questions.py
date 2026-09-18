"""Every question this system asks Jev, in one file.

This is the file to review. Jev answers the question you wrote, not the one
you meant, so the wording here *is* the behaviour of the system - more than
any code in `pipeline/`. The numbers these answers are compared against sit
together in the `jev:` block of `config.yaml`; between the two, that is the
entire surface where judgement enters.

Four rules followed throughout, each one a documented failure mode of the
model:

  * **Say the condition, do not hint at it.** Criteria are read literally.
  * **Never ask for arithmetic.** No counting, no dates, no "how many of
    these". Shares, ages and totals are computed in `pipeline/`.
  * **One judgement per question.** Relevance, side, whose view it is, and
    which bloc it joins are four questions, not one rubric.
  * **Send only what the question needs.** State is trimmed, because
    accuracy falls as it fills with material the question cannot use.

Jev generates no text at all, which is why nothing here asks for a summary,
a label, or a name. Every word in the report comes from `phrasing.py` or
verbatim from a document.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# ======================================================================
# Primitive builders - the three question types of the System One API
# ======================================================================
def noul(instructions: Any, *, yes: Any = None, no: Any = None) -> dict:
    """A yes/no question. The answer is P(yes), with no confidence value."""
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    criteria = {}
    if yes is not None:
        criteria["true"] = yes
    if no is not None:
        criteria["false"] = no
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions: Any, criteria: dict[str, Any]) -> dict:
    """Pick one of a defined set. The answer carries the full distribution."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: Any, levels: list[Any]) -> dict:
    """Rate against ordered levels. Levels must describe concrete situations."""
    return {"type": "score", "instructions": instructions, "criteria": levels}


# ======================================================================
# 1. Reading one document against one issue
# ======================================================================
SPEAKS_FOR_CRITERIA = {
    "own_view": {
        "means": "The author is stating their own belief, argument or forecast.",
        "examples": ["I think", "my view", "they will", "we believe",
                     "in my opinion", "expect them to"],
    },
    "market_pricing": {
        "means": "The author is reporting what markets, betting odds, polls "
                 "or forecasters currently price, without saying whether "
                 "they agree.",
        "examples": ["futures imply", "priced at", "odds of", "the market is "
                     "pricing", "swaps show", "traders see"],
    },
    "official_guidance": {
        "means": "The author is relaying what an official body, spokesperson "
                 "or office has said, or is that body speaking for itself.",
        "examples": ["the committee", "we are prepared to", "policy is data "
                     "dependent", "the chair said", "officials signalled",
                     "the statement said"],
    },
    "reported_data": {
        "means": "The author is relaying a measurement, data release or an "
                 "event that already happened, and takes no side on the "
                 "question.",
        "examples": ["came in at", "rose to", "fell to", "was released",
                     "raised rates", "hiked rates", "voted to", "announced "
                     "a decision"],
    },
    "unclear": {
        "means": "None of the above fits, or the document takes no position "
                 "at all.",
    },
}

INTENSITY_LEVELS = [
    "Raises the position as a possibility, a question, or with heavy hedging.",
    "States the position plainly, as an ordinary assertion.",
    "States the position emphatically, as settled or certain.",
]


def read_state(issue, document: dict, *, max_chars: int = 4000) -> dict:
    """The state for a read: the question's context and one document.

    The document is trimmed and stripped of anything the four read
    questions cannot use. Everything the questions do need - what is being
    asked, the facts that fix its meaning, and where the document was
    published - is named, so no question has to reach for it.
    """
    body = (document.get("body") or "").strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    state = {
        "question": issue.question.strip(),
        "document": {
            "venue": document.get("channel", "") or "unknown",
            "venue_kind": document.get("channel_kind", "forum"),
            "text": body,
        },
    }
    # The thread a comment is answering is context, never the commenter's
    # own position - every question below asks about `document.text` alone.
    context = (document.get("context") or "").strip()
    if context:
        state["document"]["answering"] = context[:300]
    if issue.background:
        state["background"] = issue.background.strip()
    return state


def read_questions(issue, *, number_candidates: Optional[list[dict]] = None) -> dict:
    """The four-to-five judgements that turn a document into a Reading.

    They are asked together, against one state, because they are
    independent of one another and Jev evaluates them in parallel.
    """
    q = issue.question.strip()
    questions: dict[str, dict] = {
        "relevant": noul(
            f"The text in `document.text` bears on this question: «{q}» "
            f"It bears on the question if it argues for an answer, reports "
            f"someone else's answer, or reports a fact that would change the "
            f"answer.",
            yes=_relevance_yes(issue),
            no="The text is about something else, or is an advertisement, a "
               "greeting, a joke with no position, or pure abuse.",
        ),
        "stance": choice(
            f"Which answer to this question does the text in `document.text` "
            f"support: «{q}»",
            _stance_criteria(issue),
        ),
        "speaks_for": choice(
            "Whose position is the text in `document.text` giving?",
            SPEAKS_FOR_CRITERIA,
        ),
        "intensity": score(
            "How firmly does the text in `document.text` hold the position it "
            "takes?",
            INTENSITY_LEVELS,
        ),
        "sarcastic": noul(
            "The text in `document.text` means the opposite of what it says "
            "literally, because it is sarcastic or ironic.",
            yes={
                "means": "Read straight, the words say one thing; the author "
                         "plainly intends the reverse.",
                "examples": ["oh sure", "yeah right", "any day now", "/s",
                             "sure thing", "🙄", "as if", "totally"],
            },
            no="The author means what the words say.",
        ),
    }
    if number_candidates:
        questions["stated_number"] = choice(
            _number_instructions(issue),
            _number_criteria(number_candidates),
        )
    return questions


def _relevance_yes(issue) -> Any:
    """What a yes means, plus the subject matter in the issue's own words.

    The examples are the issue file's own vocabulary. They are illustrative,
    not a filter - a document can bear on the question without using any of
    them - but they pin down what "the subject" refers to, which a literal
    reader needs, and they are what the offline stand-in matches on.
    """
    subject = [t for t in (issue.entities + issue.terms) if t][:8]
    # The words that answer the question are part of its subject matter: a
    # document saying "they have to hike" is about rate decisions whether or
    # not it names the institution.
    for option in issue.axis:
        subject.extend(option.keywords[:3])

    entry: dict[str, Any] = {
        "means": "The text argues for an answer to the question, reports "
                 "someone else's answer, or reports a fact that bears on it.",
    }
    seen, examples = set(), []
    for term in subject:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            examples.append(term)
    if examples:
        entry["examples"] = examples[:20]
    return entry


def _stance_criteria(issue) -> dict[str, Any]:
    """Each option's criteria carry its meaning and, where the issue file
    gives them, phrases that exemplify it.

    The examples earn their place twice: they sharpen a literal reading of
    the option for Jev, and they are the same list the offline stand-in
    matches on, so the two readers are defined by one piece of data.
    """
    criteria: dict[str, Any] = {}
    for option in issue.axis:
        entry: dict[str, Any] = {
            "means": option.description or option.label,
        }
        if option.keywords:
            entry["examples"] = list(option.keywords[:10])
        criteria[option.id] = entry
    criteria.setdefault("unclear", {
        "means": "The text takes no identifiable position on this question.",
    })
    return criteria


def _number_instructions(issue) -> str:
    what = issue.quantity.description if issue.quantity else issue.question
    return (
        "Each option below is a number that appears in `document.text`, shown "
        "with the words around it. Which one, if any, is the text's estimate "
        f"of this: {what.strip()} "
        "Choose `none` unless the text presents the number as the likelihood "
        "of that outcome. Percentages that measure something else - a rate, a "
        "level, a change, a share of people - are not estimates of it."
    )


def _number_criteria(candidates: list[dict]) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    for i, c in enumerate(candidates[:12]):
        criteria[f"n{i}"] = {
            "number": c["raw"],
            "in_context": c["context"],
        }
    criteria["none"] = "No number in the list is the likelihood of that outcome."
    return criteria


# --- candidate numbers are found in code, not by the model ------------
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")
_IN_TEN = re.compile(r"\b(\d{1,2})\s*(?:in|out of|/)\s*(?:10|ten)\b", re.IGNORECASE)
_ODDS_WORD = re.compile(r"\b(?:coin\s?flip|even odds|fifty[- ]fifty|50[- ]50)\b",
                        re.IGNORECASE)
_CONTEXT = 70


def number_candidates(text: str, limit: int = 12) -> list[dict]:
    """Every number in the text that *could* be a likelihood, with context.

    Finding candidates is a job for a regular expression, and choosing
    between them is a job for judgement. Splitting it that way is what keeps
    an inflation print from being read as the odds of a rate rise - the one
    bug in the X-era pipeline that corrupted a whole tier's reading.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(raw: str, start: int, end: int, value: float) -> None:
        if not (0.0 <= value <= 1.0) or raw in seen:
            return
        seen.add(raw)
        lo = max(0, start - _CONTEXT)
        hi = min(len(text), end + _CONTEXT)
        out.append({"raw": raw, "value": value,
                    "context": "…" + text[lo:hi].strip() + "…"})

    for m in _PCT.finditer(text):
        add(m.group(0), m.start(), m.end(), float(m.group(1)) / 100.0)
        if len(out) >= limit:
            return out
    for m in _IN_TEN.finditer(text):
        add(m.group(0), m.start(), m.end(), float(m.group(1)) / 10.0)
        if len(out) >= limit:
            return out
    m = _ODDS_WORD.search(text)
    if m:
        add(m.group(0), m.start(), m.end(), 0.5)
    return out[:limit]


# ======================================================================
# 2. Which tier a channel belongs to
# ======================================================================
# Written and rewritten against real venues. The first version said
# `official` meant "an institution or company speaking for itself", and Jev
# read that exactly as written: an exchange, an asset manager and a bank
# were all filed as officialdom, because their own websites are indeed them
# speaking for themselves. What the tier is *for* is narrower - it is the
# body whose decision the question turns on - and the criteria now say so.
TIER_CRITERIA = {
    "official": (
        "The venue belongs to the body that the question in `question` is "
        "about: the institution, agency or office that will make the "
        "decision, or whose action the question asks about. Its own site, "
        "press feed or account, or one of its officers publishing in that "
        "capacity. A company's own website is NOT official here unless that "
        "company is the subject of the question - a bank, broker, exchange "
        "or asset manager writing about someone else's decision is not."
    ),
    "pro_media": (
        "The venue is a staffed news organisation, or a working journalist "
        "publishing under an outlet's name."
    ),
    "expert": (
        "The venue is a community, publication, research desk or forum whose "
        "regular contributors work in, or formally study, the field the "
        "question is about. Commercial research from a firm that trades in "
        "or is exposed to the market belongs here, not in official."
    ),
    "crowd": (
        "The venue is a general-audience forum, aggregator, content platform "
        "or general-interest publication where anyone can take part or post."
    ),
}


def tier_state(channel: str, kind: str, samples: list[str], *,
               question: str = "", max_chars: int = 900) -> dict:
    joined = []
    used = 0
    for s in samples[:5]:
        s = s.strip().replace("\n", " ")[:180]
        if used + len(s) > max_chars:
            break
        joined.append(s)
        used += len(s)
    state = {"venue": {"name": channel, "kind": kind, "recent_items": joined}}
    if question:
        # `official` is defined relative to the question - it is the body the
        # question is about, not any body at all - so the question has to be
        # in the state for that option to mean anything.
        state["question"] = question.strip()
    return state


def tier_questions() -> dict:
    return {
        "tier": choice(
            "What kind of venue is the one named in `venue.name`? Judge the "
            "venue itself, not the opinions in `venue.recent_items`.",
            TIER_CRITERIA,
        )
    }


# ======================================================================
# 3. Which search terms are worth spending a request on
# ======================================================================
BREADTH_CRITERIA = {
    "too_broad": (
        "Most documents matching this term would be about something else "
        "entirely."
    ),
    "well_scoped": (
        "Most documents matching this term would be about the subject of the "
        "question."
    ),
    "too_narrow": (
        "The term is so specific that almost nothing would match it - an "
        "exact sentence, or a phrase people do not actually write."
    ),
}


def term_state(issue, terms: list[str]) -> dict:
    state = {
        "question": issue.question.strip(),
        "candidate_terms": list(terms),
    }
    if issue.background:
        state["background"] = issue.background.strip()
    return state


def term_questions(terms: list[str]) -> dict:
    """Two judgements per candidate term, all in one request.

    Terms are addressed by index into `candidate_terms` rather than being
    repeated, and every candidate is judged in the same call - the parallel
    fan-out the API is built for.
    """
    questions: dict[str, dict] = {}
    for i, term in enumerate(terms):
        questions[f"t{i}_on_topic"] = noul(
            f"Searching a public forum or news site for `candidate_terms[{i}]` "
            f"would mainly return documents that discuss the question in "
            f"`question`.",
            yes="The term names the subject, an actor in it, or the event the "
                "question turns on.",
            no="The term is generic, ambiguous, or names something the "
               "question does not turn on.",
        )
        questions[f"t{i}_breadth"] = choice(
            f"How well scoped is the search term `candidate_terms[{i}]` for "
            f"finding documents about the question in `question`?",
            BREADTH_CRITERIA,
        )
    return questions


# ======================================================================
# 4. Whether a mined phrase is a position someone holds
# ======================================================================
def position_state(issue, phrases: list[str]) -> dict:
    state = {
        "question": issue.question.strip(),
        "candidate_positions": list(phrases),
    }
    if issue.background:
        state["background"] = issue.background.strip()
    return state


def position_questions(phrases: list[str]) -> dict:
    questions: dict[str, dict] = {}
    for i, _ in enumerate(phrases):
        questions[f"p{i}_coherent"] = noul(
            f"`candidate_positions[{i}]` names a position, or a reason for a "
            f"position, that a person could hold about the question in "
            f"`question`.",
            yes="It states something a person could argue for or against.",
            no="It is a fragment, a topic label, boilerplate, or a phrase "
               "that asserts nothing.",
        )
    return questions


# ======================================================================
# 5. Which virtual opinion leader a document speaks for
# ======================================================================
def assign_state(issue, document: dict, *, max_chars: int = 4000) -> dict:
    body = (document.get("body") or "").strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    state = {
        "question": issue.question.strip(),
        "document": {"venue": document.get("channel", "") or "unknown",
                     "text": body},
    }
    context = (document.get("context") or "").strip()
    if context:
        state["document"]["answering"] = context[:300]
    return state


def assign_questions(leaders, *, lang: str = "zh") -> dict:
    """Pick the bloc, and separately decide whether the document joins one.

    A Choice is relative - it settles *which* of the positions fits best,
    even when none of them fits well. The Noul is absolute and can be low
    for all of them. Keeping them apart is what lets a document that argues
    something nobody else is arguing be reported as unassigned instead of
    being filed under the nearest bloc.
    """
    criteria: dict[str, Any] = {}
    for leader in leaders:
        criteria[leader.id] = {
            "position": leader.description or leader.label,
            # The short form of the position, which for a mined leader is a
            # phrase lifted out of the documents themselves.
            "examples": [leader.label],
        }
    if len(criteria) < 2:
        # Choice needs at least two options; a one-leader panel is judged by
        # the Noul alone and the Choice is skipped.
        return {"belongs": _belongs_question(leaders)}
    return {
        "leader": choice(
            "Which of these positions is the text in `document.text` arguing "
            "for? Judge by the position the text takes, not by the words it "
            "shares with an option.",
            criteria,
        ),
        "belongs": _belongs_question(leaders),
    }


def _belongs_question(leaders) -> dict:
    listed = "; ".join(f"({i + 1}) {leader.description or leader.label}"
                       for i, leader in enumerate(leaders))
    return noul(
        "The text in `document.text` argues for at least one of these "
        f"positions: {listed}",
        yes={
            "means": "The text argues for one of the listed positions.",
            "examples": [leader.label for leader in leaders],
        },
        no="The text takes no position, or argues for something that is not "
           "in the list.",
    )
