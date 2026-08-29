# xfeeder

把 X（Twitter）上关于某个议题的所有声音，合成为**几个虚拟意见领袖**和**一句论断**，
按信源层级分开呈现，并在共识移动或层级分歧时告警。

灵感来自《超新星纪元》里的量子计算机民主：全体畅所欲言，机器理解所有人的意图，
最后合成为少数几个能大致代表所有人的声音。

设计取舍与理由见 **[DESIGN.md](DESIGN.md)**——那是这个项目的主要内容，代码是它的实现。

```
                 ┌── 官方信息   ──┐
                 ├── 专业媒体   ──┤
X / 查询  ──▶    ├── 英语区 KOL ──┤ ──▶ 加权 ──▶ 论点聚类 ──▶ 虚拟意见领袖 ──▶ 分歧 / 告警
                 ├── 中文区 KOL ──┤
                 └── 大众用户   ──┘
```

---

## 30 秒看到效果（不需要任何 key）

```bash
pip install -e .
xfeeder demo
```

用内置的 66 条示例数据跑完整条管线，输出终端报告 + 自包含 HTML。
离线模式下**所有统计是真的**，只有句子是模板套出来的。

真实运行：

```bash
export X_BEARER_TOKEN=...        # X API v2，app-only bearer
export ANTHROPIC_API_KEY=...
xfeeder init                     # 生成 config/
# 编辑 config/config.yaml：把你关注的账号填进各层白名单
xfeeder run --issue fed-rate --html
```

---

## 一次输出长什么样

```
美联储下次会议加息概率            41%  综合隐含概率（跨层级加权）
2026-08-29 10:09 UTC · 58 条发言 · 47 个账号 · 窗口 24h

层级          隐含                     发言/账号   一致度   置信
大众用户       53%  ██████████·······     38/30      18%    59%
英语区 KOL     51%  █████████········      7/6       17%    30%
中文区 KOL     67%  ████████████·····      5/5       28%    32%
专业媒体       58%  ██████████·······      6/5       11%    30%
官方信息       12%  ██···············      2/1      100%    49%

大众用户  54% 的加权声量倾向「加息」（30 个账号），隐含概率 53%。
  加息派·通胀   (24% 的声量 · 6 条 / 6 账号)
    → 我们相信会加息。
      · 日常开支持续上涨，通胀并未真正回落
      · 官方口径与体感物价脱节
    反转条件：核心 PCE 环比连续两个月回到 0.2% 以下
    @user_zh_0：菜价又涨了，肯定要加息的…
  按兵不动派·信贷 (22% 的声量 · 6 条 / 6 账号)
    → 我们相信不会加息。
      ...

信号
  ● 大众读数高于官方指引 41%（53% vs 12%）      ← 最值得看的一条
  ● 中文区 KOL 比 英语区 KOL 高 16%（67% vs 51%）
```

每个"虚拟意见领袖"背后是一个真实的帖子簇：份额、账号数、引用原帖全部可追溯。
**所有数字由算术算出，模型只负责把它们写成句子。**

---

## 命令

| 命令 | 作用 |
|---|---|
| `xfeeder demo` | 内置数据跑通全流程，无需 key |
| `xfeeder init` | 生成 `config/` 脚手架 |
| `xfeeder run --issue X` | 完整循环：抓取 → 分层 → 抽取 → 合成 → 报告 |
| `xfeeder ingest --issue X` | 只抓取 |
| `xfeeder extract --issue X` | 只重读已存帖子（`--force` 全量重抽） |
| `xfeeder synthesize --issue X` | 只重新合成（不花抓取额度） |
| `xfeeder report --issue X --html` | 重渲染最近一次快照，不花任何钱 |
| `xfeeder watch --issue X --interval 1800` | 循环运行，变化时告警 |
| `xfeeder issues` / `xfeeder stats` | 查看配置与库存 |

全局开关：`--config <path>`、`--offline`（完全不调模型）。

每个阶段都能单独跑，所以一次失败只损失一个阶段，不是整轮。

---

## 定义一个议题

议题就是一个**能从单条帖子独立判断**的问题。
"美联储 9 月会不会加息"是好问题；"经济现在怎么样"不是。

