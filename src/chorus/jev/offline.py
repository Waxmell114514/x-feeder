"""A deterministic stand-in for Jev, so the pipeline runs with no key.

`chorus demo` has to go end to end on a laptop with nothing exported, and
every stage after judgement - weighting, capping, the panel, the report,
the alerts - has to be inspectable before anyone spends anything. So this
module answers the same question shapes as the API, in the same wire
format, from nothing but the questions themselves.

It is crude, and it is meant to be obvious that it is crude. It reads the
`examples` that `questions.py` puts in the criteria and matches them as
keywords, with two refinements carried over from the previous generation of
this project because without them the demo lies:

  * a negated keyword does not count ("won't hike" is not a hike call), and
  * the longest matching phrase wins, so a specific phrase beats a
    substring of itself.

What it is *not* is a fallback for production. A run that quietly degrades
to keyword matching would report the same confident-looking numbers over a
much worse reading, so the CLI says loudly when this is what answered.
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional

STUB_MODEL = "offline-stub"

_WORD = re.compile(r"[a-z0-9']+")
_CJK = re.compile(r"[一-鿿]")
_NEG_EN = re.compile(
    r"\b(not|no|never|without|hardly|unlikely|nobody|none)\b|n't|far from",
    re.IGNORECASE,
)
_NEG_ZH = ("不", "没", "无", "别", "未", "难以")
_NEG_WINDOW = 28

_HEDGE = ("maybe", "perhaps", "not sure", "could", "might", "possibly", "if ",
          "可能", "也许", "大概", "或许", "不确定")
_STRONG = ("definitely", "certainly", "no doubt", "guaranteed", "obviously",
           "绝对", "肯定", "必然", "一定")

# Cues that decide whether a number in a document is a likelihood. The live
# model judges this from the surrounding words; offline we approximate with
# the same two lists the X-era extractor used.
_PROB_CUE = re.compile(
    r"(chance|odds|probabilit|implie[sd]|imply|priced|pricing|bets?|"
    r"coin flip|likelihood|概率|可能性|定价|赔率|胜率)", re.IGNORECASE)
_NOT_PROB = re.compile(
    r"(inflation|cpi|pce|core|wage|yield|growth|unemployment|payroll|rent|"
    r"return|share of|通胀|利率|收益率|工资|涨幅|增速)", re.IGNORECASE)


# ----------------------------------------------------------------------
def answer(state: Any, questions: dict) -> dict:
    """Answer a whole request. Same shape the API returns."""
    answers: dict[str, Any] = {}
    for key, q in questions.items():
        kind = q.get("type")
        if kind == "noul":
            answers[key] = {"type": "noul", "noul": _noul(state, q, key)}
        elif kind == "choice":
            answers[key] = _choice(state, q, key)
        elif kind == "score":
            answers[key] = _score(state, q, key)
    return {"model": STUB_MODEL, "answers": answers,
            "usage": {"input_tokens": 0, "output_tokens": 0}}


# ----------------------------------------------------------------------
def _haystack(state: Any, key: str) -> str:
    """The text this question is about.

    Questions that address one item of a list by index - the search terms,
    the candidate positions - are judged against that item; everything else
    is judged against the document.
    """
    item = _indexed_item(state, key)
    if item is not None:
        return item
    if isinstance(state, dict):
        doc = state.get("document")
        if isinstance(doc, dict):
            return str(doc.get("text", ""))
        venue = state.get("venue")
        if isinstance(venue, dict):
            return " ".join([str(venue.get("name", ""))] +
                            [str(x) for x in venue.get("recent_items", [])])
    return _flatten(state)


_INDEX_KEY = re.compile(r"^[a-z]+?(\d+)_")


def _indexed_item(state: Any, key: str) -> Optional[str]:
    m = _INDEX_KEY.match(key)
    if not m or not isinstance(state, dict):
        return None
    idx = int(m.group(1))
    for value in state.values():
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return value[idx] if idx < len(value) else ""
    return None


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) | set(_CJK.findall(text))


def _negated(text: str, at: int) -> bool:
    """Is there a negation marker in the ~28 characters before `at`?

    Word boundaries matter: "another" contains "not", "cannot" contains "no".
    """
    window = text[max(0, at - _NEG_WINDOW):at]
    return bool(_NEG_EN.search(window)) or any(n in window for n in _NEG_ZH)


def _phrase_score(haystack: str, phrases: list[str]) -> float:
    """Length-weighted keyword score, skipping negated occurrences."""
    low = haystack.lower()
    total = 0.0
    for phrase in phrases:
        if not phrase:
            continue
        hay, needle = (low, phrase.lower()) if phrase.lower() in low else (haystack, phrase)
        at = hay.find(needle)
        if at < 0 or _negated(hay, at):
            continue
        total += len(phrase)
    return total


def _criteria_phrases(value: Any) -> tuple[list[str], list[str]]:
    """-> (example phrases, prose describing the option)."""
    if value is None:
        return [], []
    if isinstance(value, str):
        return [], [value]
    if isinstance(value, list):
        return [str(v) for v in value], []
    if isinstance(value, dict):
        examples = [str(v) for v in value.get("examples", []) or []]
        prose = [str(v) for k, v in value.items() if k != "examples"]
        return examples, prose
    return [], [str(value)]


# ----------------------------------------------------------------------
def _noul(state: Any, q: dict, key: str) -> float:
    hay = _haystack(state, key)
    criteria = q.get("criteria") or {}
    yes_ex, yes_prose = _criteria_phrases(criteria.get("true"))
    no_ex, no_prose = _criteria_phrases(criteria.get("false"))

    if yes_ex or no_ex:
        yes = _phrase_score(hay, yes_ex)
        no = _phrase_score(hay, no_ex)
        if yes or no:
            return round(min(0.97, max(0.03, (yes + 1.0) / (yes + no + 2.0))), 3)

    # Nothing to match on: fall back to how much of the question's own
    # vocabulary the text uses. Deliberately unconfident either way.
    text = " ".join([str(q.get("instructions", ""))] + yes_prose + no_prose)
    return round(0.15 + 0.75 * _overlap(text, hay), 3)


def _overlap(reference: str, hay: str) -> float:
    ref = {w for w in _words(reference) if len(w) > 3 or _CJK.match(w)}
    if not ref:
        return 0.0
    hits = len(ref & _words(hay))
    return min(1.0, hits / max(4.0, min(len(ref), 14.0)))


def _choice(state: Any, q: dict, key: str) -> dict:
    hay = _haystack(state, key)
    criteria: dict = q.get("criteria") or {}
    scores: dict[str, float] = {}

    for option, spec in criteria.items():
        examples, prose = _criteria_phrases(spec)
        if option == "none":
            continue
        if "in_context" in (spec or {}) if isinstance(spec, dict) else False:
            scores[option] = _number_score(spec)
            continue
        s = _phrase_score(hay, examples) * 1.5
        s += 6.0 * _overlap(" ".join(prose) or option, hay)
        s += 2.0 * _phrase_score(hay, [option.replace("_", " ")])
        scores[option] = s

    if "none" in criteria:
        # "none" wins by default and is beaten only by a real match.
        scores["none"] = 3.0

    # With nothing to go on, the answer is "no position" if the question
    # offers one - never the option that happens to be listed first.
    if scores and max(scores.values()) <= 0.0:
        for opt in ("unclear", "none", "unknown", "other"):
            if opt in scores:
                scores[opt] = 1.0
                break

    if not scores:
        return {"type": "choice", "choice": "", "probabilities": {}, "confidence": 0.0}

    probs = _softmax(scores)
    best = max(probs, key=lambda k: probs[k])
    return {"type": "choice", "choice": best, "probabilities": probs,
            "confidence": round(_concentration(probs), 3)}


_NUMBER_WINDOW = 35


def _number_score(spec: dict) -> float:
    """Cue words near the number decide, not cue words anywhere near it.

    "CPI came in at 3.4% and futures imply a 38% chance" carries both a veto
    and a cue; which one applies depends on which number you are looking at,
    so the window is measured from the number itself.
    """
    context = str(spec.get("in_context", ""))
    raw = str(spec.get("number", ""))
    at = context.find(raw)
    if at >= 0:
        lo = max(0, at - _NUMBER_WINDOW)
        hi = min(len(context), at + len(raw) + _NUMBER_WINDOW)
        context = context[lo:hi]
    if _NOT_PROB.search(context):
        return 0.0
    return 8.0 if _PROB_CUE.search(context) else 1.0


def _score(state: Any, q: dict, key: str) -> dict:
    hay = _haystack(state, key)
    levels = list(q.get("criteria") or [])
    n = len(levels)
    if n < 2:
        return {"type": "score", "score": 0.0, "legend": {}, "probabilities": {},
                "confidence": 0.0}

    low = hay.lower()
    level = (n - 1) // 2
    if any(h in low or h in hay for h in _HEDGE):
        level = 0
    if any(s in low or s in hay for s in _STRONG):
        level = n - 1
    if hay.strip().endswith(("?", "？")):
        level = 0

    probs = {str(i): 0.1 for i in range(n)}
    probs[str(level)] = 1.0
    total = sum(probs.values())
    probs = {k: round(v / total, 3) for k, v in probs.items()}
    return {
        "type": "score",
        "score": float(level),
        "legend": {str(i): str(levels[i]) for i in range(n)},
        "probabilities": probs,
        "confidence": round(_concentration(probs), 3),
    }


def _softmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values())
    exp = {k: math.exp((v - top) / 3.0) for k, v in scores.items()}
    total = sum(exp.values()) or 1.0
    return {k: round(v / total, 4) for k, v in exp.items()}


def _concentration(probs: dict[str, float]) -> float:
    """A confidence-shaped number: 1 when the mass sits on one option."""
    vals = [v for v in probs.values() if v > 0]
    if len(vals) <= 1:
        return 1.0
    entropy = -sum(v * math.log(v) for v in vals)
    return max(0.0, min(1.0, 1.0 - entropy / math.log(len(vals))))
