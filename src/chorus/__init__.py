"""chorus - turn public opinion on an issue into a few virtual opinion leaders.

Pipeline:  plan -> ingest -> tier -> read -> panel -> assign -> report

Two outside services, and a strict division of labour between them:

  * public sources (Reddit, Hacker News, Google News, any RSS feed) supply
    the raw voices - no API key, no per-read metering;
  * Jev (TypeSafe's System One model) supplies the judgements - is this on
    topic, which side is it on, which of these opinion leaders is it
    speaking for. Jev returns typed answers with calibrated probabilities
    and never writes prose.

Everything else - counting, weighting, thresholds, and every sentence in
the report - is ordinary Python in this package.
"""

__version__ = "0.2.0"

TIERS = ("official", "pro_media", "expert", "crowd")

TIER_LABELS_ZH = {
    "official": "官方信息",
    "pro_media": "专业媒体",
    "expert": "专业社区",
    "crowd": "大众讨论",
}

TIER_LABELS_EN = {
    "official": "Official",
    "pro_media": "Professional media",
    "expert": "Expert communities",
    "crowd": "Public discussion",
}


def tier_label(tier: str, lang: str = "zh") -> str:
    table = TIER_LABELS_ZH if lang == "zh" else TIER_LABELS_EN
    return table.get(tier, tier)