```yaml
issues:
  - id: fed-rate
    title_zh: "美联储下次会议加息概率"
    question: >
      Will the FOMC raise the federal funds target range at its next meeting?
    background: >
      当前 4.25-4.50%，过去三次会议均为按兵不动，核心 PCE 高于目标。
    window_hours: 24
    half_life_hours: 12          # 12 小时前的发言只算半票

    axis:
      - {id: hike, label_zh: "加息",     anchor: 0.90}
      - {id: hold, label_zh: "按兵不动", anchor: 0.12}
      - {id: cut,  label_zh: "降息",     anchor: 0.02}
      - {id: unclear, label_zh: "态度不明"}      # 无 anchor：计算时排除

    quantity:
      name: "P(hike at next meeting)"
      description: 发言者赋予"下次会议加息"的概率

    queries:
      - {cohort: official,  query: "from:federalreserve OR from:nyfed"}
      - {cohort: pro_media, query: "(from:Reuters OR from:NickTimiraos) (fed OR fomc)"}
      - {cohort: en_kol,    query: "(FOMC OR \"rate hike\") (min_faves:150) lang:en"}
      - {cohort: cn_kol,    query: "(美联储 OR 加息 OR 议息) (min_faves:50) lang:zh"}
      - {cohort: crowd,     query: "(FOMC OR \"the fed\") lang:en -min_faves:150"}
```

`anchor` 是"该立场在 0-1 概率轴上的位置"，用于在**没人报数字**时从立场分布反推读数。
它是先验判断，不是测量——改它会改变结果，这是有意暴露出来的旋钮。

要监控别的东西，复制这个文件改问题和查询即可：财报超预期概率、某个法案能否通过、
某条公链的分叉方案能否获得共识……只要问题能被单条帖子回答。

---

## 分层规则

由便宜到贵，先命中先返回：

1. **白名单**（`config.yaml` 里的 handles）——免费、精确。
   **这是唯一能把账号放进 `official` 的途径**：推断可以出错，官方身份不行。
2. **查询先验**——`from:federalreserve` 只可能返回官方，这个信息免费。
3. **启发式**——bio 关键词、粉丝阈值、发帖语言（决定 KOL 分中英）。
4. **模型分类**——只处理剩下的，结果落库，下次不再花钱。

```yaml
cohorts:
  crowd:
    authority_weighting: 0.0   # 一人一票：40 粉和 40000 粉等重
    blend_weight: 0.15
  official:
    authority_weighting: 1.0
    engagement_weighting: 0.15 # 三个赞的美联储声明仍然是美联储
    handles: [federalreserve, nyfed, stlouisfed, ...]
```

---

## 成本

**X API 是主要成本。** recent search 只覆盖最近 7 天，读取额度按付费档位计
（免费档基本不可用于此场景）。系统的省钱手段：`since_id` 增量游标、默认排除转推、
每个查询独立 `max_results`。先用 `--hours 3` 小窗口试跑，确认查询写对了再放大。

**模型成本**默认走 Opus 5，抽取用 `effort: low`（机械活），合成用 `high`（语言即产品）。
三层防浪费：按 (帖子, 议题) 落库只为新帖付费、固定评分规则走 prompt 缓存、
磁盘缓存按内容哈希。重渲染报告、崩溃重跑都是零成本。
每次运行结束打印实际 token 与估算金额。

想更省：把 `llm.extract_model` 换成 `claude-haiku-4-5`，抽取质量会降一些，
但因为立场轴是封闭集合、且有一致性闸门兜底，通常仍可用。

---

## 换数据源

`sources/` 下是一个两方法的接口。要接别的平台或你自己的采集器，
把数据导成 JSONL 喂给 `FixtureSource` 就行，下游全部不变：

```json
{"id":"1","author_id":"u1","handle":"someone","followers":1200,
 "created_at":"2026-08-29T09:00:00Z","text":"…","like":12,"retweet":3,
 "cohort_hint":"crowd","query_tag":"fed-rate:crowd_en"}
```

---

## 安装与测试

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q            # 67 passed，全部离线
```

依赖：`anthropic` `pydantic` `PyYAML` `numpy` `Jinja2` `rich`。
HTTP 走标准库，没有额外的网络依赖。

## 目录

```
src/xfeeder/
  sources/     X API v2 / fixture 回放
  pipeline/    ingest · cohort · extract · weighting★ · cluster · synthesize★ · alerts
  llm/         prompts★ · client · offline · embeddings
  render/      terminal · html
config/        config.yaml · demo.yaml · issues/*.yaml
fixtures/      示例数据与其生成脚本
tests/         67 个测试，全部无需网络
```

★ = 设计的实质所在，详见 [DESIGN.md](DESIGN.md)。

## 局限

写在 [DESIGN.md 第八节](DESIGN.md#八已知局限不加粉饰)，没有粉饰。
最重要的一条：**查询写得偏，结论就偏**——这个误差比模型误差大得多。
