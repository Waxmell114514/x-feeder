"""Turning a question into the strings that get typed into a search box."""
from chorus.models import TermJudgement
from chorus.pipeline import plan as P


def origins(candidates):
    return {term.lower(): origin for term, origin in candidates}


def test_the_issue_files_own_terms_are_candidates(issue):
    terms = {t.lower() for t, _ in P.candidate_terms(issue)}
    assert "fomc meeting" in terms
    assert "federal reserve" in terms


def test_names_in_the_question_become_candidates(issue):
    """A question mentioning an institution should search for it even when
    nobody listed it."""
    bare = issue.model_copy(deep=True)
    bare.terms, bare.entities = [], []
    terms = {t.lower() for t, _ in P.candidate_terms(bare)}
    assert "federal reserve" in terms or "fomc" in terms


def test_stance_vocabulary_is_marked_as_such(issue):
    table = origins(P.candidate_terms(issue))
    assert table.get("hike") == "stance:hike"
    assert table.get("hold") == "stance:hold"


def test_corpus_terms_are_mined_from_the_documents(issue):
    corpus = ["core services inflation has not come down"] * 3
    table = origins(P.candidate_terms(issue, corpus))
    assert any(origin == "corpus" for origin in table.values())


def test_a_capitalised_function_word_is_not_a_search_term(issue):
    """"Will hiring fall in 2027?" starts with a capital that is not a name."""
    q = issue.model_copy(deep=True)
    q.terms, q.entities = [], []
    q.question = "Will hiring in software engineering fall in 2027?"
    q.background = ""
    terms = {t.lower() for t, _ in P.candidate_terms(q)}
    assert "will" not in terms


def test_terms_are_deduplicated(issue):
    doubled = issue.model_copy(deep=True)
    doubled.terms = ["inflation", "Inflation", "inflation"]
    terms = [t for t, _ in P.candidate_terms(doubled)]
    assert len([t for t in terms if t.lower() == "inflation"]) == 1


# ------------------------------------------------------- the balance rule
def judged(term, kept=True, on_topic=0.9):
    return TermJudgement(term=term, on_topic=on_topic, kept=kept)


def test_one_sided_vocabulary_is_dropped_rather_than_searched(issue):
    """Searching 'rate hike' and not 'on hold' finds a crowd that believes
    in rate hikes. Either every side contributes terms, or none does."""
    judgements = [judged("hike"), judged("raise rates"), judged("fomc")]
    origins_map = {"hike": "stance:hike", "raise rates": "stance:hike",
                   "fomc": "issue"}
    out = P.balance_stance_terms(judgements, origins_map, issue)
    kept = {j.term for j in out if j.kept}
    assert kept == {"fomc"}
    assert "balanced" in next(j for j in out if j.term == "hike").reason


def test_balanced_vocabulary_survives_in_equal_measure(issue):
    judgements = [judged("hike", on_topic=0.95), judged("raise rates", on_topic=0.8),
                  judged("hold", on_topic=0.9), judged("rate cut", on_topic=0.7)]
    origins_map = {"hike": "stance:hike", "raise rates": "stance:hike",
                   "hold": "stance:hold", "rate cut": "stance:cut"}
    out = P.balance_stance_terms(judgements, origins_map, issue)
    kept = {j.term for j in out if j.kept}
    assert kept == {"hike", "hold", "rate cut"}      # one each, the best one


# ------------------------------------------------------------- queries
def test_a_subreddit_named_under_a_tier_is_searched_and_pinned(issue):
    queries = P.build_queries(None, issue, ["fomc", "interest rates"])
    subreddit_queries = {q.channel: q.tier for q in queries if q.source == "reddit"}
    assert subreddit_queries.get("AskEconomics") == "expert"
    assert subreddit_queries.get("economics") == "crowd"


def test_a_site_wide_search_pins_nothing(issue):
    queries = P.build_queries(None, issue, ["fomc"])
    wide = [q for q in queries if q.source == "reddit" and not q.channel]
    assert wide and wide[0].tier == ""


def test_feeds_carry_the_terms_for_local_filtering(issue):
    queries = P.build_queries(None, issue, ["fomc", "interest rates"])
    feeds = [q for q in queries if q.source == "rss"]
    assert feeds and feeds[0].tier == "official"
    assert feeds[0].terms == ["fomc", "interest rates"]


def test_google_news_gets_a_window_it_understands(issue):
    queries = P.build_queries(None, issue, ["fomc"])
    news = next(q for q in queries if q.source == "gnews")
    assert "when:2d" in news.query          # a 48h window


def test_terms_are_or_joined_and_phrases_are_quoted(issue):
    queries = P.build_queries(None, issue, ["fomc", "interest rates"])
    reddit = next(q for q in queries if q.source == "reddit")
    assert reddit.query == '(fomc OR "interest rates")'


def test_a_single_term_needs_no_parentheses(issue):
    queries = P.build_queries(None, issue, ["fomc"])
    assert next(q for q in queries if q.source == "reddit").query == "fomc"


def test_query_tags_are_stable_across_processes(issue):
    """The tag is the identity of a query in the store; a salted hash would
    silently re-collect everything on every run."""
    first = [q.tag for q in P.build_queries(None, issue, ["fomc"])]
    second = [q.tag for q in P.build_queries(None, issue, ["fomc"])]
    assert first == second
    assert all(t.startswith("fed-rate:") for t in first)


def test_a_disabled_source_produces_no_queries(issue):
    quiet = issue.model_copy(deep=True)
    quiet.sources.reddit.enabled = False
    quiet.sources.gnews.enabled = False
    quiet.sources.hackernews.enabled = False
    quiet.sources.feeds = []
    assert P.build_queries(None, quiet, ["fomc"]) == []


# ------------------------------------------------------------ end to end
def test_a_plan_without_a_judge_keeps_what_a_human_wrote(cfg, issue, jev):
    plan = P.build_plan(cfg, issue, jev, log=lambda *a: None)
    assert plan.queries
    kept = plan.kept_terms()
    assert "fomc meeting" in {t.lower() for t in kept}
    assert all("stand-in" in t.reason or "balanced" in t.reason
               for t in plan.terms)
