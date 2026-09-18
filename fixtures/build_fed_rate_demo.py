#!/usr/bin/env python3
"""Build fixtures/fed_rate_demo.jsonl.

Synthetic public documents written to exercise every mechanic in the
pipeline:

  - four tiers with genuinely different positions
  - an official tier that says less than the public hears
  - wire copy syndicated verbatim under three mastheads (duplicate damping)
  - one account posting nine times (per-speaker cap)
  - six accounts posting one identical line (astroturf damping)
  - one comment with 9,000 upvotes (engagement cap)
  - a policy rate written as "4.25-4.50%" (a number that is NOT a
    probability) next to a real "38% chance" (one that is)
  - sarcasm, a rhetorical question, and an off-topic post
  - headlines with no body (thin-document discount)

Nothing here is real. Venue names are used as plausible labels for
invented text; no quote is from any real person or outlet.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.timezone.utc)
OUT = pathlib.Path(__file__).parent / "fed_rate_demo.jsonl"

rows: list[dict] = []
_seq = [0]


def doc(source, channel, author, title, text="", *, kind="forum", hours_ago=6.0,
        score=0, comments=0, reposts=0, url="", lang=None, reply_to=""):
    _seq[0] += 1
    rows.append({
        "id": f"{source}:demo{_seq[0]:03d}",
        "source": source,
        "channel": channel,
        "channel_kind": kind,
        "author": author,
        "title": title,
        "text": text,
        "url": url or f"https://example.invalid/{source}/{_seq[0]}",
        "created_at": (NOW - dt.timedelta(hours=hours_ago)).isoformat(),
        "score": score,
        "comments": comments,
        "reposts": reposts,
        "lang": lang,
        # A comment carries its thread's title as context, exactly as the
        # Reddit collector returns it.
        "raw": {"reply_to": reply_to} if reply_to else {},
    })
    return rows[-1]["id"]


# ============================================================ official
# The institution's own feed. It says far less than everyone else hears.
doc("rss", "federalreserve.gov", "", kind="feed", hours_ago=30,
    title="FOMC statement: target range maintained at 4.25-4.50%",
    text="The Committee decided to maintain the target range for the federal "
         "funds rate at 4.25-4.50%. The Committee remains strongly committed "
         "to returning inflation to its 2 percent objective. Policy is data "
         "dependent and no decision has been made about the next meeting; the "
         "rate is unchanged for now.")
doc("rss", "federalreserve.gov", "", kind="feed", hours_ago=16,
    title="Chair's remarks on the policy outlook",
    text="We are prepared to hold rates steady for as long as the data "
         "warrant. We will act as appropriate to sustain the expansion, and we "
         "would move if the inflation picture deteriorated materially. For "
         "now the stance is data dependent.")
doc("rss", "newyorkfed.org", "", kind="feed", hours_ago=26,
    title="Survey of Consumer Expectations: one-year inflation expectations rise",
    text="Median one-year-ahead inflation expectations rose to 3.4% from 3.1%. "
         "Expectations at the three-year horizon were little changed. The "
         "survey is a measurement and carries no policy signal.")
doc("rss", "federalreserve.gov", "", kind="feed", hours_ago=9,
    title="Speech: the case for patience",
    text="With services inflation still sticky, I would not rule out another "
         "increase this year, though my base case remains that we hold rates "
         "steady while the data settle.")

# ============================================================ pro_media
# Headlines, not arguments - and the same wire story under three mastheads.
WIRE = "Fed seen holding rates steady next week, economists say"
for outlet, hours in (("reuters.com", 20), ("apnews.com", 19.5), ("wsj.com", 19)):
    doc("gnews", outlet, "", kind="outlet", hours_ago=hours, title=WIRE)

doc("gnews", "bloomberg.com", "", kind="outlet", hours_ago=14,
    title="Futures imply a 38% chance of a rate hike at the next meeting",
    text="Interest-rate futures now imply a 38% chance that the Fed raises "
         "rates at the next meeting, up from 24% a week ago, even as core "
         "inflation ran at 3.1%.")
doc("gnews", "cnbc.com", "", kind="outlet", hours_ago=11,
    title="Powell: no decision made, policy remains data dependent")
doc("gnews", "ft.com", "", kind="outlet", hours_ago=23,
    title="Hot services inflation revives talk of another hike")
doc("gnews", "marketwatch.com", "", kind="outlet", hours_ago=8,
    title="Economists split on whether the Fed will hike or hold")
doc("gnews", "reuters.com", "", kind="outlet", hours_ago=5,
    title="Jobs report keeps a September hike on the table")
doc("gnews", "apnews.com", "", kind="outlet", hours_ago=31,
    title="Fed officials signal patience as inflation cools unevenly")

# ============================================================ expert
EXPERT_THREAD = "Why would the Fed hike again when core PCE is falling?"
thread = doc("reddit", "r/AskEconomics", "econ_grad_ab", EXPERT_THREAD,
             "Genuine question. Headline PCE is drifting down. What is the "
             "argument for another increase from here?",
             hours_ago=21, score=140, comments=38)
doc("reddit", "r/AskEconomics", "macro_reader", EXPERT_THREAD,
    "The argument is that core services inflation has not come down at all. "
    "Goods disinflation did the work and it is finished. If core services "
    "inflation stays at this level they will hike, whatever the headline "
    "number does.", hours_ago=20, score=210, comments=12, reply_to=thread)
doc("reddit", "r/AskEconomics", "fx_desk_ro", EXPERT_THREAD,
    "Most of the committee has said they are data dependent and will stay put "
    "while real rates are already restrictive. The base case is that they "
    "hold rates steady, not that they hike.",
    hours_ago=20, score=185, comments=9, reply_to=thread)
doc("reddit", "r/AskEconomics", "slow_thinker_7", EXPERT_THREAD,
    "Worth separating two things: what they will do, and what they should do. "
    "Real rates are restrictive already, so I think they hold rates steady, "
    "and I think holding is right.",
    hours_ago=19, score=96, comments=4, reply_to=thread)
ri_thread = doc(
    "reddit", "r/badeconomics", "rii_poster",
    "RI: 'the Fed has to hike or inflation wins'",
    "This framing keeps coming back and it is wrong. Core services inflation "
    "is sticky but the labour market is cooling, and cooling labour markets "
    "have historically closed that gap without another increase. They will "
    "hold rates steady and be right to.", hours_ago=27, score=260, comments=41)
doc("reddit", "r/badeconomics", "macro_reader",
    "RI: 'the Fed has to hike or inflation wins'",
    "Counterpoint, and I say this as someone who was wrong last cycle: core "
    "services inflation has not come down, and every quarter we wait the "
    "expectations problem gets harder. I think they hike.",
    hours_ago=26, score=120, comments=6, reply_to=ri_thread)
doc("reddit", "r/econmonitor", "data_first_zz",
    "Core services inflation, in one chart",
    "Three-month annualised core services inflation has not come down since "
    "the spring. If you believe the Fed reacts to that series, you believe "
    "they hike.", hours_ago=34, score=155, comments=22)
doc("reddit", "r/econmonitor", "fx_desk_ro", "Labour market tracker update",
    "Quits, hires and temp help all rolling over. The labour market is "
    "cooling, which is the whole argument for why they stay put.",
    hours_ago=12, score=130, comments=15)
doc("reddit", "r/AskEconomics", "econ_grad_ab",
    "Does the 38% in futures mean anything?",
    "Futures imply a 38% chance of a hike. That is market pricing, not a "
    "forecast from anyone in particular. Treat it as a price, not a view.",
    hours_ago=7, score=88, comments=11)

# ============================================================ crowd
CROWD = [
    ("r/economics", "grocery_watch", "Inflation is still eating my paycheck",
     "Everything at the store is up again this month. Rent went up. They have "
     "to hike, whatever the official numbers say about inflation coming down.",
     18, 820, 140),
    ("r/economics", "mortgage_pain", "Anyone else refinancing into this?",
     "Credit is already tight for normal people. Another increase would break "
     "the housing market. They will hold rates steady because they have to.",
     17, 640, 95),
    ("r/economics", "just_here_man", "Core services inflation explains everything",
     "Core services inflation has not come down. That is the whole story and "
     "it is why they hike next meeting.", 16, 410, 60),
    ("r/economics", "quiet_lurker22", "The labour market is cooling",
     "Every leading indicator on jobs is rolling over. The labour market is "
     "cooling and that is why they stay put.", 15, 380, 44),
    ("r/investing", "vol_seller", "Positioning into the meeting",
     "Futures imply a 38% chance of a hike, so the pain trade is a hike. I am "
     "not taking a view, I am just saying where the market is priced.",
     13, 520, 71),
    ("r/investing", "dip_buyer_99", "They are not hiking into an election year",
     "No hike. They will stay put and talk tough. Rates unchanged, that is my "
     "call.", 12, 300, 39),
    ("r/investing", "rate_nerd", "Real rates are already restrictive",
     "By any measure policy is restrictive already. Another increase is not "
     "needed; they hold rates steady and let it work.", 11, 275, 28),
    ("r/personalfinance", "saving_for_house", "Should I lock a mortgage rate now?",
     "Trying to decide whether to lock. If they hike this gets worse, and "
     "everything I read says core services inflation has not come down.",
     10, 190, 33),
    ("r/personalfinance", "cd_ladder_guy", "CD rates if they hold",
     "If they hold rates steady the good CD rates are gone in a month. Data "
     "dependent has meant unchanged every time this year.", 9.5, 140, 18),
    ("r/news", "headline_reader", "Fed officials signal patience",
     "Officials keep saying data dependent, which has meant rates unchanged "
     "every single time this year.", 22, 260, 47),
    ("r/news", "angry_at_prices", "My insurance bill went up 20% again",
     "Nobody in Washington believes inflation is real. They should hike until "
     "prices actually stop rising.", 21, 700, 120),
    ("news.ycombinator.com", "hn_macro",
     "Ask HN: is the 38% futures number meaningful?",
     "Futures imply a 38% chance of a hike. Curious how people here read "
     "that: a forecast, or just a hedging cost?", 8, 96, 54),
    ("news.ycombinator.com", "hn_skeptic", "Fed pricing thread",
     "Every cycle people mistake market pricing for a forecast. The 38% is a "
     "price. My own guess is they hold rates steady.", 7.5, 74, 21),
]
for channel, author, title, text, hours, score, comments in CROWD:
    doc("reddit" if channel.startswith("r/") else "hackernews",
        channel, author, title, text, hours_ago=hours, score=score,
        comments=comments)

# --- one account that will not stop posting (per-speaker cap) ----------
for i in range(9):
    doc("reddit", "r/economics", "loud_poster", f"They are hiking, part {i + 1}",
        "I said it last week and I will say it again: core services inflation "
        "has not come down, so they hike. Screenshot this.",
        hours_ago=6 + i * 0.5, score=45 + i, comments=3)

# --- six accounts, one identical line (astroturf damping) --------------
ASTRO = ("THE FED WILL HIKE. PRICE IT IN. Anyone telling you they hold rates "
         "steady is selling you something.")
for i in range(6):
    doc("reddit", "r/investing", f"acct_{i}9931", "Rate call", ASTRO,
        hours_ago=4 + i * 0.2, score=12, comments=0)

# --- one genuinely viral comment (engagement cap) ----------------------
doc("reddit", "r/economics", "one_hit_wonder", "Nobody at the Fed buys groceries",
    "Has anyone on the committee looked at a receipt this year? Prices are "
    "still going up every week. They should hike.",
    hours_ago=5, score=9000, comments=1400)

# --- sarcasm, a question, and something off topic ----------------------
doc("reddit", "r/investing", "dry_humour", "Rate outlook",
    "Oh sure, they will definitely cut rates next week. Any day now. Just "
    "like they definitely cut last time, and the time before that.",
    hours_ago=6, score=210, comments=31)
doc("reddit", "r/economics", "curious_kid", "What actually happens if they hike?",
    "Serious question, I do not have a view. What changes for an ordinary "
    "person if they hike?", hours_ago=3, score=88, comments=26)
doc("reddit", "r/news", "off_topic_andy",
    "Anyone else think the new season is overrated?",
    "Three episodes in and nothing has happened. Am I watching the same show "
    "as everyone else?", hours_ago=2, score=1500, comments=300)

OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
               encoding="utf-8")
print(f"wrote {len(rows)} documents to {OUT}")
