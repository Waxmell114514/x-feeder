#!/usr/bin/env python3
"""Build fixtures/fed_rate_demo.jsonl.

Synthetic posts written to exercise every mechanic in the pipeline:
  - five tiers with genuinely different positions
  - an official tier that says less than the crowd hears
  - media reporting market pricing rather than an opinion
  - a duplicated astroturf line in the crowd tier (damping)
  - one viral crowd post (engagement cap)
  - one brand-new low-follower account (new-account penalty)
  - sarcasm and a rhetorical question
  - one account posting many times (per-author cap)

No real accounts, no real quotes. Handles ending in `_demo` are invented;
institutional handles are used only as plausible labels for synthetic text.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

NOW = dt.datetime(2026, 8, 29, 12, 0, tzinfo=dt.timezone.utc)
OUT = pathlib.Path(__file__).parent / "fed_rate_demo.jsonl"

rows: list[dict] = []
_next = [700000000000000000]


def post(handle, name, followers, text, *, cohort, tag, hours_ago=3.0, like=0,
         retweet=0, reply=0, quote=0, bio="", lang=None, verified=False,
         author_age_days=2200, is_reply=False, author_id=None):
    _next[0] += 137
    rows.append({
        "id": str(_next[0]),
        "author_id": author_id or f"u_{handle.lower()}",
        "handle": handle,
        "name": name,
        "followers": followers,
        "verified": verified,
        "description": bio,
        "author_created_at": (NOW - dt.timedelta(days=author_age_days)).isoformat(),
        "created_at": (NOW - dt.timedelta(hours=hours_ago)).isoformat(),
        "text": text,
        "lang": lang,
        "like": like, "retweet": retweet, "reply": reply, "quote": quote,
        "cohort_hint": cohort,
        "query_tag": tag,
        "is_reply": is_reply,
    })


# ---------------------------------------------------------------- official
OFF = dict(cohort="official", tag="fed-rate:official", verified=True)
post("federalreserve", "Federal Reserve", 1_900_000,
     "The Committee remains strongly committed to returning inflation to its 2 percent "
     "objective. Policy is data dependent; no decision has been made about the September "
     "meeting. Rates unchanged at 4.25-4.50%.",
     hours_ago=9, like=2400, retweet=1100, bio="Official account of the Federal Reserve Board.", **OFF)
post("federalreserve", "Federal Reserve", 1_900_000,
     "Chair's remarks: we will act as appropriate to sustain the expansion. We are "
     "prepared to hold rates steady for as long as the data warrant, and to move if the "
     "inflation picture deteriorates.",
     hours_ago=8, like=1800, retweet=900, bio="Official account of the Federal Reserve Board.", **OFF)
post("nyfed", "New York Fed", 420_000,
     "Survey of Consumer Expectations: one-year-ahead inflation expectations rose to 3.4% "
     "from 3.1%. Median expectations remain above the pre-pandemic average.",
     hours_ago=11, like=620, retweet=310, bio="The Federal Reserve Bank of New York. Official account.", **OFF)
post("stlouisfed", "St. Louis Fed", 310_000,
     "President's speech: with core services inflation still sticky, I would not rule out "
     "further tightening this year. I am not yet convinced we are on a sustainable path to 2%.",
     hours_ago=6, like=980, retweet=540, bio="Federal Reserve Bank of St. Louis official account.", **OFF)
post("sffed", "San Francisco Fed", 240_000,
     "New research note: the labour market has normalised faster than headline payrolls "
     "suggest. On our measures, wage pressure is no longer a source of upside inflation risk.",
     hours_ago=13, like=410, retweet=190, bio="Federal Reserve Bank of San Francisco. Official.", **OFF)

# ---------------------------------------------------------------- pro_media
MED = dict(cohort="pro_media", tag="fed-rate:media", verified=True)
post("Reuters", "Reuters", 26_000_000,
     "Fed funds futures now imply roughly a 38% chance of a rate hike at the September "
     "meeting, up from 24% before this morning's CPI print, according to CME data.",
     hours_ago=5, like=3100, retweet=1900, quote=420,
     bio="Top news from Reuters. The global news organisation.", **MED)
post("business", "Bloomberg", 8_400_000,
     "Traders boosted bets on another Fed hike after core CPI came in hotter than "
     "forecast. Swaps now price about 40% odds of a September increase.",
     hours_ago=5, like=2200, retweet=1400, bio="The first word in business news. Bloomberg.", **MED)
post("NickTimiraos", "Nick Timiraos", 480_000,
     "Officials are unlikely to have made up their minds. The internal debate is between "
     "holding through year-end and one more insurance hike; nobody senior is arguing for cuts.",
     hours_ago=4, like=5400, retweet=2100, quote=610,
     bio="Chief economics correspondent, The Wall Street Journal. Covering the Fed.", **MED)
post("economics", "Bloomberg Economics", 610_000,
     "Our nowcast puts core PCE at 2.9% annualised over the last three months. That is "
     "above the level consistent with a hold, but not high enough on its own to force a hike.",
     hours_ago=7, like=890, retweet=430, bio="Bloomberg Economics. Data and analysis.", **MED)
post("Caixin", "Caixin Global", 260_000,
     "美联储9月议息前瞻：市场定价加息概率升至四成，但多数受访经济学家仍预计按兵不动。",
     hours_ago=6, like=740, retweet=380, lang="zh",
     bio="财新传媒旗下国际财经媒体，记者报道全球市场。", **MED)
post("Reuters", "Reuters", 26_000_000,
     "ANALYSIS: Economists polled by Reuters overwhelmingly expect the Fed to hold in "
     "September. 58 of 71 respondents see no change; 11 see a 25bp hike.",
     hours_ago=3, like=1600, retweet=980,
     bio="Top news from Reuters. The global news organisation.", **MED)

# ---------------------------------------------------------------- en_kol
EK = dict(cohort="en_kol", tag="fed-rate:en_kol")
post("biancoresearch", "Jim Bianco (demo)", 340_000,
     "Core services ex-housing has now accelerated three months running. The market is "
     "badly underpricing this. They hike in September and the long end sells off.",
     hours_ago=4, like=4100, retweet=1300, quote=290, verified=True,
     bio="Macro research. Rates, credit, and why the consensus is wrong.", **EK)
post("MacroAlf_demo", "Macro Alf (demo)", 290_000,
     "No hike. Full stop. Bank credit growth is negative in real terms, and the Fed has "
     "never tightened into a credit contraction of this size. They hold and they wait.",
     hours_ago=6, like=3600, retweet=1100, verified=True,
     bio="Ex-rates trader. Writing about macro and liquidity.", **EK)
post("RatesTrader_demo", "Rates Desk (demo)", 88_000,
     "Positioning matters more than the CPI print here. Everyone is short front-end. "
     "One hot number does not get you a hike, it gets you a hawkish hold.",
     hours_ago=3, like=1400, retweet=380,
     bio="Front-end rates. Opinions my own, not my desk's.", **EK)
post("Ninja_Economics", "Ninja Economics (demo)", 210_000,
     "The inflation data has re-accelerated and the labour market never broke. I make it "
     "about 60% they hike, and I think the committee is more hawkish than the dots imply.",
     hours_ago=2, like=2700, retweet=760, verified=True,
     bio="Economics, charts, and occasional sarcasm.", **EK)
post("LizAnnSonders_demo", "Liz Ann (demo)", 420_000,
     "Watch the breadth of the inflation data, not the headline. On a trimmed-mean basis "
     "the picture is still disinflationary. That argues for holding rates steady.",
     hours_ago=5, like=1900, retweet=520, verified=True,
     bio="Chief investment strategist. Markets, macro, and mean reversion.", **EK)
post("biancoresearch", "Jim Bianco (demo)", 340_000,
     "To be explicit about the number: I'd put September at 65%. The market's 38% is a "
     "gift if you think inflation is re-accelerating.",
     hours_ago=3.5, like=2200, retweet=640, verified=True,
     bio="Macro research. Rates, credit, and why the consensus is wrong.", **EK)
post("HedgeFundGuy_demo", "Buyside (demo)", 61_000,
     "They are not hiking six weeks before the data revisions land. Hold, hawkish "
     "statement, and everybody pretends they saw it coming.",
     hours_ago=7, like=890, retweet=210, bio="PM. Macro. Long volatility, short narratives.", **EK)

# ---------------------------------------------------------------- cn_kol
CK = dict(cohort="cn_kol", tag="fed-rate:cn_kol", lang="zh")
post("hongguan_laowang", "宏观老王", 180_000,
     "核心通胀连续三个月反弹，就业也没塌，我倾向于认为9月会加息一次。市场现在只定价四成，"
     "明显低估了鲍威尔的鹰派程度。",
     hours_ago=4, like=3200, retweet=880, verified=True,
     bio="宏观研究，利率与汇率。前卖方分析师。", **CK)
post("zhaisheng_note", "债生笔记", 96_000,
     "别被单月CPI带节奏。美国信贷增速已经是负的，这个位置加息等于自找麻烦。我的判断是按兵不动，"
     "但声明会写得很鹰。",
     hours_ago=5, like=2100, retweet=520,
     bio="固定收益。写给自己看的笔记，不构成建议。", **CK)
post("meilian_watch", "美联储观察", 240_000,
     "CME FedWatch 显示9月加息概率已升至38%。历史上这个位置继续往上走的次数不多，但这次通胀结构"
     "确实更黏。我给50%。",
     hours_ago=3, like=4100, retweet=1200, verified=True,
     bio="每天更新美联储动态与市场定价。", **CK)
post("huaerjie_jiaoyuan", "华尔街交易员", 130_000,
     "加息。核心服务通胀没下来，工资增速还在4%以上，这种组合下按兵不动才是异常。",
     hours_ago=6, like=1800, retweet=460,
     bio="十年交易经验，主做美股和美债。", **CK)
post("hongguan_laowang", "宏观老王", 180_000,
     "补充一句：如果9月不加，12月大概率要加，区别只是时间。政策路径的终点没变。",
     hours_ago=3.2, like=1100, retweet=290, verified=True,
     bio="宏观研究，利率与汇率。前卖方分析师。", **CK)
post("licai_xiaobai_kol", "理财小白说", 52_000,
     "问一个问题：现在到底是加息还是降息？大家怎么看？",
     hours_ago=8, like=340, retweet=40, bio="分享理财常识。", **CK)

# ---------------------------------------------------------------- crowd
CR_EN = dict(cohort="crowd", tag="fed-rate:crowd_en")
CR_ZH = dict(cohort="crowd", tag="fed-rate:crowd_zh", lang="zh")

crowd_hike_en = [
    "Groceries are up again this month. Of course they're going to hike, they have no choice.",
    "CPI hot, jobs fine, they hike. This isn't complicated.",
    "My rent went up 9% this year. Tell me again how inflation is beaten. They're hiking.",
    "Everything I buy costs more every single month. Rate hike incoming, obviously.",
    "The Fed will hike in September. Powell has been telegraphing it for weeks.",
    "Insurance, food, electricity all up. Hike is coming whether the market likes it or not.",
    "They're going to raise rates and everyone acting shocked hasn't looked at a receipt lately.",
    "hike. 100%. inflation never actually went away it just moved into services",
]
crowd_hold_en = [
    "No hike. The economy is way weaker than the headline numbers say.",
    "They'll hold. Small businesses are already struggling to refinance anything.",
    "There's no way they raise rates into an election-year slowdown. Hold.",
    "People forget that the last three meetings were holds. Nothing has changed enough.",
    "They will hold and talk tough. Same as every meeting this year.",
]
crowd_cut_en = [
    "Honestly they should be cutting. Housing is frozen and nobody can afford a mortgage.",
    "Cut. The lags haven't even fully hit yet.",
]
crowd_hike_zh = [
    "菜价又涨了，肯定要加息的，别做梦了。",
    "美国通胀根本没下去，我觉得会加息。",
    "身边搞外贸的都说美元还要贵，加息没跑了。",
    "加息吧，反正我又没有美元资产（苦笑）",
    "看了下数据，核心通胀反弹这么明显，不加息说不过去。",
    "这次真的会加息，上次也是这么说的然后真的加了。",
]
crowd_hold_zh = [
    "不会加息的，美国经济已经很脆弱了。",
    "我赌按兵不动，鲍威尔没那个胆子。",
    "维持不变，然后嘴上说得很凶，年年如此。",
]

t = 1.0
for i, txt in enumerate(crowd_hike_en):
    post(f"user_en_{i}", f"anon{i}", 120 + i * 37, txt, hours_ago=t + i * 0.4,
         like=6 + i * 3, retweet=i, bio="", **CR_EN)
for i, txt in enumerate(crowd_hold_en):
    post(f"user_en_h{i}", f"anon_h{i}", 340 + i * 90, txt, hours_ago=2 + i * 0.6,
         like=11 + i * 4, retweet=1, bio="", **CR_EN)
for i, txt in enumerate(crowd_cut_en):
    post(f"user_en_c{i}", f"anon_c{i}", 88 + i * 20, txt, hours_ago=4 + i,
         like=5, bio="", **CR_EN)
for i, txt in enumerate(crowd_hike_zh):
    post(f"user_zh_{i}", f"网友{i}", 200 + i * 60, txt, hours_ago=1.5 + i * 0.5,
         like=9 + i * 5, retweet=i, bio="", **CR_ZH)
for i, txt in enumerate(crowd_hold_zh):
    post(f"user_zh_h{i}", f"网友h{i}", 410 + i * 70, txt, hours_ago=3 + i * 0.7,
         like=14, bio="", **CR_ZH)

# --- one viral crowd post: engagement cap must stop it dominating the tier
post("viral_guy_demo", "Some Guy", 8_400,
     "The Fed is going to hike and the entire market is positioned the wrong way. "
     "Screenshot this.",
     hours_ago=2, like=91_000, retweet=24_000, quote=3_100, **CR_EN)

# --- astroturf: the identical line from six accounts, several brand new
for i in range(6):
    post(f"promo_{i}", f"Signals{i}", 40 + i * 5,
         "BREAKING: Fed to hike rates in September. Join our channel for the full "
         "analysis before the market moves.",
         hours_ago=2.5 + i * 0.1, like=2, retweet=1,
         author_age_days=9 if i < 4 else 400, **CR_EN)

# --- one account flooding the tier: the per-author cap must bite
for i in range(9):
    post("loud_bear_demo", "Loud Bear", 3_200,
         f"They are NOT hiking. Thread {i + 1}/9 on why the credit data makes a "
         f"September increase impossible.",
         hours_ago=1.2 + i * 0.15, like=40 + i, retweet=3, **CR_EN)

# --- sarcasm and a rhetorical question
post("dry_wit_demo", "Dry Wit", 2_100,
     "Sure, they'll definitely cut rates with core running at 3%. Any day now. 🙄",
     hours_ago=2.2, like=310, retweet=44, **CR_EN)
post("just_asking_demo", "Curious", 640,
     "Does anyone actually know if they're hiking or is everyone just guessing?",
     hours_ago=3.3, like=22, **CR_EN)

OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
               encoding="utf-8")
print(f"wrote {len(rows)} posts -> {OUT}")
