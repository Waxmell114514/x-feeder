"""Configuration: issues, tiers, weights, thresholds.

Two rules of thumb this file is built around.

  * Everything a reviewer might argue with should be here, not in code.
    Anchors, caps, blend weights, and every Jev threshold are data.
  * The wording of the questions handed to Jev lives in `jev/questions.py`,
    and the numbers those answers are compared against live in `jev:` below.
    Those two places are the whole surface where judgement enters.
"""
from __future__ import annotations

import os
import pathlib
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from . import TIERS


class StanceOption(BaseModel):
    id: str
    label: str
    label_zh: str = ""
    # Where this stance sits on the 0..1 quantity axis. Used to derive an
    # implied reading from documents that take a side without giving a number.
    anchor: Optional[float] = None
    # Handed to Jev as the option's criteria, read literally, so write it as
    # the condition itself rather than as a hint.
    description: str = ""
    # Only used by the offline (no-key) reader as a crude keyword prior.
    keywords: list[str] = Field(default_factory=list)


class Quantity(BaseModel):
    id: str = "probability"
    name: str = "probability"
    description: str = ""
    unit: str = "probability"
    lo: float = 0.0
    hi: float = 1.0


class LeaderSpec(BaseModel):
    """A virtual opinion leader declared by hand in the issue file.

    Declaring the panel is the stable way to run this over time: snapshots
    are only comparable when the blocs keep their identity between runs.
    `chorus panel --write` exists to turn a mined panel into exactly this.
    """

    id: str
    label: str
    label_zh: str = ""
    stance: str = "unclear"
    description: str = ""
    flip_on: str = ""


# ----------------------------------------------------------------------
# Where to look
# ----------------------------------------------------------------------
class RedditPlan(BaseModel):
    enabled: bool = True
    # tier -> subreddits searched for this issue. Naming a subreddit here
    # both tells the planner where to look and pins the tier of what it finds.
    subreddits: dict[str, list[str]] = Field(default_factory=dict)
    search_all: bool = True            # also run a site-wide search
    sort: str = "new"                  # new | relevance | top | comments
    time: str = "week"                 # hour | day | week | month | year | all
    max_results: int = 60
    include_comments: bool = False     # top-level comments on matching threads


class HackerNewsPlan(BaseModel):
    enabled: bool = False
    include_comments: bool = True
    min_points: int = 0
    max_results: int = 60
    tier: str = "crowd"


class GoogleNewsPlan(BaseModel):
    enabled: bool = True
    hl: str = "en-US"
    gl: str = "US"
    ceid: str = "US:en"
    max_results: int = 60
    # Google News returns headlines, not article bodies, and it indexes far
    # more than newsrooms: press releases, broker blogs, trade sheets,
    # user-contributed investing sites. Pinning everything it returns to
    # `pro_media` would hand a tier named "professional media" to whoever
    # got indexed. Left empty, each new outlet is placed by the allowlist,
    # then by domain heuristics, then by one Jev call that is cached for
    # that domain forever.
    default_tier: str = ""


class V2exPlan(BaseModel):
    """V2EX, via the public sov2ex full-text index.

    The Chinese-language crowd source, and close to the only open one left:
    Weibo, Zhihu and Tieba have no public search, and the RSS bridges that
    stood in for them have closed. It is one forum of one profession, which
    makes it a real Chinese crowd rather than the Chinese crowd - fine for a
    question about software work, useless for one about mortgages.
    """

    enabled: bool = False
    node: str = ""                     # restrict to one V2EX node
    max_results: int = 50
    tier: str = "crowd"


class FeedPlan(BaseModel):
    """One RSS/Atom feed, polled whole and filtered locally.

    This is the escape hatch that makes the system work outside English:
    point it at any publisher's feed - including Chinese outlets, or an
    RSSHub route - and the rest of the pipeline is unchanged.
    """

    url: str
    tier: str = "pro_media"
    channel: str = ""                  # defaults to the feed's host
    match_terms: bool = True           # keep only entries matching a planned term
    max_results: int = 40


class IssueSources(BaseModel):
    reddit: RedditPlan = Field(default_factory=RedditPlan)
    hackernews: HackerNewsPlan = Field(default_factory=HackerNewsPlan)
    gnews: GoogleNewsPlan = Field(default_factory=GoogleNewsPlan)
    v2ex: V2exPlan = Field(default_factory=V2exPlan)
    feeds: list[FeedPlan] = Field(default_factory=list)


