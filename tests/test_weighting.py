"""The weighting rules are the system's politics; they get the most tests."""
import datetime as dt

import pytest
from conftest import NOW, make_author, make_extraction, make_post

from xfeeder.config import CohortRule, Weighting
from xfeeder.pipeline import weighting as W


@pytest.fixture
def crowd_rule():
    return CohortRule(authority_weighting=0.0, engagement_weighting=1.0)


def test_crowd_is_one_account_one_vote(crowd_rule):
    """A 40-follower account and a 400k-follower account weigh the same."""
    small = W.credibility(make_author("a", followers=40), crowd_rule, Weighting())
    huge = W.credibility(make_author("b", followers=400_000), crowd_rule, Weighting())
    assert small == pytest.approx(huge)


def test_authority_tier_does_weight_followers():
    rule = CohortRule(authority_weighting=1.0)
    small = W.credibility(make_author("a", followers=40), rule, Weighting())
    huge = W.credibility(make_author("b", followers=400_000), rule, Weighting())
    assert huge > small * 1.3


def test_new_account_is_penalised():
    rule = CohortRule(authority_weighting=0.0)
    w = Weighting()
    fresh = W.credibility(make_author("a", followers=100, age_days=3), rule, w)
    aged = W.credibility(make_author("b", followers=100, age_days=900), rule, w)
    assert fresh == pytest.approx(aged * w.new_account_penalty)


def test_engagement_is_capped():
    """A post with a million likes is worth a few posts, not a few thousand."""
    w = Weighting()
    modest = W.reach(make_post("1", "a", like=10), 1.0, w.engagement_cap)
    viral = W.reach(make_post("2", "a", like=1_000_000, retweet=250_000), 1.0,
                    w.engagement_cap)
    assert viral <= w.engagement_cap
    assert viral < modest * 4


def test_official_tier_ignores_engagement():
    w = Weighting()
    quiet = W.reach(make_post("1", "a", like=3), 0.15, w.engagement_cap)
    loud = W.reach(make_post("2", "a", like=50_000), 0.15, w.engagement_cap)
    assert loud < quiet * 1.6


def test_recency_halves_at_the_half_life():
    fresh = W.recency(make_post("1", "a", hours_ago=0), NOW, 12.0)
    old = W.recency(make_post("2", "a", hours_ago=12), NOW, 12.0)
    assert old == pytest.approx(fresh / 2, rel=1e-6)


def test_duplicate_damping_is_sublinear(crowd_rule):
    """Six identical posts count as ~sqrt(6) voices, not six."""
    kw = dict(rule=crowd_rule, weighting=Weighting(), now=NOW, half_life_hours=12.0,
              engagement_weighting=1.0)
    author = make_author("a", followers=100)
    single = W.post_weight(post=make_post("1", "a"), author=author,
                           extraction=make_extraction("1"), dup_size=1, **kw)
    duped = W.post_weight(post=make_post("2", "a"), author=author,
                          extraction=make_extraction("2"), dup_size=6, **kw)
    assert duped == pytest.approx(single / 6 ** 0.5)
    assert 6 * duped == pytest.approx(single * 6 ** 0.5)


def test_author_cap_holds_after_water_filling():
    """No account may exceed the cap - including after redistribution."""
    weights = {f"p{i}": 1.0 for i in range(20)}
    weights.update({f"flood{i}": 5.0 for i in range(10)})   # one account, 10 posts
    author_of = {f"p{i}": f"a{i}" for i in range(20)}
    author_of.update({f"flood{i}": "loud" for i in range(10)})

    capped = W.apply_author_cap(weights, author_of, 0.05)
    total = sum(capped.values())
    by_author = {}
    for pid, w in capped.items():
        by_author[author_of[pid]] = by_author.get(author_of[pid], 0.0) + w
    assert max(by_author.values()) / total <= 0.05 + 1e-6
    assert by_author["loud"] / total == pytest.approx(0.05, abs=1e-3)


def test_author_cap_is_a_noop_when_nobody_is_over():
    weights = {f"p{i}": 1.0 for i in range(50)}
    author_of = {f"p{i}": f"a{i}" for i in range(50)}
    assert W.apply_author_cap(weights, author_of, 0.05) == weights


def test_implied_probability_prefers_stated_numbers_when_they_are_dense():
    ex = {f"p{i}": make_extraction(f"p{i}", stance="hike", probability=0.4)
          for i in range(10)}
    weights = {k: 1.0 for k in ex}
    blended, explicit, from_stance, coverage = W.implied_probability(
        weights, ex, {"hike": 0.9, "hold": 0.12})
    assert coverage == pytest.approx(1.0)
    assert blended == pytest.approx(0.4)          # numbers win, not the anchor
    assert from_stance == pytest.approx(0.9)


def test_implied_probability_falls_back_to_stance_anchors():
    ex = {f"p{i}": make_extraction(f"p{i}", stance="hold") for i in range(6)}
    weights = {k: 1.0 for k in ex}
    blended, explicit, from_stance, coverage = W.implied_probability(
        weights, ex, {"hike": 0.9, "hold": 0.12})
    assert explicit is None and coverage == 0.0
    assert blended == pytest.approx(0.12)


def test_implied_probability_blends_by_coverage():
    ex = {"a": make_extraction("a", stance="hike", probability=0.5),
          "b": make_extraction("b", stance="hike")}
    weights = {"a": 1.0, "b": 1.0}
    blended, explicit, from_stance, coverage = W.implied_probability(
        weights, ex, {"hike": 0.9})
    assert coverage == pytest.approx(0.5)
    assert blended == pytest.approx(0.5 * 0.5 + 0.5 * 0.9)


def test_unanchored_stances_do_not_drag_the_estimate():
    """'unclear' has no anchor and must be excluded, not treated as zero."""
    ex = {"a": make_extraction("a", stance="hike"),
          "b": make_extraction("b", stance="unclear")}
    blended, _, _, _ = W.implied_probability({"a": 1.0, "b": 1.0}, ex, {"hike": 0.9})
    assert blended == pytest.approx(0.9)


def test_agreement_is_one_when_unanimous_and_zero_when_split():
    assert W.agreement({"hike": 1.0}) == pytest.approx(1.0)
    assert W.agreement({"hike": 0.5, "hold": 0.5}) == pytest.approx(0.0)
    assert 0.0 < W.agreement({"hike": 0.8, "hold": 0.2}) < 1.0


def test_questions_and_sarcasm_are_discounted():
    plain = make_extraction("a", intensity=0.7)
    question = make_extraction("b", intensity=0.7)
    question.is_question = True
    assert W.assertion_quality(question) < W.assertion_quality(plain)


def test_confidence_rises_with_sample_and_source_quality():
    ex = {f"p{i}": make_extraction(f"p{i}", kind="data") for i in range(40)}
    weights = {k: 1.0 for k in ex}
    strong = W.confidence(n_authors=40, agree=1.0, weights=weights, extractions=ex)

    ex2 = {"p0": make_extraction("p0", kind="noise")}
    weak = W.confidence(n_authors=1, agree=0.1, weights={"p0": 1.0}, extractions=ex2)
    assert strong > 0.9 > weak
