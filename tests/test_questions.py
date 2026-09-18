"""The question catalogue: shapes the API accepts, and the number finder."""
from chorus.jev import questions as Q

from conftest import leader


def test_every_choice_has_at_least_two_options(issue):
    questions = Q.read_questions(issue)
    for key, q in questions.items():
        if q["type"] == "choice":
            assert len(q["criteria"]) >= 2, key
        if q["type"] == "score":
            assert 2 <= len(q["criteria"]) <= 10, key


def test_the_stance_options_are_exactly_the_issue_axis(issue):
    criteria = Q.read_questions(issue)["stance"]["criteria"]
    assert set(criteria) == set(issue.stance_ids())


def test_there_is_always_a_no_position_option(issue):
    """A closed set with no way out makes the model pick a side it did not see."""
    thin = issue.model_copy(deep=True)
    thin.axis = [o for o in issue.axis if o.id != "unclear"]
    assert "unclear" in Q.read_questions(thin)["stance"]["criteria"]


def test_the_number_question_appears_only_when_there_is_a_number(issue):
    assert "stated_number" not in Q.read_questions(issue)
    with_numbers = Q.read_questions(
        issue, number_candidates=Q.number_candidates("a 38% chance"))
    assert "stated_number" in with_numbers
    assert "none" in with_numbers["stated_number"]["criteria"]


def test_state_carries_the_question_the_background_and_the_venue(issue):
    state = Q.read_state(issue, {"channel": "r/economics", "body": "hello"})
    assert state["question"].startswith("Will the Federal Reserve")
    assert state["document"]["venue"] == "r/economics"
    assert "background" in state


def test_state_is_trimmed_so_the_question_is_not_drowned(issue):
    state = Q.read_state(issue, {"body": "x" * 9000}, max_chars=500)
    assert len(state["document"]["text"]) == 500


# ---------------------------------------------------------------- numbers
def test_percentages_are_found_with_their_context():
    found = Q.number_candidates("Futures imply a 38% chance of a hike")
    assert [c["raw"] for c in found] == ["38%"]
    assert found[0]["value"] == 0.38
    assert "imply" in found[0]["context"]


def test_odds_phrasings_are_found_too():
    assert Q.number_candidates("I'd say 7 in 10")[0]["value"] == 0.7
    assert Q.number_candidates("it's a coin flip")[0]["value"] == 0.5


def test_every_candidate_is_offered_including_the_wrong_ones():
    """Code finds candidates; judgement picks between them. A rate and a
    probability look identical to a regular expression, which is the point."""
    found = Q.number_candidates("CPI at 3.4%, futures imply a 38% chance")
    assert {c["raw"] for c in found} == {"3.4%", "38%"}


def test_numbers_outside_zero_to_one_are_not_candidates():
    assert Q.number_candidates("prices are up 140% since 2019") == []


def test_candidates_are_capped():
    text = " ".join(f"{i}%" for i in range(1, 40))
    assert len(Q.number_candidates(text)) <= 12


# ----------------------------------------------------------------- terms
def test_term_questions_address_candidates_by_index():
    questions = Q.term_questions(["fomc", "interest rates"])
    assert "t0_on_topic" in questions and "t1_breadth" in questions
    assert "candidate_terms[1]" in questions["t1_breadth"]["instructions"]


def test_all_terms_are_judged_in_one_request(issue):
    terms = [f"term {i}" for i in range(12)]
    state = Q.term_state(issue, terms)
    questions = Q.term_questions(terms)
    assert len(state["candidate_terms"]) == 12
    assert len(questions) == 24        # one noul and one choice each


# --------------------------------------------------------------- assign
def test_assignment_asks_which_and_whether_separately():
    questions = Q.assign_questions([leader("a"), leader("b")])
    assert questions["leader"]["type"] == "choice"
    assert questions["belongs"]["type"] == "noul"


def test_a_one_leader_panel_skips_the_choice():
    """Choice needs two options, and there is nothing to choose between."""
    questions = Q.assign_questions([leader("only")])
    assert set(questions) == {"belongs"}


def test_tier_criteria_cover_every_configured_tier(cfg):
    assert set(Q.TIER_CRITERIA) == set(cfg.tiers)
