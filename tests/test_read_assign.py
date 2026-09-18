"""Reading a document into a position, and routing it to a bloc."""
from chorus.jev import QUESTION_VERSION
from chorus.jev.client import Response
from chorus.models import Panel
from chorus.pipeline.assign import run_assign
from chorus.pipeline.read import run_read, _to_reading
from chorus.store import Store

from conftest import leader, make_document, make_reading


def response(**answers):
    return Response({"model": "stub", "answers": answers})


def choice(pick, probs, confidence):
    return {"type": "choice", "choice": pick, "probabilities": probs,
            "confidence": confidence}


def noul(value):
    return {"type": "noul", "noul": value}


def score(value, levels=3):
    return {"type": "score", "score": value,
            "legend": {str(i): str(i) for i in range(levels)},
            "probabilities": {}, "confidence": 0.8}


def read_one(cfg, issue, doc, **answers):
    """One document through the reader, with sensible answers by default."""
    base = {
        "relevant": noul(0.9),
        "stance": choice("hike", {"hike": 0.9, "hold": 0.1}, 0.9),
        "speaks_for": choice("own_view", {"own_view": 1.0}, 0.9),
        "intensity": score(1.0),
        "sarcastic": noul(0.1),
    }
    base.update(answers)
    return _to_reading(cfg, issue, doc, response(**base))


def test_a_document_below_the_relevance_floor_does_not_count(cfg, issue):
    doc = make_document("d1", text="the weather is nice")
    assert not read_one(cfg, issue, doc, relevant=noul(0.2)).relevant


def test_a_stance_the_model_could_not_separate_is_no_stance(cfg, issue):
    """A 0.34/0.33/0.33 split is a shrug, and recording the winner would
    turn it into a vote."""
    doc = make_document("d1", text="rates")
    reading = read_one(cfg, issue, doc,
                       stance=choice("hike", {"hike": 0.34, "hold": 0.33,
                                              "cut": 0.33}, 0.02))
    assert reading.stance == "unclear"


def test_a_document_that_takes_no_side_stays_in_the_sample(cfg, issue):
    """An FOMC statement saying no decision has been made is 96% on topic
    and deliberately non-committal. Dropping it deletes the one fact the
    official-versus-public signal is built on."""
    doc = make_document("d1", text="No decision has been made about the next "
                                   "meeting; policy is data dependent.")
    reading = read_one(cfg, issue, doc, relevant=noul(0.96),
                       stance=choice("unclear", {"unclear": 0.9, "hold": 0.1}, 0.9))
    assert reading.relevant
    assert reading.stance == "unclear"


def test_a_dead_heat_at_the_top_is_no_stance_either(cfg, issue):
    """0.50 against 0.49 with the rest at zero is a concentrated
    distribution and a tie. Confidence alone does not catch it."""
    doc = make_document("d1", text="rates")
    reading = read_one(cfg, issue, doc,
                       stance=choice("hike", {"hike": 0.50, "hold": 0.49,
                                              "cut": 0.005, "unclear": 0.005},
                                     0.47))
    assert reading.stance == "unclear"


def test_a_clear_winner_survives_both_checks(cfg, issue):
    doc = make_document("d1", text="rates")
    reading = read_one(cfg, issue, doc,
                       stance=choice("hike", {"hike": 0.7, "hold": 0.3}, 0.6))
    assert reading.stance == "hike"


def test_the_full_distribution_is_kept_not_just_the_winner(cfg, issue):
    doc = make_document("d1", text="hike")
    reading = read_one(cfg, issue, doc,
                       stance=choice("hike", {"hike": 0.6, "hold": 0.4}, 0.55))
    assert reading.stance_probabilities == {"hike": 0.6, "hold": 0.4}
    assert reading.stance == "hike"


def test_news_of_the_event_is_not_a_forecast_of_it(cfg, issue):
    """"Fed hikes rates" is news about last week. Counting it as a vote for
    a hike next month is how a monitor reports 86% certainty about a
    question nobody has answered yet."""
    doc = make_document("d1", title="Fed hikes interest rates by quarter point")
    reading = read_one(cfg, issue, doc,
                       speaks_for=choice("reported_data", {"reported_data": 1.0}, 1.0),
                       stance=choice("hike", {"hike": 0.95, "hold": 0.05}, 0.95))
    assert reading.relevant                 # still in the sample, still volume
    assert reading.stance == "unclear"      # but not a forecast
    assert reading.speaks_for == "reported_data"


def test_an_unsure_guess_that_it_is_reporting_erases_nothing(cfg, issue):
    """Deleting a real position on a coin-flip provenance guess is the same
    failure the sarcasm rule refuses to make, pointed the other way."""
    doc = make_document("d1", text="We are prepared to hold rates steady for "
                                   "as long as the data warrant.")
    reading = read_one(cfg, issue, doc,
                       speaks_for=choice("reported_data",
                                         {"reported_data": 0.4, "own_view": 0.35,
                                          "official_guidance": 0.25}, 0.2),
                       stance=choice("hold", {"hold": 0.9, "hike": 0.1}, 0.9))
    assert reading.stance == "hold"


