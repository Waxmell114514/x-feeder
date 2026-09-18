"""Normalisation, language detection, and phrase mining in two scripts."""
from chorus import textutil as T


def test_english_phrases_are_the_corpus_own_words():
    docs = ["core services inflation has not come down at all",
            "core services inflation keeps running hot this quarter"]
    mined = dict(T.mine_phrases(docs, min_docs=2, limit=5))
    assert "core services inflation" in mined


def test_chinese_is_mined_as_character_ngrams():
    """Chinese has no spaces to find a phrase by, and there is no segmenter
    here. Without this the Chinese path mines nothing and a mined panel over
    Chinese documents is silently empty."""
    docs = ["AI 编程工具正在让初级岗位减少，很多公司已经缩招了",
            "岗位减少主要是行业周期，AI 只是被用来解释降本",
            "我们团队今年岗位减少了一半，AI 只是借口"]
    mined = dict(T.mine_phrases(docs, min_docs=2, limit=8))
    assert "岗位减少" in mined
    assert mined["岗位减少"] == 3


def test_a_chinese_phrase_never_starts_or_ends_on_a_function_character():
    """A phrase beginning "的" reads as a fragment, not a position."""
    docs = ["这个的岗位减少的问题很严重", "那种的岗位减少的情况也一样"]
    for phrase, _ in T.mine_phrases(docs, min_docs=2, limit=20):
        if T.is_cjk(phrase[0]):
            assert phrase[0] not in T.CJK_STOPCHARS
            assert phrase[-1] not in T.CJK_STOPCHARS


def test_chinese_characters_are_joined_without_spaces():
    assert T.join_tokens(["岗", "位", "减", "少"]) == "岗位减少"
    assert T.join_tokens(["ai", "编", "程"]) == "ai 编程"
    assert T.join_tokens(["core", "services"]) == "core services"


def test_mixed_script_text_keeps_both():
    docs = ["用 AI 替代初级开发的做法越来越多",
            "用 AI 替代初级开发这件事被夸大了"]
    mined = [p for p, _ in T.mine_phrases(docs, min_docs=2, limit=20)]
    assert any("替代初级开发" in p for p in mined)


def test_a_phrase_must_be_used_by_several_documents():
    docs = ["岗位减少了很多", "完全无关的一句话"]
    assert T.mine_phrases(docs, min_docs=2) == []


def test_language_detection_covers_the_scripts_we_collect():
    assert T.detect_lang("岗位减少了") == "zh"
    assert T.detect_lang("jobs are shrinking") == "en"
    assert T.detect_lang("") == "und"


def test_duplicate_detection_works_on_chinese_too():
    a = "AI 正在减少程序员岗位！！！"
    b = "AI 正在减少程序员岗位"
    assert T.text_hash(a) == T.text_hash(b)


def test_urls_are_not_mined_as_positions():
    """A forum post is half links. Mining the raw body turns a tracking
    parameter into a candidate position, and each one costs a real question
    to reject."""
    class Doc:
        def __init__(self, text):
            self.own_text = text
            self.text = text

    docs = [Doc("岗位减少了 参考 https://mp.weixin.qq.com/s/zk0kxULzHmMj4"),
            Doc("岗位减少是真的 见 https://github.com/umlink/java-full-stack")]
    mined = [p for p, _ in T.mine_phrases(T.mining_corpus(docs), min_docs=2)]
    assert "岗位减少" in mined
    assert not any("weixin" in p or "github" in p or "https" in p for p in mined)


def test_a_longer_chinese_phrase_beats_a_fragment_of_it():
    """Without a segmenter, a piece of a word appears in more documents
    than the phrase it came from, so frequency alone picks the fragment."""
    docs = ["编码能力已经被替代了，岗位在减少",
            "编码能力已经被替代，很多岗位在减少",
            "编码能力已经被替代，公司不招人了",
            "能力问题而已",
            "能力问题罢了"]
    ranked = [p for p, _ in T.mine_phrases(docs, min_docs=2, limit=6)]
    assert ranked
    assert len(ranked[0]) >= 4
    assert not any(len(p) < 4 and T.is_cjk(p[0]) for p in ranked)


def test_chinese_reasons_are_clauses_not_broken_words():
    """"码能力已经" is 编|码能力已经 - a word cut in half. Punctuation gives
    Chinese the boundaries that spaces give English."""
    docs = ["护城河感觉已经干涸了，用 AI 写代码快一年多了",
            "AI 时代最终的赢家是大模型企业，其他软件企业护城河归零",
            "我发现之前的编码能力已经可以完全被 AI 替代了"]
    picked = T.mine_clauses(docs, limit=3)
    assert picked
    for clause in picked:
        assert len(clause) >= T.CLAUSE_MIN_CHARS
        assert any(clause in d for d in docs)     # verbatim, punctuation aside


def test_near_duplicate_clauses_are_not_quoted_twice():
    docs = ["岗位真的在减少，我身边好几个人失业了",
            "岗位确实在减少，我身边好几个人失业了呢"]
    picked = T.mine_clauses(docs, limit=3)
    assert len(picked) == len(set(picked))
    assert len(picked) <= 2


def test_clause_mining_survives_having_nothing_to_echo():
    assert T.mine_clauses([]) == []
    assert T.mine_clauses(["短"]) == []


def test_clauses_are_ranked_by_the_position_they_are_quoted_for():
    """With nothing echoed across documents, ranking by length picks
    whichever sentence rambled longest."""
    docs = ["在前 AI 时代的很多事情都不一样了",
            "护城河已经干涸了，编码能力可以被完全替代",
            "所有接受过正规科班计算机科学教育和网络安全教育的人恐怕都难接受"]
    about = "程序员过去赖以立足的护城河被抹平，编码能力已经可以被 AI 替代"
    picked = T.mine_clauses(docs, limit=3, about=about)
    assert any(w in picked[0] for w in ("护城河", "编码能力"))
    assert "正规科班" in picked[-1]          # the rambling one ranks last


def test_a_clause_starting_on_a_particle_is_the_tail_of_another_sentence():
    """"是 AI 替代不了的" is the back half of "你有哪些能力，是 AI 替代不了
    的？" - quoted alone it argues the opposite of what it came from."""
    assert T.clauses("你有哪些工作上的能力，是 AI 替代不了的") == [
        "你有哪些工作上的能力"]


def test_a_quote_never_stops_mid_parenthesis():
    got = T.clauses("编码能力已经可以完全被 AI 替代了（老板不知道，但我清楚）")
    assert any("替代了" in c for c in got)
    assert not any(c.endswith("（老板不知道") for c in got)
