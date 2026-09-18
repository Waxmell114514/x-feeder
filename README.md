# chorus

把公开渠道上关于某个议题的所有声音，合成为**几个虚拟意见领袖**和**一句论断**，
按信源层级分开呈现，并在共识移动或层级分歧时告警。

灵感来自《超新星纪元》里的量子计算机民主：全体畅所欲言，机器理解所有人的意图，
最后合成为少数几个能大致代表所有人的声音。

设计取舍与理由见 **[DESIGN.md](DESIGN.md)**——那是这个项目的主要内容，代码是它的实现。

```
你提出一个议题
     │
     ├─▶ 关键词规划   代码出候选 → Jev 判断哪些值得搜（立场词必须两边等量）
     │
     ├─▶ 公开信源     Reddit · Hacker News · Google News · 任意 RSS   （全部免密钥）
     │                     │
     │            ┌── 官方信息   ──┐
     │            ├── 专业媒体   ──┤
     │            ├── 专业社区   ──┤ ──▶ 加权 ──▶ 分歧 / 告警
     │            └── 大众讨论   ──┘
     │
     └─▶ Jev 分类     每篇文档 → 站哪一边 → 替哪位虚拟意见领袖说话
```

**两个外部服务，分工严格：** 公开源提供声音，Jev 提供判断。
其余的一切——计数、加权、阈值、报告里的每一句话——都是这个包里的普通 Python。

---

## 30 秒看到效果（不需要任何 key）

```bash
pip install -e .
chorus demo
```

用内置的 54 份示例文档跑完整条管线，输出终端报告 + 自包含 HTML。
离线模式下**所有统计是真的**，只有判断来自关键词替身。

真实运行：

```bash
export TYPESAFE_API_KEY=...      # https://console.typesafe.ai
chorus init                      # 生成 config/
chorus new --id ai-jobs --question "Will hiring in software engineering fall in 2027?"
chorus plan --issue ai-jobs      # 先看它打算搜什么——这一步决定的东西最多
chorus run  --issue ai-jobs --html
```

没有 `TYPESAFE_API_KEY` 时系统不会假装能判断：它会明说自己在用替身。

---

## 一次输出长什么样

```
各层级不一致：大众讨论 比 官方信息 高 42%（54% vs 12%）；综合隐含概率 30%。
美联储下次会议加息概率 · 51 篇 / 36 个发言者 / 16 个场所，窗口 48h

层级          隐含                    篇/发言者   一致度   置信
官方信息       12%  ██···············      3/1      100%    56%
专业媒体       16%  ███··············      9/7      100%    77%
专业社区       44%  ████████·········      9/6       33%    43%
大众讨论       54%  ██████████·······     30/22      61%    72%

专业社区  专业社区：意见分裂，按兵不动 51%、加息 36%、态度不明 13%（6 个发言者 / 3 个场所），隐含概率 44%。
  加息·core services inflation  (36% 的声量 · 3 篇 / 2 个发言者)
    → 我们相信加息。
      · services inflation has not come
      · three-month annualised core services inflation
    data_first_zz (r/econmonitor): Core services inflation, in one chart —
    Three-month annualised core services inflation has not come down since…
  按兵不动·rates steady          (33% 的声量 · 2 篇 / 2 个发言者)
    → 我们相信按兵不动。
      · cooling labour markets have historically
  未表态: 13%

信号
  ● 大众读数高于官方口径 42%（54% vs 12%）      ← 最值得看的一条
  ● 大众讨论 比 专业媒体 高 38%（54% vs 16%）
```

（这是 `chorus demo --live` 的真实输出：内置文档 + 真实 Jev 判断。）

每个"虚拟意见领袖"背后是一组真实文档：份额、发言者数、引用原文全部可追溯。
**所有数字由算术算出；Jev 只做分类判断，不生成任何文字；代表的理由是从原文逐字摘出的短语。**

