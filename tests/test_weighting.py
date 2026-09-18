"""The arithmetic that decides how much each voice is worth."""
import datetime as dt

from chorus.config import TierRule, Weighting
from chorus.pipeline import weighting as W

from conftest import NOW, make_document, make_reading


CROWD = TierRule(engagement_weighting=1.0)
OFFICIAL = TierRule(engagement_weighting=0.1)
WEIGHTING = Weighting()


def weigh(doc, reading, rule=CROWD, **kw):
    return W.document_weight(
        doc=doc, reading=reading, rule=rule, weighting=kw.pop("weighting", WEIGHTING),
        now=NOW, half_life_hours=24.0, **kw)


def test_engagement_weighting_comes_from_the_tier_and_nowhere_else():
    """There is no way to weigh a document against one tier's rule with
    another tier's engagement setting."""
    import inspect
    assert "engagement_weighting" not in inspect.signature(
        W.document_weight).parameters


def test_every_venue_counts_the_same_unless_the_config_says_otherwise():
    rule = TierRule()
    a = make_document("1", channel="r/economics")
    b = make_document("2", channel="r/investing")
    assert W.venue_authority(a, rule) == W.venue_authority(b, rule) == 1.0


def test_a_configured_venue_multiplier_applies_to_subdomains():
    rule = TierRule(channel_authority={"reuters.com": 1.5})
    assert W.venue_authority(make_document("1", channel="reuters.com"), rule) == 1.5
    assert W.venue_authority(make_document("2", channel="feeds.reuters.com"),
                             rule) == 1.5
    assert W.venue_authority(make_document("3", channel="notreuters.com"), rule) == 1.0


def test_engagement_is_capped():
    quiet = make_document("1", score=2, text="a" * 200)
    viral = make_document("2", score=90_000, comments=20_000, text="a" * 200)
    r = make_reading("1")
    ratio = weigh(viral, r) / weigh(quiet, r)
    assert 1.0 < ratio <= WEIGHTING.engagement_cap


def test_official_tier_all_but_ignores_engagement():
    quiet = make_document("1", score=2, text="a" * 200)
    loud = make_document("2", score=50_000, text="a" * 200)
    r = make_reading("1")
    assert weigh(loud, r, OFFICIAL) / weigh(quiet, r, OFFICIAL) < 1.6


def test_recency_halves_at_the_half_life():
    fresh = make_document("1", hours_ago=0, text="a" * 200)
    old = make_document("2", hours_ago=24, text="a" * 200)
    r = make_reading("1")
    assert abs(weigh(old, r) / weigh(fresh, r) - 0.5) < 0.01


def test_duplicate_damping_is_sublinear():
    doc = make_document("1", text="a" * 200)
    r = make_reading("1")
    single = weigh(doc, r, dup_size=1)
    of_nine = weigh(doc, r, dup_size=9) * 9
    assert of_nine < 9 * single
    assert abs(of_nine - 3 * single) < 1e-9        # 9/sqrt(9) = 3


def test_a_headline_is_worth_less_than_an_argument():
    headline = make_document("1", title="Fed seen holding rates steady", text="")
    argued = make_document("2", text="x" * 400)
    r = make_reading("1")
    assert weigh(headline, r) < weigh(argued, r)


def test_questions_and_sarcasm_are_discounted_but_never_flipped():
    plain = make_reading("1")
    asking = make_reading("1", is_question=True)
    ironic = make_reading("1", sarcastic=True)
    assert W.assertion_quality(asking) < W.assertion_quality(plain)
    assert W.assertion_quality(ironic) < W.assertion_quality(plain)
    assert ironic.stance == plain.stance      # discounted, not inverted


def test_an_unsure_stance_is_worth_less_than_a_sure_one():
    sure = make_reading("1", confidence=0.95)
    unsure = make_reading("1", confidence=0.31)
    assert W.assertion_quality(unsure) < W.assertion_quality(sure)


# ---------------------------------------------------------------- caps
def test_speaker_cap_holds_after_water_filling():
    weights = {f"d{i}": 1.0 for i in range(40)}
    weights["flood"] = 20.0
    speaker_of = {**{f"d{i}": f"u{i}" for i in range(40)}, "flood": "u0"}
    before = 21.0 / 60.0
    capped = W.apply_cap(weights, lambda d: speaker_of[d], 0.05)

    total = sum(capped.values())
    by_speaker = {}
    for doc_id, w in capped.items():
        by_speaker[speaker_of[doc_id]] = by_speaker.get(speaker_of[doc_id], 0) + w
    assert before > 0.34
    assert max(by_speaker.values()) / total <= 0.0501


