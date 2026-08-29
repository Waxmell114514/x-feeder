"""Domain models.

Everything that flows between pipeline stages is one of these. They are
Pydantic models so that (a) LLM structured output validates straight into
them and (b) SQLite round-trips are just `model_dump_json`.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Cohort = Literal["official", "pro_media", "en_kol", "cn_kol", "crowd"]


# --------------------------------------------------------------------------
# Raw material
# --------------------------------------------------------------------------
class Author(BaseModel):
    id: str
    handle: str
    name: str = ""
    followers: int = 0
    following: int = 0
    verified: bool = False
    description: str = ""
    created_at: Optional[dt.datetime] = None
    lang_hint: Optional[str] = None

    @property
    def account_age_days(self) -> Optional[float]:
        if self.created_at is None:
            return None
        now = dt.datetime.now(dt.timezone.utc)
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
        return (now - created).total_seconds() / 86400.0


class Metrics(BaseModel):
    like: int = 0
    retweet: int = 0
    reply: int = 0
    quote: int = 0
    impression: int = 0
    bookmark: int = 0


class Post(BaseModel):
    id: str
    platform: str = "x"
    author_id: str
    text: str
    lang: Optional[str] = None
    created_at: dt.datetime
    metrics: Metrics = Field(default_factory=Metrics)
    url: str = ""
    is_reply: bool = False
    is_quote: bool = False
    is_retweet: bool = False
    conversation_id: Optional[str] = None
    referenced_id: Optional[str] = None
    query_tag: str = ""  # which configured query pulled it in
    raw: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Stage 2: who is speaking
# --------------------------------------------------------------------------
class CohortAssignment(BaseModel):
    author_id: str
    cohort: Cohort
    method: Literal["allowlist", "heuristic", "llm", "manual"]
    confidence: float = 1.0
    reason: str = ""


# --------------------------------------------------------------------------
# Stage 3: what each post asserts about the issue
# --------------------------------------------------------------------------
class Extraction(BaseModel):
    """One post, reduced to its position on one issue."""

    post_id: str
    issue_id: str
    relevant: bool
    stance: str = "unclear"          # one of issue.axis ids
    probability: Optional[float] = None   # explicit number if the author gave one
    horizon: Optional[str] = None    # "next meeting" / "2026H2" / free text
    key_claim: str = ""              # the assertion, normalised to one sentence
    reasoning_kind: Literal[
        "data", "official_guidance", "market_pricing", "analysis", "opinion", "noise"
    ] = "opinion"
    intensity: float = 0.5           # 0..1 how strongly held
    is_question: bool = False
    is_sarcastic: bool = False
    extractor_version: str = ""

    # filled in later by the weighting stage
    weight: float = 0.0


# --------------------------------------------------------------------------
# Stage 4/5: the synthesis
# --------------------------------------------------------------------------
class Quote(BaseModel):
    post_id: str
    handle: str
    text: str
    url: str = ""
    weight: float = 0.0


class OpinionCluster(BaseModel):
    """A group of posts making substantially the same argument."""

    id: str
    issue_id: str
    cohort: Cohort
    stance: str
    post_ids: list[str] = Field(default_factory=list)
    n_posts: int = 0
    n_authors: int = 0
    weight: float = 0.0
    share: float = 0.0               # of the cohort's total weight
    exemplar_claims: list[str] = Field(default_factory=list)
    top_quotes: list[Quote] = Field(default_factory=list)
    mean_probability: Optional[float] = None


class Delegate(BaseModel):
    """A synthetic opinion leader: one cluster, given a voice.

    This is the 'virtual representative' of the design - it speaks for a
    measured share of a cohort, and every number on it was computed, not
    written by a model.
    """

    id: str
    issue_id: str
    cohort: Cohort
    name: str                        # e.g. "数据派" / "The Hawks"
    verdict: str                     # the one-line assertion: "我们相信会加息"
    stance: str
    rationale: list[str] = Field(default_factory=list)   # ranked reasons
    caveat: str = ""                 # what would change their mind
    probability: Optional[float] = None
    share: float = 0.0
    weight: float = 0.0
    n_posts: int = 0
    n_authors: int = 0
    quotes: list[Quote] = Field(default_factory=list)
    cluster_id: str = ""


class CohortVerdict(BaseModel):
    issue_id: str
    cohort: Cohort
    headline: str = ""               # one line for the whole cohort
    stance_shares: dict[str, float] = Field(default_factory=dict)
    dominant_stance: str = "unclear"
    probability: Optional[float] = None
    probability_explicit: Optional[float] = None
    probability_from_stance: Optional[float] = None
    explicit_coverage: float = 0.0   # weight share of posts that gave a number
    agreement: float = 0.0           # 0..1, 1 = unanimous
    confidence: float = 0.0
    n_posts: int = 0
    n_authors: int = 0
    weight: float = 0.0
    delegates: list[Delegate] = Field(default_factory=list)


class Divergence(BaseModel):
    pair: tuple[str, str]
    delta: float
    note: str = ""


class Snapshot(BaseModel):
    """One full read of the world for one issue at one time."""

    issue_id: str
    ts: dt.datetime
    window_hours: int = 24
    cohorts: dict[str, CohortVerdict] = Field(default_factory=dict)
    blended_probability: Optional[float] = None
    global_headline: str = ""
    divergences: list[Divergence] = Field(default_factory=list)
    n_posts: int = 0
    n_authors: int = 0
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