底下那两行小字是故意露出来的，而且是两件不同的事：

- **表了态但无代表**——他们选了边，却不属于面板上任何一位。这是面板的窟窿，要修面板。
- **未表态**——他们根本没选边。没有哪个面板能代表一个不表态的人；
  对官方层来说这往往是最重要的数字（央行发的大部分文字就是不表态）。

一层里表态的声量太少（默认 25%）时，这一层**不给读数**，表格里显示 `—`——
用 18% 的人算出来的数字摆在整层的位置上，是这套系统最容易骗人的地方。

---

## 命令

| 命令 | 作用 |
|---|---|
| `chorus demo` | 内置数据跑通全流程，无需 key |
| `chorus init` | 生成 `config/` 脚手架 |
| `chorus new --id X --question "..."` | 从一个是非问题生成议题文件 |
| `chorus plan --issue X` | 判断该搜哪些关键词，并把整张判断表打出来 |
| `chorus plan --issue X --expand` | 再从已抓到的文档里挖新词（伪相关反馈） |
| `chorus run --issue X` | 完整循环：规划 → 抓取 → 分层 → 阅读 → 归类 → 报告 |
| `chorus panel --issue X` | 查看 / 挖掘意见领袖面板；`--write` 打印可冻结的 YAML |
| `chorus ingest / tier / read / assign` | 只跑其中一个阶段 |
| `chorus report --issue X --html` | 重渲染最近一次快照，不花任何钱 |
| `chorus watch --issue X --interval 1800` | 循环运行，变化时告警 |
| `chorus issues` / `chorus stats` | 查看配置与库存 |

仓库里带了两个议题：`fed-rate`（英文，也是 `chorus demo` 的离线样例）
和 `ai-jobs-cn`（中文，真实跑过）。

全局开关：`--config <path>`、`--offline`（完全不调 Jev）。

每个阶段都能单独跑、都幂等，所以一次失败只损失一个阶段，不是整轮。

---

## 定义一个议题

议题就是一个**能从单篇文档独立判断**的问题。
"美联储 9 月会不会加息"是好问题；"经济现在怎么样"不是。

```yaml
issues:
  - id: fed-rate
    title_zh: "美联储下次会议加息概率"
    question: >
      Will the Federal Reserve raise its policy interest rate at the next
      FOMC meeting?
    background: >
      当前 4.25-4.50%，过去三次会议均为按兵不动，核心 PCE 高于目标。
    window_hours: 48
    half_life_hours: 24          # 24 小时前的发言只算半票

    axis:                        # 一篇文档可以站的边
      - id: hike
        label_zh: "加息"
        anchor: 0.90             # 该立场在 0-1 概率轴上的位置
        description: "The text argues the Fed will, or should, raise rates."
        keywords: ["hike", "raise rates", "tighten", "加息"]
      - id: hold
        label_zh: "按兵不动"
        anchor: 0.12
        description: "The text argues rates stay unchanged."
        keywords: ["hold", "on hold", "unchanged", "按兵不动"]
      - {id: unclear, label_zh: "态度不明"}     # 无 anchor：计算时排除

    entities: ["Federal Reserve", "FOMC", "Jerome Powell"]
    terms: ["fed rate decision", "interest rates", "monetary policy"]

    sources:
      reddit:
        subreddits:              # 写在哪一层下面，就按哪一层计
          expert: [AskEconomics, badeconomics]
          crowd:  [economics, investing]
        include_comments: true   # 标题是话题，评论才是立场
      gnews: {enabled: true}
      feeds:
        - {url: "https://www.federalreserve.gov/feeds/press_all.xml",
           tier: official, channel: federalreserve.gov}
```

`description` 会被原样交给 Jev 当作该选项的判据（它是**字面**理解的）；
`keywords` 既是给 Jev 的示例，也是离线替身唯一能匹配的东西。

`anchor` 用于在**没人报数字**时从立场分布反推读数。
它是先验判断，不是测量——改它会改变结果，这是有意暴露出来的旋钮。

