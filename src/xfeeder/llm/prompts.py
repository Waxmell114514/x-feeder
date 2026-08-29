"""Prompts and the schemas the model must fill.

Design rule that governs every prompt in this file:

    **The model never counts.**

Shares, author counts, weights and probabilities are computed by
`pipeline/weighting.py` and handed to the model as facts. The model's only
job is to turn a measured group of posts into readable prose in that
group's own voice. Anywhere the model is asked for a number, it is a number
it read out of one post - never an aggregate.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ======================================================================
# 1. Cohort classification
# ======================================================================
class AuthorClass(BaseModel):
    index: int
    cohort: Literal["official", "pro_media", "en_kol", "cn_kol", "crowd"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class AuthorClassBatch(BaseModel):
    items: list[AuthorClass]


CLASSIFY_SYSTEM = """You sort X (Twitter) accounts into exactly one of five source tiers \
for a market-monitoring system.

Tiers:
- official: the primary institution itself or its officers speaking ex officio \
(central banks, statistical agencies, regulators, governments, listed companies' \
investor-relations accounts, sitting officials).
- pro_media: staffed news organisations and their working journalists \
(Reuters, Bloomberg, WSJ, FT, CNBC, Nikkei, Caixin, and reporters who identify \
an outlet in their bio).
- en_kol: individual commentators, analysts, traders, researchers and \
newsletter writers publishing mainly in English with a real audience.
- cn_kol: the same, publishing mainly in Chinese.
- crowd: everyone else - ordinary accounts, small accounts, anonymous \
retail participants, meme accounts.

Judgement rules:
- The tier follows the ROLE, not the follower count. A central bank with \
20k followers is still official. A pundit with 3M followers is still a KOL.
- A journalist's personal account is pro_media. A former journalist now \
writing an independent newsletter is a KOL.
- Media organisations that only aggregate and repost (headline bots, \
"Breaking" accounts with no newsroom) are crowd, not pro_media.
- Split en_kol vs cn_kol by the language the account mostly PUBLISHES in, \
not by the author's nationality.
- Under real ambiguity choose crowd and say why - a wrong promotion into \
official or pro_media corrupts the whole aggregate, a wrong demotion \
costs one vote.

Return one entry per input account, keyed by the given index."""


def classify_user_prompt(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        lines.append(
            f"[{r['index']}] @{r['handle']} | name: {r['name']!r} | "
            f"followers: {r['followers']} | verified: {r['verified']} | "
            f"account_age_days: {r.get('age_days', '?')} | "
            f"posting_lang: {r.get('lang', '?')}\n"
            f"     bio: {r['description'][:280]!r}\n"
            f"     sample post: {r.get('sample', '')[:200]!r}"
        )
    return "Classify these accounts:\n\n" + "\n".join(lines)


# ======================================================================
# 2. Stance extraction
# ======================================================================
class PostExtraction(BaseModel):
    index: int
    relevant: bool
    stance: str
    probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    horizon: Optional[str] = None
    key_claim: str = ""
    reasoning_kind: Literal[
        "data", "official_guidance", "market_pricing", "analysis", "opinion", "noise"
    ] = "opinion"
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    is_question: bool = False
    is_sarcastic: bool = False


class ExtractionBatch(BaseModel):
    items: list[PostExtraction]


def extract_system_prompt(issue, lang: str = "zh") -> str:
    axis = "\n".join(
        f'  - "{s.id}": {s.label}' + (f" ({s.label_zh})" if s.label_zh else "")
        for s in issue.axis
    )
    quantity = ""
    if issue.quantity:
        quantity = (
            f"\nQUANTITY TO EXTRACT\n{issue.quantity.name}: {issue.quantity.description}\n"
            "Record `probability` only when the post actually conveys a magnitude, "
            "as a number in 0.0-1.0. Convert freely: '60%' -> 0.6, 'coin flip' -> 0.5, "
            "'basically certain' -> 0.95, 'no way' -> 0.03, '2 in 10' -> 0.2. "
            "If the post takes a side but gives no magnitude, leave it null - the "
            "aggregator infers a level from the stance separately, and a guessed "
            "number here would be counted twice.\n"
            "A percentage in the post is USUALLY NOT this probability. Inflation "
            "rates, policy rate levels, wage growth, yields, portfolio returns and "
            "price moves are all percentages and none of them belong in this "
            "field. Record a number only where the post frames it as the "
            "likelihood of the event - 'a 38% chance', 'priced at 40%', "
            "'\u6982\u7387\u5347\u81f338%'. When in doubt leave it null.\n"
        )
    claim_lang = "Chinese" if lang == "zh" else "English"

    return f"""You read X (Twitter) posts one at a time and reduce each to its \
position on a single question. You are the measurement instrument for a \
consensus engine, so consistency matters more than insight.

THE QUESTION
{issue.question}

BACKGROUND
{issue.background or "(none)"}

STANCE AXIS - `stance` must be exactly one of these ids:
{axis}
{quantity}
HOW TO READ A POST

1. Relevance. `relevant: false` if the post does not bear on the question at \
all. Being about the same general topic is not enough - it must let you infer \
something about THIS question. Ads, giveaways, unrelated tickers, and pure \
insults are irrelevant.

2. Whose belief is it? This is the distinction that most often goes wrong.
   - The author's own view -> reasoning_kind: analysis or opinion.
   - The author reporting what markets price ("futures now imply 62%") -> \
reasoning_kind: market_pricing. Still record the number: it is the signal. \
The stance is what that number implies, not what the author personally thinks.
   - The author quoting an official ("Powell said they remain data-dependent") \
-> reasoning_kind: official_guidance.
   - The author citing a data release -> reasoning_kind: data.
   Never attribute a quoted view to the author as their own conviction.

3. Sarcasm and rhetorical questions. Mark `is_sarcastic` when the literal \
reading inverts the intended one, and record the INTENDED stance. Mark \
`is_question` for genuine questions; those usually get stance "unclear" and \
low intensity. A rhetorical question that clearly asserts a view is not a \
question - record the view.

4. Intensity, 0.0-1.0: how firmly the position is held. Hedged musing ~0.3, \
a flat assertion ~0.7, an all-caps staking-reputation call ~0.95.

5. key_claim: ONE sentence in {claim_lang} giving the position and its single \
main reason. Write it canonically - no names, no emoji, no hedging, no \
rhetorical flourish - so that two people making the same argument in different \
words produce near-identical sentences. This sentence is what gets clustered; \
style you preserve here becomes noise downstream.
   Good:  "因为核心通胀反弹，会加息。"
   Bad:   "老哥们，我跟你们说，这波稳了兄弟们🚀 绝对加息"

Return exactly one entry per input post, keyed by its index. Do not merge, \
skip, or reorder."""


def extract_user_prompt(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        meta = f"lang={r.get('lang', '?')} cohort={r.get('cohort', '?')}"
        ctx = f"\n     (reply in a thread)" if r.get("is_reply") else ""
        lines.append(f"[{r['index']}] @{r['handle']} ({meta}){ctx}\n     {r['text']}")
    return "Posts:\n\n" + "\n\n".join(lines)


# ======================================================================
# 3. Delegate synthesis - the virtual opinion leader
# ======================================================================
class DelegateDraft(BaseModel):
    name: str
    verdict: str
    rationale: list[str]
    caveat: str = ""
    quote_ids: list[str] = Field(default_factory=list)


DELEGATE_SYSTEM_TMPL = """You give a voice to a measured bloc of opinion.

A clustering step has already grouped posts that make substantially the same \
argument about one question. You receive that group together with statistics \
computed from it. Your output is the group speaking in the first person plural: \
one synthetic representative standing in for every real person in the cluster.

ABSOLUTE CONSTRAINTS
- Every statistic you are given is measured fact. Never restate a number \
differently, never round it into a different claim, never invent a count, \
share, or probability that is not in the input.
- Say only what the supplied posts say. If the cluster gives one reason, give \
one reason - do not round the argument out with plausible extra reasoning the \
posts do not contain. An honest thin delegate beats a rich invented one.
- Represent the cluster's centre of gravity, not its most vivid member. The \
loudest post in the group is not its position.
- quote_ids must be chosen from the supplied post ids, verbatim.

OUTPUT
- name: a 2-6 character/word label naming this bloc by its argument, in {lang_name}. \
It is a faction name, not a sentence. e.g. "数据派", "定价即真相", "The Hawks".
- verdict: ONE first-person-plural sentence, in {lang_name}, stating what this \
bloc believes about the question. Declarative and plain: "我们相信会加息。" \
No hedging adverbs unless the bloc itself is hedged.
- rationale: 2-4 short bullets in {lang_name}, ordered by how frequently the \
reason appears in the supplied claims - the most common reason first.
- caveat: one sentence in {lang_name} naming the evidence that would move this \
bloc off its position. Draw it from the posts where possible; leave empty if \
the posts give no hint.
- quote_ids: 1-3 ids of the posts that best exemplify the bloc's position."""


def delegate_user_prompt(*, issue, cluster_stats: dict, claims: list[str],
                         quotes: list[dict]) -> str:
    claim_block = "\n".join(f"  - {c}" for c in claims[:60])
    quote_block = "\n".join(
        f"  [{q['post_id']}] @{q['handle']} (weight {q['weight']:.2f}): {q['text']}"
        for q in quotes
    )
    prob = cluster_stats.get("mean_probability")
    prob_line = (
        f"- mean probability stated inside this cluster: {prob:.0%}\n"
        if prob is not None else
        "- no member of this cluster stated an explicit probability\n"
    )
    return f"""QUESTION
{issue.question}

THIS CLUSTER (measured, do not alter)
- source tier: {cluster_stats['cohort_label']}
- stance: {cluster_stats['stance_label']}
- {cluster_stats['n_posts']} posts from {cluster_stats['n_authors']} distinct accounts
- {cluster_stats['share']:.0%} of this tier's weighted voice
{prob_line}
THE CLAIMS MADE IN THIS CLUSTER ({len(claims)} total, deduplicated)
{claim_block}

REPRESENTATIVE POSTS
{quote_block}

Write this bloc's representative."""


# ======================================================================
# 4. Cohort headline
# ======================================================================
class CohortHeadline(BaseModel):
    headline: str


COHORT_HEADLINE_SYSTEM = """You write the single line that a monitoring \
dashboard shows for one source tier on one question.

You are given measured statistics and the tier's blocs. Write ONE sentence in \
{lang_name} that a reader can act on:
- Lead with the tier's position, not with meta-commentary.
- If the tier is split, say so and give the split ("六成认为...，其余...").
- Use only the supplied numbers, verbatim in meaning.
- No preamble, no "总的来说", no restating the question."""


def cohort_headline_prompt(*, issue, cohort_label: str, stats: dict,
                           delegates: list[dict]) -> str:
    block = "\n".join(
        f"  - {d['name']} ({d['share']:.0%} of the tier, {d['n_authors']} accounts): {d['verdict']}"
        for d in delegates
    ) or "  (no bloc reached the reporting threshold)"
    shares = ", ".join(f"{k} {v:.0%}" for k, v in stats["stance_shares"].items())
    prob = stats.get("probability")
    prob_line = f"implied probability: {prob:.0%}\n" if prob is not None else ""
    return f"""QUESTION
{issue.question}

TIER: {cohort_label}
posts: {stats['n_posts']} from {stats['n_authors']} accounts
stance shares (by weighted voice): {shares}
{prob_line}agreement: {stats['agreement']:.0%}

BLOCS
{block}

Write the one-line verdict for this tier."""


# ======================================================================
# 5. Global synthesis
# ======================================================================
class GlobalSynthesis(BaseModel):
    headline: str
    reading: list[str] = Field(default_factory=list)
    watch_for: list[str] = Field(default_factory=list)


GLOBAL_SYSTEM = """You write the top of a monitoring brief for one question, \
for a reader who will make a position decision from it.

You are given each source tier's measured verdict, the cross-tier divergences, \
and what changed since the previous snapshot. Write in {lang_name}.

- headline: one sentence. The state of the world on this question right now. \
If tiers disagree, the disagreement IS the headline.
- reading: 2-4 bullets. What the numbers mean. The most useful bullets name a \
specific gap between tiers, or a specific move since last time, and say what \
it would imply. Never average tiers into mush - the tiers are kept separate \
because official guidance and retail sentiment are different instruments.
- watch_for: 1-3 bullets. Concrete, checkable things that would change the \
picture: a named data release, a named speaker, a threshold being crossed.

Use only supplied numbers. If a tier had too little data, say so rather than \
filling the gap."""


def global_prompt(*, issue, cohort_blocks: list[str], divergences: list[str],
                  deltas: list[str], blended: Optional[float]) -> str:
    b = f"blended implied probability across tiers: {blended:.0%}\n" if blended is not None else ""
    return f"""QUESTION
{issue.question}

BACKGROUND
{issue.background or "(none)"}

{b}
TIER VERDICTS
{chr(10).join(cohort_blocks)}

CROSS-TIER DIVERGENCE
{chr(10).join(divergences) or "  (no material divergence)"}

CHANGE SINCE PREVIOUS SNAPSHOT
{chr(10).join(deltas) or "  (no previous snapshot)"}

Write the brief."""


LANG_NAME = {"zh": "Chinese", "en": "English", "ja": "Japanese"}
