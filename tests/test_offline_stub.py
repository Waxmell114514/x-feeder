"""The keyword stand-in. It has to be crude, but it must not be wrong."""
from chorus.jev import offline as O
from chorus.jev import questions as Q


def choice(text, criteria, key="q"):
    questions = {key: Q.choice("which side", criteria)}
    return O.answer({"document": {"text": text}}, questions)["answers"][key]


STANCES = {
    "hike": {"means": "raise rates", "examples": ["hike", "raise rates"]},
    "hold": {"means": "leave unchanged",
             "examples": ["hold", "no hike", "unchanged", "hold rates steady"]},
    "unclear": {"means": "no position"},
}


def test_a_matching_phrase_wins():
    assert choice("they will hike next week", STANCES)["choice"] == "hike"


def test_a_negated_keyword_does_not_count():
    """'will not hike' is not a hike call, and reading it as one flips a tier."""
    answer = choice("they will not hike next week", STANCES)
    assert answer["choice"] != "hike"
    assert answer["choice"] == "unclear"        # no position, not the first option


def test_the_longest_matching_phrase_wins():
    """'hold rates steady' contains 'hold'; the specific phrase must win."""
    answer = choice("they will hold rates steady", STANCES)
    assert answer["choice"] == "hold"


def test_nothing_matching_leaves_the_answer_unconfident():
    answer = choice("the weather is nice today", STANCES)
    assert answer["confidence"] < 0.5


def test_probabilities_sum_to_one():
    answer = choice("they will hike", STANCES)
    assert abs(sum(answer["probabilities"].values()) - 1.0) < 1e-3


# ---------------------------------------------------------------- numbers
def number_pick(text):
    candidates = Q.number_candidates(text)
    questions = {"stated_number": Q.choice("which is the likelihood",
                                           Q._number_criteria(candidates))}
    answer = O.answer({"document": {"text": text}}, questions)["answers"]["stated_number"]
    if answer["choice"] == "none":
        return None
    return candidates[int(answer["choice"][1:])]["raw"]


def test_a_probability_next_to_a_cue_word_is_picked():
    assert number_pick("futures imply a 38% chance of a hike") == "38%"


def test_a_policy_rate_is_not_a_probability():
    """The bug that destroyed the official tier's reading in the X-era build."""
    assert number_pick("the target range is unchanged at 4.25-4.50%") is None


def test_the_cue_is_measured_from_the_number_not_the_sentence():
    text = "CPI came in at 3.4% and futures now imply a 38% chance of a hike"
    assert number_pick(text) == "38%"


def test_none_wins_when_no_number_is_a_likelihood():
    assert number_pick("inflation ran at 3.1% and wages at 4.2%") is None


# ------------------------------------------------------------------ score
def score(text):
    questions = {"intensity": Q.score("how firm", Q.INTENSITY_LEVELS)}
    return O.answer({"document": {"text": text}}, questions)["answers"]["intensity"]


def test_hedging_scores_low_and_certainty_scores_high():
    assert score("maybe they hike, not sure")["score"] < score("they hike")["score"]
    assert score("they will definitely hike")["score"] > score("they hike")["score"]


def test_a_question_is_not_a_firm_position():
    assert score("will they hike?")["score"] == 0.0


def test_legend_is_returned_so_the_scale_is_not_assumed():
    assert len(score("they hike")["legend"]) == len(Q.INTENSITY_LEVELS)


# ------------------------------------------------------------------ noul
def test_indexed_questions_are_judged_against_their_own_item():
    state = {"question": "will the fed hike", "candidate_terms": ["fomc", "zzz"]}
    answers = O.answer(state, Q.term_questions(["fomc", "zzz"]))["answers"]
    assert answers["t0_on_topic"]["type"] == "noul"
    assert answers["t1_breadth"]["type"] == "choice"