---

## 意见领袖面板：声明，或挖掘

Jev 不生成文字，所以面板必须**先于**分类存在。这被逼出来的约束对监控反而更合适：
每轮重新发明的阵营没法跨时间比较，而"共识变了没有"是监控唯一要回答的问题。

**第一次跑**：不写 `panel:`，系统从文档里挖。候选位置是**逐字摘出的短语**，
按"多少篇不同文档用过"排序，去重，再由 Jev 逐个判断"这是不是一个人可能持有的立场"。

```bash
chorus panel --issue fed-rate --write     # 打印成议题文件要的样子
```

**之后**：把它粘进议题文件冻结下来，顺手给每位领袖补一句 `flip_on:`（什么证据会让这派改主意）——
那是唯一需要人来写、模型写不了的句子。

```yaml
panel:
  - id: hike--services-inflation
    label: "core services inflation has not come down"
    label_zh: "加息派·通胀未退"
    stance: hike
    description: 'Argues for "raise rates", reasoning from: core services inflation has not come down.'
    flip_on: "核心 PCE 环比连续两个月回到 0.2% 以下"
```

---

## 信源与分层

| 源 | 免密钥 | 给什么 | 注意 |
|---|---|---|---|
| Reddit | ⚠️ 见下 | 帖子 + 顶层评论，分数/评论数 | **机房 IP 匿名访问被直接封**，服务器上需要凭证 |
| Hacker News | ✅ Algolia | 故事 + 评论全文检索 | 人群很窄 |
| Google News | ✅ RSS | 几乎所有媒体的**标题**，中英文都行 | **只有标题**，系统会按"薄文档"打折 |
| V2EX | ✅ sov2ex | 中文技术社区的帖子全文检索 | 只有主题帖，评论区挖不到 |
| 任意 RSS/Atom | ✅ | 机构自己的发布 | 官方层只能靠它 |

分层由便宜到贵，先命中先返回：

1. **场所白名单**（`config.yaml` 里的 `channels`）——免费、精确。
   **这是唯一能把场所放进 `official` 的途径**：推断可以出错，官方身份不行。
2. **查询先验**——议题文件把 `AskEconomics` 写在 `expert` 下面，那就是 expert。
3. **启发式**——场所名关键词、`.gov` 域名。
4. **Jev 判断**——只处理剩下的，一个场所判一次就落库（问题措辞改了才会重判）。
   判完记得 `chorus tier --issue X` 看一眼：`official` 这一层混进一家资管公司，
   "官方与大众相反"这个信号就废了。

判不准的场所一律落到 `crowd`，绝不向上：错误提拔会污染整个读数，错误降级只损失一个场所的权威。

**Reddit 要注意：** 从家庭宽带跑，`.json` 匿名接口直接可用；**从服务器/云主机跑会吃 403**
——Reddit 对机房 IP 段返回的是一整页 HTML 拦截页，不是限流响应，换 User-Agent 没有用。
去 https://www.reddit.com/prefs/apps 建一个免费的 script app，导出
`REDDIT_CLIENT_ID` 和 `REDDIT_CLIENT_SECRET`，这个源会自动改走 `oauth.reddit.com`。
（我在容器里实测过被封的那一边；带凭证的那条路径有单测，但没有真实凭证可验。）