def test_an_impossible_cap_settles_on_an_equal_split_not_on_zero():
    """Four speakers cannot each hold 5%. Chasing that shrinks every weight
    towards zero, so the cap floors at 1/holders instead."""
    weights = {"a": 10.0, "b": 1.0, "c": 1.0, "d": 1.0}
    capped = W.apply_cap(weights, lambda d: d, 0.05)
    total = sum(capped.values())
    assert total > 0
    assert abs(max(capped.values()) / total - 0.25) < 0.01


def test_channel_cap_holds_too():
    weights = {f"d{i}": 1.0 for i in range(20)}
    channel_of = {f"d{i}": ("r/loud" if i < 15 else "r/quiet") for i in range(20)}
    channel_of["d19"] = "r/third"
    capped = W.apply_cap(weights, lambda d: channel_of[d], 0.35)
    total = sum(capped.values())
    loud = sum(w for d, w in capped.items() if channel_of[d] == "r/loud")
    assert 0.74 < 15 / 20                       # 75% before the cap
    assert loud / total <= 0.3501


def test_a_cap_is_a_noop_when_nobody_is_over():
    weights = {f"d{i}": 1.0 for i in range(40)}
    capped = W.apply_cap(weights, lambda d: d, 0.05)
    assert capped == weights


# -------------------------------------------------------- the readings
def test_implied_probability_prefers_stated_numbers_when_they_are_dense():
    weights = {"a": 1.0, "b": 1.0}
    readings = {"a": make_reading("a", stated=0.4), "b": make_reading("b", stated=0.6)}
    blended, explicit, from_stance, coverage = W.implied_probability(
        weights, readings, {"hike": 0.9})
    assert coverage == 1.0
    assert abs(explicit - 0.5) < 1e-9
    assert abs(blended - 0.5) < 1e-9


def test_implied_probability_falls_back_to_the_anchors():
    weights = {"a": 1.0, "b": 1.0}
    readings = {"a": make_reading("a", stance="hike"),
                "b": make_reading("b", stance="hold")}
    blended, explicit, from_stance, coverage = W.implied_probability(
        weights, readings, {"hike": 0.9, "hold": 0.1})
    assert explicit is None and coverage == 0.0
    assert abs(from_stance - 0.5) < 1e-9


def test_unanchored_stances_do_not_drag_the_estimate():
    weights = {"a": 1.0, "b": 1.0}
    readings = {"a": make_reading("a", stance="hike"),
                "b": make_reading("b", stance="unclear")}
    _, _, from_stance, _ = W.implied_probability(weights, readings, {"hike": 0.9})
    assert abs(from_stance - 0.9) < 1e-9        # not 0.45


def test_implied_probability_blends_by_coverage():
    weights = {"a": 1.0, "b": 3.0}
    readings = {"a": make_reading("a", stance="hike", stated=1.0),
                "b": make_reading("b", stance="hike")}
    blended, explicit, from_stance, coverage = W.implied_probability(
        weights, readings, {"hike": 0.5})
    assert abs(coverage - 0.25) < 1e-9
    assert abs(blended - (0.25 * 1.0 + 0.75 * 0.5)) < 1e-9


def test_agreement_is_one_when_unanimous_and_zero_when_split():
    assert W.agreement({"hike": 1.0}) == 1.0
    assert abs(W.agreement({"hike": 0.5, "hold": 0.5})) < 1e-9


def test_agreement_ignores_the_people_who_said_nothing():
    """Everyone who spoke said the same thing. That is unanimous, however
    many stayed quiet - how many stayed quiet is a different number."""
    anchors = {"hike": 0.9, "hold": 0.1}
    shares = {"hold": 0.3, "unclear": 0.7}
    assert W.agreement(shares) < 0.2               # conflated
    assert W.agreement(shares, anchors) == 1.0     # measured over sides


def test_agreement_is_zero_when_nobody_took_a_side():
    assert W.agreement({"unclear": 1.0}, {"hike": 0.9}) == 0.0


def test_confidence_rises_with_sample_spread_and_provenance():
    weights = {"a": 1.0}
    opinion = {"a": make_reading("a", speaks_for="own_view")}
    official = {"a": make_reading("a", speaks_for="official_guidance")}
    thin = W.confidence(n_speakers=2, n_channels=1, agree=0.5, weights=weights,
                        readings=opinion)
    broad = W.confidence(n_speakers=40, n_channels=8, agree=0.5, weights=weights,
                         readings=opinion)
    sourced = W.confidence(n_speakers=2, n_channels=1, agree=0.5, weights=weights,
                           readings=official)
    assert broad > thin
    assert sourced > thin
