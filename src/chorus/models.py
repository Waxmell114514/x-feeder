"""Domain models.

Everything that flows between pipeline stages is one of these. They are
Pydantic models so that (a) a Jev answer validates straight into them and
(b) SQLite round-trips are just `model_dump_json`.

One shape change from the X-era version is worth naming: the unit is a
`Document`, not a tweet, and its identity as a *speaker* is the pair
(channel, author). Public sources do not hand out follower counts, so
authority is a property of the venue - r/AskEconomics, reuters.com, the
Fed's own press feed - and never of a personal audience we cannot measure.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Tier = Literal["official", "pro_media", "expert", "crowd"]


# --------------------------------------------------------------------------
# Raw material
# --------------------------------------------------------------------------
class Engagement(BaseModel):
    """What the venue tells us about reception. Different sources fill
    different fields; anything missing stays 0 and simply does not count."""

    score: int = 0          # upvotes / points
    comments: int = 0
    reposts: int = 0        # crossposts, syndications


class Document(BaseModel):
    id: str                 # "<source>:<native id>" - stable across runs
    source: str = "fixture"
    channel: str = ""       # subreddit / outlet domain / feed id: the tiering key
    channel_kind: Literal["forum", "outlet", "feed"] = "forum"
    author: str = ""        # username where the source exposes one
    title: str = ""
    text: str = ""
    url: str = ""
    created_at: dt.datetime
    engagement: Engagement = Field(default_factory=Engagement)
    lang: Optional[str] = None
    query_tag: str = ""     # which planned query pulled it in
    tier_hint: str = ""     # prior carried by that query, if any
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def body(self) -> str:
        """Title and body as one piece of text, which is what gets read."""
        title = (self.title or "").strip()
        text = (self.text or "").strip()
        if title and text:
            return f"{title}\n\n{text}"
        return title or text

    @property
    def in_reply_to(self) -> str:
        return str(self.raw.get("reply_to") or "")

    @property
    def own_text(self) -> str:
        """The words this author actually wrote.

        A comment carries its thread's title so that "yes, exactly this" is
        readable at all - but the title is someone else's sentence. Counting
        it as the commenter's own puts the thread's vocabulary into every
        reply: a thread called "Why would the Fed hike again?" makes hikers
        of everyone who answers it.
        """
        if self.in_reply_to and self.text.strip():
            return self.text.strip()
        return self.body

    @property
    def context(self) -> str:
        """The thread this document is answering, when it is answering one."""
        return self.title.strip() if self.in_reply_to else ""

    @property
    def speaker(self) -> str:
        """Who gets one vote.

        A forum account is one person across every venue on that platform -
        posting the same thing in five subreddits is one voice, not five,
        which is exactly what the per-speaker cap has to believe. An outlet
        piece is the outlet speaking, so a document with no byline falls
        back to its venue.
        """
        return f"{self.source}/{self.author}" if self.author else self.channel


# --------------------------------------------------------------------------
# Stage: who is speaking (per channel, not per account)
# --------------------------------------------------------------------------
class ChannelTier(BaseModel):
    channel: str
    tier: Tier
    method: Literal["allowlist", "query_prior", "heuristic", "jev", "manual"]
    confidence: float = 1.0
    reason: str = ""
    # The question version that produced a judged tier. A venue judged under
    # older wording is re-judged rather than trusted forever.
    version: str = ""


# --------------------------------------------------------------------------
# Stage: what each document asserts about the issue
# --------------------------------------------------------------------------
class Reading(BaseModel):
    """One document, reduced to its position on one issue.

    Every field here is a Jev answer or something computed from one. The
    probability fields are the model's own calibrated distributions, kept
    whole rather than collapsed, because the weighting stage would rather
    have the distribution than a hard label.
    """

    doc_id: str
    issue_id: str
    relevant: bool = False
    relevance: float = 0.0            # noul: P(this bears on the question)
    stance: str = "unclear"           # one of issue.axis ids
    stance_probabilities: dict[str, float] = Field(default_factory=dict)
    stance_confidence: float = 0.0
    speaks_for: Literal[
        "own_view", "market_pricing", "official_guidance", "reported_data", "unclear"
    ] = "own_view"
    intensity: float = 0.5            # 0..1, from a Score over three levels
    sarcastic: bool = False
    # A question mark is something a regular expression can see, so it is
    # never asked of the model.
    is_question: bool = False
    stated_probability: Optional[float] = None   # a number the document itself gives
    reader_version: str = ""

    # filled in later by the weighting stage
    weight: float = 0.0


# --------------------------------------------------------------------------
# Stage: the panel of virtual opinion leaders
# --------------------------------------------------------------------------
class Leader(BaseModel):
    """A virtual opinion leader: a position, defined before the documents
    are sorted into it.

    `description` is handed to Jev verbatim as the option's criteria, so it
    is written to be read literally. `evidence` records where a mined
    leader came from, so a reader can check that the machine did not invent
    a faction that nobody holds.
    """

    id: str
    label: str
    label_zh: str = ""
    stance: str = "unclear"
    description: str = ""
    flip_on: str = ""                 # what would move this bloc, if declared
    origin: Literal["declared", "mined"] = "declared"
    evidence: list[str] = Field(default_factory=list)

    def name(self, lang: str = "zh") -> str:
        return (self.label_zh or self.label) if lang == "zh" else self.label


class Panel(BaseModel):
    issue_id: str
    leaders: list[Leader] = Field(default_factory=list)
    origin: Literal["declared", "mined"] = "declared"
    built_at: Optional[dt.datetime] = None
    note: str = ""

    def by_id(self) -> dict[str, Leader]:
        return {leader.id: leader for leader in self.leaders}


class PanelAssignment(BaseModel):
    doc_id: str
    issue_id: str
    leader_id: str = ""               # "" = below the confidence floor
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float = 0.0
    panel_version: str = ""


# --------------------------------------------------------------------------
# Stage: the report
# --------------------------------------------------------------------------
class Quote(BaseModel):
    doc_id: str
    speaker: str
    channel: str = ""
    text: str
    url: str = ""
    weight: float = 0.0


class Delegate(BaseModel):
    """A virtual opinion leader, measured inside one tier.

    Every number on it was computed by `pipeline/weighting.py`; every word
    on it is either a template from `phrasing.py` or text lifted verbatim
    out of the documents. Nothing here was written by a model.
    """

    id: str
    issue_id: str
    tier: Tier
    leader_id: str
    name: str
    verdict: str
    stance: str
    rationale: list[str] = Field(default_factory=list)   # verbatim source phrases
    caveat: str = ""
    probability: Optional[float] = None
    share: float = 0.0
    weight: float = 0.0
    n_docs: int = 0
    n_speakers: int = 0
    assign_confidence: float = 0.0
    quotes: list[Quote] = Field(default_factory=list)


class TierVerdict(BaseModel):
    issue_id: str
    tier: Tier
    headline: str = ""
    stance_shares: dict[str, float] = Field(default_factory=dict)
    dominant_stance: str = "unclear"
    probability: Optional[float] = None
    probability_explicit: Optional[float] = None
    probability_from_stance: Optional[float] = None
    explicit_coverage: float = 0.0
    agreement: float = 0.0           # 0..1, 1 = one voice
    confidence: float = 0.0
    n_docs: int = 0
    n_speakers: int = 0
    n_channels: int = 0
    weight: float = 0.0
    # Two different facts, kept apart on purpose. `unassigned` is voice that
    # took a side and still fit no leader - a gap in the panel. `undecided`
    # is voice that took no side at all - a fact about the world, and not
    # something a better panel would fix.
    unassigned_share: float = 0.0
    undecided_share: float = 0.0
    delegates: list[Delegate] = Field(default_factory=list)


class Divergence(BaseModel):
    pair: tuple[str, str]
    delta: float
    note: str = ""


class Snapshot(BaseModel):
    """One full read of the public record for one issue at one time."""

    issue_id: str
    ts: dt.datetime
    window_hours: int = 24
    tiers: dict[str, TierVerdict] = Field(default_factory=dict)
    panel: list[Leader] = Field(default_factory=list)
    blended_probability: Optional[float] = None
    global_headline: str = ""
    divergences: list[Divergence] = Field(default_factory=list)
    n_docs: int = 0
    n_speakers: int = 0
    n_channels: int = 0
    sources: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class Alert(BaseModel):
    issue_id: str
    ts: dt.datetime
    kind: Literal[
        "consensus_shift", "divergence", "new_argument", "official_contradiction",
        "stance_flip", "volume_spike",
    ]
    severity: Literal["info", "warn", "critical"] = "info"
    title: str
    detail: str = ""
    evidence: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Stage: the search plan
# --------------------------------------------------------------------------
class PlannedQuery(BaseModel):
    """One concrete search against one source, bound to a tier prior."""

    source: str
    tier: str = ""
    query: str = ""                  # the keywords, in that source's syntax
    channel: str = ""                # subreddit / feed url, when the source needs one
    max_results: int = 60
    tag: str = ""
    terms: list[str] = Field(default_factory=list)
    # Source-specific knobs (sort order, whether to pull comments, ...).
    # Kept as a bag so a new source needs no change to this model.
    options: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class TermJudgement(BaseModel):
    term: str
    on_topic: float = 0.0            # noul
    breadth: str = "well_scoped"     # too_broad | well_scoped | too_narrow
    breadth_confidence: float = 0.0
    kept: bool = False
    reason: str = ""
    origin: Literal["issue", "corpus"] = "issue"


class SearchPlan(BaseModel):
    issue_id: str
    built_at: Optional[dt.datetime] = None
    terms: list[TermJudgement] = Field(default_factory=list)
    queries: list[PlannedQuery] = Field(default_factory=list)
    note: str = ""

    def kept_terms(self) -> list[str]:
        return [t.term for t in self.terms if t.kept]