**中文怎么办：** 仓库里带了一个真实跑过的中文议题（`ai-jobs-cn`），
实测结论写在 [DESIGN.md 第十一节](DESIGN.md#十一中文议题实测发生了什么)，这里只说要点：

- **媒体层很好填。** Google News 的中文检索一次能拿 100 条中文媒体，
  而且是检索不是整份 feed 过滤。这比"只能靠 RSS 兜底"强得多。
- **大众层只有一个场所。** V2EX（经 sov2ex 公开检索）。
  微博/知乎/贴吧没有公开检索接口，RSSHub 公共实例已经 403。
- **官方层基本是空的。** 中国政府网站不发 RSS。所以 `official_contradiction`
  这个最有价值的信号，在中文议题上大概率不会触发——拿不到官方那一侧。
- **判据用中文写没问题。** 同一批 74 篇中文文档，中文判据和英文判据的一致率
  是 96%（相关性和立场都是）；分歧的 3 篇里英文判据更保守也更对。
  CJK 那条精度警告在"选一个封闭选项"这类判断上影响很小。
- **瓶颈是取样，不是判断。** 7 天窗口 74 篇里只有 13 篇真的在谈这个问题；
  换成 30 天 + 更具体的词之后是 80 篇里 31 篇。中文议题的窗口要宽得多。
- **面板要自己写。** 中文没有空格，字级 n-gram 会挖出被切断的词，
  而且 11 篇长帖里每个人都用自己的话讲同一件事，挖掘挖不出共同论点。
  正确做法是跑一轮、**读一遍原文**、把阵营手写进议题文件。

---

## 成本

| | 上一代（X + Opus 5） | 这一代（公开源 + Jev） |
|---|---|---|
| 信源 | X API 付费档，按读取计量 | **0**，四个源全部免密钥 |
| 判断 | $5 / 百万输入 token | **$0.042 / 百万输入 token，输出免费** |
| 一轮 100 篇 | — | **实测 $0.0066**（113 请求 / 573 个问题 / 15.7 万 token） |
| 一轮 300 篇 | 仅抽取一项约 $1.7 起 | **约 $0.02** |
| 整轮耗时 | 每批数秒 | **实测 14 秒**（含全部抓取），八路并发 |
| 重跑（全缓存） | 重新付费 | **0.5 秒，$0** |

上面三个"实测"是真的跑出来的数字，不是估算：一次对 101 篇真实公开文档的完整循环。

省钱的手段还在：判断按 (文档, 议题, 问题版本) 落库，磁盘缓存按 (模型, 问题版本, state) 哈希。
重渲染报告、崩溃重跑都是零成本。改一个问题的措辞，会精确作废它可能影响的那些答案。
每次运行结束打印实际 token 与估算金额。

---

## 换 / 加数据源

`sources/` 下是一个单方法接口（`fetch(query, since) -> [Document]`）。
要接别的平台或你自己的采集器，把数据导成 JSONL 喂给 `FixtureSource` 就行，下游全部不变：

```json
{"id":"mysource:1","source":"rss","channel":"example.com","channel_kind":"outlet",
 "author":"","title":"…","text":"…","url":"https://…",
 "created_at":"2026-09-18T09:00:00Z","score":0,"comments":0}
```

---

## 安装与测试

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q            # 170 passed，全部离线
```

依赖：`pydantic` `PyYAML` `numpy` `Jinja2` `rich`。
HTTP 走标准库——包括 Jev（一个端点、一个方法），没有额外的网络依赖。

## 目录

```
src/chorus/
  jev/         questions★ · client · offline · embeddings
  sources/     reddit · hackernews · gnews · rss · fixture
  pipeline/    plan★ · ingest · tier · read · panel★ · assign★ · weighting★ · synthesize · alerts
  phrasing.py★ 报告里所有句子的模板（模型不写字，这里写）
  render/      terminal · html
config/        config.yaml · demo.yaml · issues/*.yaml
fixtures/      示例文档与其生成脚本
tests/         170 个测试，全部无需网络
```

★ = 设计的实质所在，详见 [DESIGN.md](DESIGN.md)。

## 局限

写在 [DESIGN.md 第十节](DESIGN.md#十已知局限不加粉饰)，没有粉饰。
最重要的一条：**"公开可检索的声音"不是"全体人民"**——
Reddit 和 HN 是特定人群的特定角落，这个误差比模型误差大得多。
`chorus plan` 把查询摊开给你看，就是因为这里只能靠人盯。