def test_a_backward_looking_issue_keeps_the_report_as_its_answer(cfg, issue):
    past = issue.model_copy(deep=True)
    past.forward_looking = False
    doc = make_document("d1", title="Fed hikes interest rates by quarter point")
    reading = read_one(cfg, past, doc,
                       speaks_for=choice("reported_data", {"reported_data": 1.0}, 1.0),
                       stance=choice("hike", {"hike": 0.95, "hold": 0.05}, 0.95))
    assert reading.stance == "hike"


def test_a_number_contradicting_its_own_side_is_dropped(cfg, issue):
    """'Definitely a hike, maybe 3%' - one of the two readings is wrong, and
    the side is the more robust of the two."""
    doc = make_document("d1", text="they will definitely hike, odds are 3%")
    reading = read_one(cfg, issue, doc,
                       stated_number=choice("n0", {"n0": 0.9}, 0.9))
    assert reading.stance == "hike"
    assert reading.stated_probability is None


def test_a_merely_low_number_survives(cfg, issue):
    doc = make_document("d1", text="I lean hike but the odds are only 35%")
    reading = read_one(cfg, issue, doc,
                       stated_number=choice("n0", {"n0": 0.9}, 0.9))
    assert reading.stated_probability == 0.35


def test_none_means_no_number_was_a_likelihood(cfg, issue):
    doc = make_document("d1", text="core inflation is 3.4% and they hike")
    reading = read_one(cfg, issue, doc,
                       stated_number=choice("none", {"none": 0.9}, 0.9))
    assert reading.stated_probability is None


def test_a_question_mark_is_seen_by_code_not_by_the_model(cfg, issue):
    doc = make_document("d1", title="Will they hike?", text="asking honestly")
    assert read_one(cfg, issue, doc).is_question


def test_sarcasm_discounts_but_never_flips(cfg, issue):
    doc = make_document("d1", text="oh sure they will hike, any day now")
    reading = read_one(cfg, issue, doc, sarcastic=noul(0.95))
    assert reading.sarcastic
    assert reading.stance == "hike"        # recorded as read, weighted down later


def test_reading_is_idempotent_and_versioned(cfg, issue, jev):
    documents = [make_document("d1", text="they will hike next week")]
    with Store(cfg.db_path) as store:
        first = run_read(cfg, store, issue, documents, jev, log=lambda *a: None)
        second = run_read(cfg, store, issue, documents, jev, log=lambda *a: None)
        assert first["new"] == 1 and second["new"] == 0 and second["reused"] == 1
        assert store.get_readings(issue.id)["d1"].reader_version == QUESTION_VERSION


# ------------------------------------------------------------- assignment
def panel_of(*leaders):
    return Panel(issue_id="fed-rate", leaders=list(leaders), origin="declared")


def test_a_document_is_only_offered_its_own_side(cfg, issue, jev):
    """A bull and a bear are never the same bloc, however similar the prose."""
    documents = [make_document("d1", text="core services inflation has not come down")]
    readings = {"d1": make_reading("d1", stance="hike")}
    panel = panel_of(leader("hike-a", "hike", "services inflation"),
                     leader("hold-a", "hold", "labour market cooling"))
    with Store(cfg.db_path) as store:
        run_assign(cfg, store, issue, documents, readings, panel, jev,
                   log=lambda *a: None)
        assignment = store.get_assignments(issue.id)["d1"]
    assert set(assignment.probabilities) <= {"hike-a"}
    assert assignment.leader_id in ("hike-a", "")


def test_a_document_whose_side_has_no_leader_is_unassigned(cfg, issue, jev):
    documents = [make_document("d1", text="they will cut rates")]
    readings = {"d1": make_reading("d1", stance="cut")}
    panel = panel_of(leader("hike-a", "hike"), leader("hike-b", "hike"))
    with Store(cfg.db_path) as store:
        info = run_assign(cfg, store, issue, documents, readings, panel, jev,
                          log=lambda *a: None)
    assert info["new"] == 0


def test_assignments_are_re_judged_when_the_panel_changes(cfg, issue, jev):
    documents = [make_document("d1", text="core services inflation has not come down")]
    readings = {"d1": make_reading("d1", stance="hike")}
    first = panel_of(leader("a", "hike", "services inflation"),
                     leader("b", "hike", "labour market"))
    second = panel_of(leader("a", "hike", "services inflation",
                             description="a completely rewritten position"),
                      leader("b", "hike", "labour market"))
    with Store(cfg.db_path) as store:
        run_assign(cfg, store, issue, documents, readings, first, jev,
                   log=lambda *a: None)
        again = run_assign(cfg, store, issue, documents, readings, first, jev,
                           log=lambda *a: None)
        assert again["new"] == 0
        changed = run_assign(cfg, store, issue, documents, readings, second, jev,
                             log=lambda *a: None)
        assert changed["new"] == 1


def test_nothing_is_assigned_without_a_panel(cfg, issue, jev):
    with Store(cfg.db_path) as store:
        info = run_assign(cfg, store, issue, [make_document("d1")],
                          {"d1": make_reading("d1")}, Panel(issue_id="fed-rate"),
                          jev, log=lambda *a: None)
    assert info["assigned"] == 0