class Issue(BaseModel):
    id: str
    title: str
    title_zh: str = ""
    question: str                      # the exact question Jev is asked
    background: str = ""               # context carried in the state
    axis: list[StanceOption]
    quantity: Optional[Quantity] = None
    # Seed vocabulary. The planner expands and judges these; it does not
    # need them to be good, only to be about the right subject.
    terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)   # names that anchor a search
    panel: list[LeaderSpec] = Field(default_factory=list)
    sources: IssueSources = Field(default_factory=IssueSources)
    window_hours: int = 48
    half_life_hours: float = 24.0
    output_lang: str = "zh"
    # Is this a question about something that has not happened yet?
    #
    # It matters more than it looks. Ask "will the Fed raise rates at the
    # next meeting" the day after a meeting, and most of what you collect
    # is coverage of the rise that just happened. Those documents are
    # relevant, and they are not forecasts - counting "Fed hikes rates" as
    # a vote for a future hike is how a monitor reports 86% certainty about
    # an event nobody has an opinion on yet. When this is true, a document
    # that Jev judged to be relaying an event rather than taking a side is
    # recorded as taking no side, which is what its own criteria say.
    #
    # Set it false for a question about the past ("did they raise in
    # September?"), where a report of the event is the answer.
    forward_looking: bool = True

    def stance_ids(self) -> list[str]:
        return [s.id for s in self.axis]

    def anchors(self) -> dict[str, float]:
        return {s.id: s.anchor for s in self.axis if s.anchor is not None}

    def stance_label(self, sid: str, lang: str = "zh") -> str:
        for s in self.axis:
            if s.id == sid:
                if lang == "zh" and s.label_zh:
                    return s.label_zh
                return s.label
        return sid


# ----------------------------------------------------------------------
# How much each voice is worth
# ----------------------------------------------------------------------
class TierRule(BaseModel):
    # Subreddits and domains that pin this tier. This is the only thing that
    # should ever put a channel in `official`: inference can be wrong, an
    # institution's own domain cannot.
    channels: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)   # matched on channel name/description
    blend_weight: float = 0.25
    # How much upvotes/comments amplify a document inside this tier. Near 0
    # for official sources, where reception says nothing about authority.
    engagement_weighting: float = 1.0
    # Optional per-channel multipliers inside the tier. Absent means 1.0,
    # i.e. one venue one weight - the public-source analogue of one person
    # one vote, and the default on purpose.
    channel_authority: dict[str, float] = Field(default_factory=dict)


class Weighting(BaseModel):
    engagement_cap: float = 4.0
    speaker_cap_pct: float = 0.05      # no single account > 5% of a tier
    channel_cap_pct: float = 0.35      # no single venue > 35% of a tier
    duplicate_damping: bool = True     # syndicated/copied text grows as sqrt(n)
    thin_document_chars: int = 140     # headline-only items carry less
    thin_document_penalty: float = 0.6


class Thresholds(BaseModel):
    panel_size: int = 6                # leaders a mined panel may propose
    min_leader_share: float = 0.06     # below this a leader is not reported
    max_delegates_per_tier: int = 4
    phrase_similarity: float = 0.58    # merging mined candidate phrases
    min_phrase_docs: int = 2           # a phrase must appear in this many docs
    # How much of a tier has to have taken a side before its number means
    # anything. Below this the tier is reported as volume without a reading:
    # "90%" computed from the 18% who committed reads as the tier's view of
    # the world, and it is not.
    min_decided_share: float = 0.25
    consensus_shift_alert: float = 0.08
    divergence_alert: float = 0.20
    volume_spike_ratio: float = 2.5


class JevConfig(BaseModel):
    """Jev (TypeSafe System One) settings, including every threshold that a
    model answer is compared against. Review this block together with
    `jev/questions.py`; between them they are the whole judgement surface."""

    model: str = "jev-latest"
    api_key_env: str = "TYPESAFE_API_KEY"
    base_url: str = "https://api.typesafe.ai/v1"
    max_concurrency: int = 8
    timeout: float = 30.0
    retries: int = 4
    cache_dir: str = ".chorus/jev-cache"
    offline: bool = False              # deterministic stub, no network
    # Accuracy falls as state grows with material the question does not need,
    # so documents are truncated before they are sent.
    max_state_chars: int = 4000
    # --- thresholds -------------------------------------------------------
    relevance_floor: float = 0.55      # noul: below this the document is dropped
    stance_confidence_floor: float = 0.30
    # How far ahead the leading side has to be. Confidence measures how
    # concentrated a distribution is, which is not the same question: on a
    # four-option axis, two options at 0.50/0.49 and two at zero is a
    # concentrated distribution and a dead heat.
    stance_margin_floor: float = 0.15
    assign_confidence_floor: float = 0.45   # below this a document joins no bloc
    tier_confidence_floor: float = 0.50     # below this an unknown channel -> crowd
    # How sure the model has to be that a document is *reporting* before
    # that erases its side on a forward-looking question. An uncertain guess
    # here would delete a real position, which is the same failure the
    # sarcasm rule refuses to make.
    provenance_confidence_floor: float = 0.50
    term_on_topic_floor: float = 0.60
    sarcasm_floor: float = 0.65
    price_in_usd_per_mtok: float = 0.042


