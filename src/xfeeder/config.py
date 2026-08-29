"""Configuration: issues, cohorts, weights, thresholds."""
from __future__ import annotations

import os
import pathlib
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from . import COHORTS


class StanceOption(BaseModel):
    id: str
    label: str
    label_zh: str = ""
    # Where this stance sits on the 0..1 quantity axis. Used to derive an
    # implied probability from posts that take a side without giving a number.
    anchor: Optional[float] = None
    # Only used by the offline/no-key extractor as a crude keyword prior.
    keywords: list[str] = Field(default_factory=list)


class Quantity(BaseModel):
    id: str = "probability"
    name: str = "probability"
    description: str = ""
    unit: str = "probability"        # "probability" | "percent" | "bps" | "raw"
    lo: float = 0.0
    hi: float = 1.0


class SourceQuery(BaseModel):
    """One search recipe bound to a cohort."""

    cohort: str
    query: str
    max_results: int = 100
    tag: str = ""


class Issue(BaseModel):
    id: str
    title: str
    title_zh: str = ""
    question: str                      # the exact question posed to the extractor
    background: str = ""               # context handed to the model (dates, prior)
    axis: list[StanceOption]
    quantity: Optional[Quantity] = None
    queries: list[SourceQuery] = Field(default_factory=list)
    window_hours: int = 24
    half_life_hours: float = 12.0
    output_lang: str = "zh"

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


class CohortRule(BaseModel):
    handles: list[str] = Field(default_factory=list)
    # heuristic gates, only consulted when the handle is not on a list
    min_followers: Optional[int] = None
    max_followers: Optional[int] = None
    langs: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)   # matched on bio
    # how much this cohort's voice is worth in the blended number
    blend_weight: float = 0.2
    # 0 = one author one vote (crowd); 1 = fully follower-weighted
    authority_weighting: float = 0.5
    # How much likes/RTs amplify a post. Near 0 for official sources, where
    # engagement says nothing about whether the statement is authoritative.
    engagement_weighting: float = 1.0


class Weighting(BaseModel):
    engagement_cap: float = 4.0
    author_cap_pct: float = 0.05       # no single author > 5% of a cohort
    duplicate_damping: bool = True     # near-dupe groups grow as sqrt(n), not n
    min_account_age_days: float = 30.0
    new_account_penalty: float = 0.35
    low_follower_floor: int = 5
    bot_penalty: float = 0.4


class Thresholds(BaseModel):
    cluster_similarity: float = 0.62   # cosine, within a stance bucket
    min_cluster_share: float = 0.06    # below this a cluster is not given a delegate
    max_delegates_per_cohort: int = 4
    consensus_shift_alert: float = 0.08
    divergence_alert: float = 0.20
    volume_spike_ratio: float = 2.5


class LLMConfig(BaseModel):
    extract_model: str = "claude-opus-5"
    synthesize_model: str = "claude-opus-5"
    extract_effort: str = "low"
    synthesize_effort: str = "high"
    extract_batch_size: int = 20
    max_concurrency: int = 4
    use_batch_api: bool = False
    cache_dir: str = ".xfeeder/llm-cache"
    offline: bool = False              # deterministic stub, no network
    # Server-side refusal fallback. Harmless for this workload and cheap
    # insurance; turn it off on platforms that reject the parameter
    # (Bedrock, Vertex, Foundry).
    refusal_fallback: bool = True


class EmbeddingConfig(BaseModel):
    provider: str = "hashing"          # hashing | voyage | openai
    model: str = "voyage-3.5"
    dim: int = 512


class SourceConfig(BaseModel):
    provider: str = "x_api"            # x_api | fixture
    bearer_token_env: str = "X_BEARER_TOKEN"
    fixture_path: str = ""
    exclude_retweets: bool = True
    max_pages: int = 5
    # Slide fixture timestamps so the newest post is "now". Without this a
    # shipped fixture would decay to zero weight as the file ages.
    fixture_time_shift: bool = True


class AlertConfig(BaseModel):
    webhook_url_env: str = "XFEEDER_WEBHOOK_URL"
    enabled: bool = False
    min_severity: str = "warn"


class Config(BaseModel):
    db_path: str = ".xfeeder/feeder.db"
    output_dir: str = "out"
    output_lang: str = "zh"
    cohorts: dict[str, CohortRule] = Field(default_factory=dict)
    weighting: Weighting = Field(default_factory=Weighting)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    llm: LLMConfig = Field(default_factory=LLMConfig)
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
        raw = {c: self.cohorts[c].blend_weight for c in self.cohorts if c in COHORTS}
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}


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

    for c in COHORTS:
        cfg.cohorts.setdefault(c, CohortRule())
    return cfg


def default_config_path() -> pathlib.Path:
    for candidate in ("config/config.yaml", "config.yaml", "xfeeder.yaml"):
        p = pathlib.Path(candidate)
        if p.exists():
            return p
    return pathlib.Path("config/config.yaml")
