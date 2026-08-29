"""The keyword extractor is a stub, but its failure modes are the real
system's failure modes, so the two bugs it exposed are pinned here."""
import pytest
from conftest import make_post

from xfeeder.llm import offline
from xfeeder.llm.offline import _negated, _read_probability
from xfeeder.pipeline.extract import _to_extractions


def rows(*texts):
    return [{"index": i, "handle": "x", "text": t} for i, t in enumerate(texts)]


# ---- probability reading -------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("futures imply roughly a 38% chance of a hike", 0.38),
    ("swaps now price about 40% odds of an increase", 0.40),
    ("I make it about 60% they hike", 0.60),
    ("CME 显示9月加息概率已升至38%", 0.38),
    ("I'd say 2 in 10 at most", 0.2),
])
def test_reads_probabilities_that_are_framed_as_odds(text, expected):
    assert _read_probability(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [
    "Rates unchanged at 4.25-4.50%",                  # a policy rate
    "inflation expectations rose to 3.4%",            # an inflation rate
    "our nowcast puts core PCE at 2.9% annualised",   # a forecast of a rate
    "My rent went up 9% this year",                   # a price move
    "wage growth still above 4%",
])
def test_ignores_percentages_that_are_not_probabilities(text):
    assert _read_probability(text) is None


# ---- negation ------------------------------------------------------------
def test_negation_uses_word_boundaries():
    """'another' contains 'not'; 'cannot' contains 'no'. Neither negates."""
    text = "Traders boosted bets on another Fed hike after CPI"
    assert not _negated(text, text.index("hike"))

    text2 = "the Fed has never tightened into a credit contraction"
    assert _negated(text2, text2.index("tighten"))


def test_negated_keyword_flips_the_stance(issue):
    out = offline.extract(rows(
        "No hike. Full stop. The Fed has never tightened into a credit crunch.",
        "Core services inflation is re-accelerating; they hike in September.",
    ), issue)
    assert out.items[0].stance == "hold"
    assert out.items[1].stance == "hike"


def test_specific_phrase_beats_the_substring_inside_it(issue):
    """'不会加息' (hold) must outrank the '加息' (hike) inside it."""
    out = offline.extract(rows("不会加息的，美国经济已经很脆弱了。"), issue)
    assert out.items[0].stance == "hold"


def test_claims_are_canonical_enough_to_collide(issue):
    """Two people making the same argument in different words should
    produce claims that cluster together."""
    out = offline.extract(rows(
        "菜价又涨了，肯定要加息的。",
        "Everything I buy costs more every month. Rate hike incoming.",
    ), issue)
    a, b = out.items
    assert a.stance == b.stance == "hike"
    assert a.key_claim.split("（")[0] == b.key_claim.split("（")[0]


# ---- the coherence gate --------------------------------------------------
def test_incoherent_probability_is_dropped(issue):
    """A post arguing for a hike cannot also put the odds of one at 3%."""
    class Item:
        index, relevant, stance, probability = 0, True, "hike", 0.03
        horizon, key_claim, reasoning_kind = None, "x", "opinion"
        intensity, is_question, is_sarcastic = 0.5, False, False

    class Batch:
        items = [Item()]

    out = _to_extractions(Batch(), [make_post("p1", "a")], issue)
    assert out[0].stance == "hike"
    assert out[0].probability is None


def test_a_merely_low_probability_survives(issue):
    """'I lean hike but it is only 35%' is a real position, not an error."""
    class Item:
        index, relevant, stance, probability = 0, True, "hike", 0.35
        horizon, key_claim, reasoning_kind = None, "x", "market_pricing"
        intensity, is_question, is_sarcastic = 0.5, False, False

    class Batch:
        items = [Item()]

    out = _to_extractions(Batch(), [make_post("p1", "a")], issue)
    assert out[0].probability == pytest.approx(0.35)


def test_missing_entries_become_irrelevant_rather_than_crashing(issue):
    class Batch:
        items = []

    out = _to_extractions(Batch(), [make_post("p1", "a"), make_post("p2", "b")], issue)
    assert len(out) == 2
    assert all(not e.relevant for e in out)