class EmbeddingConfig(BaseModel):
    provider: str = "hashing"          # hashing | voyage | openai
    model: str = "voyage-3.5"
    dim: int = 512


class SourceConfig(BaseModel):
    provider: str = "public"           # public | fixture
    fixture_path: str = ""
    fixture_time_shift: bool = True
    # Reddit and Google News both ask for a descriptive agent string and
    # throttle anonymous traffic; say who you are.
    user_agent: str = "chorus/0.2 (public-opinion research; +https://github.com/)"
    # Reddit blocks anonymous JSON reads from datacenter address ranges
    # outright - an HTML block page, not a rate-limit response, whatever
    # User-Agent you send. From a server you need app-only credentials:
    # make a "script" app at https://www.reddit.com/prefs/apps and export
    # these two. From a home connection the anonymous path still works.
    reddit_client_id_env: str = "REDDIT_CLIENT_ID"
    reddit_client_secret_env: str = "REDDIT_CLIENT_SECRET"
    request_timeout: float = 20.0
    max_pages: int = 3
    per_run_document_budget: int = 1200


class AlertConfig(BaseModel):
    webhook_url_env: str = "CHORUS_WEBHOOK_URL"
    enabled: bool = False
    min_severity: str = "warn"


class Config(BaseModel):
    db_path: str = ".chorus/chorus.db"
    output_dir: str = "out"
    output_lang: str = "zh"
    tiers: dict[str, TierRule] = Field(default_factory=dict)
    weighting: Weighting = Field(default_factory=Weighting)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    jev: JevConfig = Field(default_factory=JevConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    issues: dict[str, Issue] = Field(default_factory=dict)

    def issue(self, issue_id: str) -> Issue:
        if issue_id not in self.issues:
            known = ", ".join(sorted(self.issues)) or "(none loaded)"
            raise KeyError(f"unknown issue {issue_id!r}; loaded issues: {known}")
        return self.issues[issue_id]

    def blend_weights(self) -> dict[str, float]:
        raw = {t: self.tiers[t].blend_weight for t in self.tiers if t in TIERS}
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}

    def channel_table(self) -> dict[str, str]:
        """channel -> tier, from the allowlists. Lower-cased, @/r/ stripped."""
        table: dict[str, str] = {}
        for name, rule in self.tiers.items():
            for channel in rule.channels:
                table[normalise_channel(channel)] = name
        return table


def normalise_channel(channel: str) -> str:
    c = (channel or "").strip().lower()
    for prefix in ("/r/", "r/", "https://", "http://", "www."):
        if c.startswith(prefix):
            c = c[len(prefix):]
    return c.rstrip("/")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str | pathlib.Path) -> Config:
    """Load config.yaml plus every issue file it points at."""
    path = pathlib.Path(path)
    data = _expand_env(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    issue_globs = data.pop("issue_files", ["issues/*.yaml"])
    base = path.parent

    issues: dict[str, Issue] = {}
    for pattern in issue_globs:
        for f in sorted(base.glob(pattern)):
            doc = _expand_env(yaml.safe_load(f.read_text(encoding="utf-8")) or {})
            for item in doc.get("issues", [doc]):
                if not item:
                    continue
                issue = Issue.model_validate(item)
                issues[issue.id] = issue

    data["issues"] = {**issues, **data.get("issues", {})}
    cfg = Config.model_validate(data)

    for t in TIERS:
        cfg.tiers.setdefault(t, TierRule())
    return cfg


def default_config_path() -> pathlib.Path:
    for candidate in ("config/config.yaml", "config.yaml", "chorus.yaml"):
        p = pathlib.Path(candidate)
        if p.exists():
            return p
    return pathlib.Path("config/config.yaml")
