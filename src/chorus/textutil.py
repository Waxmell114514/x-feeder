"""Text normalisation, hashing, language detection, and phrase mining.

The phrase miner at the bottom is load-bearing for two stages that cannot
use a model to write anything: the planner, which needs candidate search
terms, and the panel builder, which needs candidate positions. Both work
the same way - take the words the corpus actually uses, rank them by how
many documents use them, and let Jev judge the shortlist. Nothing is
invented; a phrase that reaches the report was written by somebody.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"@\w+")
_HASH = re.compile(r"#(\w+)")
_WS = re.compile(r"\s+")
_CJK = re.compile(r"[一-鿿㐀-䶿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")

# Small and deliberately unclever. This list only has to stop a mined phrase
# from starting or ending on a function word, which is what makes one
# unreadable; it is not a substitute for real per-language stopwords.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "of", "to", "in", "on", "for", "with", "at", "by", "from",
    "as", "is", "are", "was", "were", "be", "been", "being", "it", "its", "he",
    "she", "they", "them", "we", "you", "i", "my", "our", "your", "their",
    "not", "no", "do", "does", "did", "have", "has", "had", "will", "would",
    "can", "could", "should", "may", "might", "must", "about", "into", "over",
    "after", "before", "just", "so", "up", "down", "out", "more", "most",
    "very", "really", "what", "when", "where", "which", "who", "how", "why",
    "there", "here", "also", "still", "even", "any", "all", "some", "one",
    "like", "get", "got", "going", "think", "know", "see", "say", "said",
    "people", "thing", "things", "way", "time", "now", "new", "good", "bad",
    "https", "http", "www", "com", "amp", "reddit", "comments", "edit",
    "a", "i", "s", "t", "m", "re", "ve", "ll", "don", "isn", "doesn",
}

# Chinese is mined as character n-grams, because there is no segmenter here
# and a phrase in Chinese has no spaces to find it by. These are the
# characters that must not start or end one: alone they carry no argument,
# and a phrase beginning "的" reads as a fragment rather than a position.
CJK_STOPCHARS = set(
    "的了是在和也都就不没有我你他她它这那个会对把被与及等很更还要说做让给"
    "从到中上下里为以而但又只能可才吧呢啊呀吗嘛过着地得之其所由于同向往"
    "你们我们他们一二三四五六七八九十年月日个种些样么点"
)


def clean(text: str) -> str:
    """Human-readable cleanup: strip URLs and collapse whitespace."""
    text = _URL.sub(" ", text)
    text = _HASH.sub(r"\1", text)
    return _WS.sub(" ", text).strip()


def normalise(text: str) -> str:
    """Aggressive normalisation used for near-duplicate detection."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _URL.sub(" ", text)
    text = _MENTION.sub(" ", text)
    text = re.sub(r"^(rt\s+)+", "", text)
    text = re.sub(r"[^\w一-鿿]+", " ", text)
    return _WS.sub(" ", text).strip()


def text_hash(text: str) -> str:
    return hashlib.sha1(normalise(text).encode("utf-8")).hexdigest()[:16]


def shingles(text: str, k: int = 5) -> set[str]:
    n = normalise(text)
    if _CJK.search(n):
        toks = list(n.replace(" ", ""))
        k = 3
    else:
        toks = n.split()
    if len(toks) <= k:
        return {" ".join(toks)} if toks else set()
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def detect_lang(text: str) -> str:
    """Good enough for cohort routing: zh / ja / ko / ru / ar / en / und."""
    if not text:
        return "und"
    counts = {
        "zh": len(_CJK.findall(text)),
        "ja": len(_KANA.findall(text)),
        "ko": len(_HANGUL.findall(text)),
        "ru": len(_CYRILLIC.findall(text)),
        "ar": len(_ARABIC.findall(text)),
        "en": len(_LATIN.findall(text)),
    }
    # Kana beats Han: Japanese text contains kanji too.
    if counts["ja"] >= 2:
        return "ja"
    best = max(counts, key=lambda k: counts[k])
    if counts[best] == 0:
        return "und"
    # A handful of Han characters in an English tweet should not flip it.
    if best == "en" and counts["zh"] >= 4:
        return "zh"
    return best


def truncate(text: str, limit: int = 280) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


_TOKEN = re.compile(
    r"[A-Za-z][A-Za-z0-9'\-]*|[0-9]+(?:\.[0-9]+)?%?|[\u4e00-\u9fff]"
)


def tokens(text: str) -> list[str]:
    """Lower-cased tokens: a word in a spaced script, a character in Chinese.

    One-letter words are kept. They are stopwords and never start or end a
    mined phrase, but dropping them mid-phrase turns "imply a 38% chance"
    into "imply 38% chance" - words the document never put next to each
    other, in something this system presents as the document's own words.

    Chinese has no spaces to find a phrase by and there is no segmenter in
    this package, so each character is a token and phrases come out as
    character n-grams. That is cruder than real segmentation and it is what
    makes the Chinese path work at all: without it `mine_phrases` returns
    nothing on Chinese text, and a mined panel over Chinese documents is
    silently empty.
    """
    return [t.lower() for t in _TOKEN.findall(text or "")]


def is_cjk(token: str) -> bool:
    return bool(token) and bool(_CJK.match(token[0]))


def join_tokens(seq: list[str]) -> str:
    """Rebuild a phrase from tokens: no space between two Chinese characters."""
    out = ""
    for i, token in enumerate(seq):
        if i and not (is_cjk(token) and is_cjk(seq[i - 1])):
            out += " "
        out += token
    return out


def content_words(text: str, min_len: int = 3) -> list[str]:
    return [t for t in tokens(text) if len(t) >= min_len and t not in STOPWORDS]


def _is_stop(token: str) -> bool:
    return token in STOPWORDS or (is_cjk(token) and token in CJK_STOPCHARS)


def _trim(seq: list[str]) -> list[str]:
    while seq and _is_stop(seq[0]):
        seq = seq[1:]
    while seq and _is_stop(seq[-1]):
        seq = seq[:-1]
    return seq


#: Chinese n-gram bounds, in characters. Three characters is usually half a
#: word - "码能力" is 编|码能力 - and past six it is a whole clause.
CJK_MIN_CHARS = 4
CJK_MAX_CHARS = 6


def _rank(phrase: str, count: int) -> float:
    """How much a candidate phrase is worth.

    Document frequency, with length counting for Chinese. Without a
    segmenter a short Chinese n-gram is usually a piece of a word, and a
    piece of a word is used by more documents than the phrase it came out
    of - so ranking on frequency alone reliably picks the fragment over the
    idea. Spaced scripts already have word boundaries and need no such
    correction.
    """
    units = tokens(phrase)
    if any(is_cjk(u) for u in units):
        return count * (len(units) / CJK_MIN_CHARS)
    return float(count)


def mine_phrases(
    texts: list[str],
    *,
    min_docs: int = 2,
    max_words: int = 4,
    min_words: int = 1,
    limit: int = 40,
    ignore=None,
    with_docs: bool = False,
):
    """Phrases the corpus actually uses, ranked by document frequency.

    Ranking counts *documents*, never occurrences, so one account repeating
    itself forty times cannot invent a phrase - the same principle the
    weighting stage applies to voice, applied to vocabulary.

    A shorter phrase is dropped when a longer phrase containing it appears
    in as many documents: "core services inflation" says something, "core
    services" less, "core" nothing.
    """
    ignored = {w.lower() for w in (ignore or set())}
    df: dict[str, set[int]] = {}

    for doc_i, text in enumerate(texts):
        seen: set[str] = set()
        words = tokens(text)
        # A Chinese token is one character, so the same n covers much less
        # ground: "岗位减少" is four tokens and one phrase, where four
        # English tokens are a clause. Chinese documents get a longer range.
        cjk = sum(1 for w in words if is_cjk(w))
        hi = max(max_words, CJK_MAX_CHARS) if cjk > len(words) / 2 else max_words
        lo = max(min_words, CJK_MIN_CHARS) if cjk > len(words) / 2 else min_words

        for n in range(lo, hi + 1):
            for i in range(len(words) - n + 1):
                gram = _trim(words[i:i + n])
                if len(gram) != n or not gram:
                    continue
                if all(_is_stop(w) for w in gram):
                    continue
                if n == 1 and (_is_stop(gram[0]) or len(gram[0]) < 4):
                    continue
                phrase = join_tokens(gram)
                if phrase in ignored or phrase in seen:
                    continue
                seen.add(phrase)
                df.setdefault(phrase, set()).add(doc_i)

    scored = [(p, len(docs)) for p, docs in df.items() if len(docs) >= min_docs]
    by_len = sorted(scored, key=lambda kv: (-len(tokens(kv[0])), -kv[1]))
    kept: list[tuple[str, int]] = []
    for phrase, count in by_len:
        if any(phrase in longer and count <= longer_count
               for longer, longer_count in kept):
            continue
        kept.append((phrase, count))

    kept.sort(key=lambda kv: (-_rank(*kv), -len(kv[0])))
    kept = kept[:limit]
    if with_docs:
        return [(phrase, count, frozenset(df[phrase])) for phrase, count in kept]
    return kept


def mining_corpus(documents, *, body_of=None) -> list[str]:
    """One text per distinct thing said, for phrase mining.

    Two corrections that matter, both carried over from the weighting
    stage's view of the world:

      * a comment's own words, not the thread title it inherits - otherwise
        every reply in a busy thread votes for the title's phrasing;
      * one entry per distinct text, so six accounts posting one identical
        line contribute one voice to the vocabulary, not six. Copy-paste
        cannot buy a seat on the panel any more than it can buy a share of
        the voice.
    """
    picker = body_of or (lambda d: getattr(d, "own_text", "")
                         or getattr(d, "text", "") or "")
    seen: set[str] = set()
    out: list[str] = []
    for doc in documents:
        # Strip URLs first. A forum post is half links, and mining the raw
        # body turns "mp weixin qq com s zk0kxulzhmmj4lpyw" into a candidate
        # position - which then costs a real Jev question to reject.
        text = clean(picker(doc))
        if not text:
            continue
        key = text_hash(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


# ----------------------------------------------------------------------
# Clause selection, for scripts where an n-gram is the wrong unit
# ----------------------------------------------------------------------
_CLAUSE = re.compile(r"[。！？；，、!?;\n]+|\.\s")
CLAUSE_MIN_CHARS = 8
CLAUSE_MAX_CHARS = 48
# A Chinese clause opening with a particle or a copula is the tail of the
# sentence before it. "是 AI 替代不了的" is the back half of "你有哪些能力，
# 是 AI 替代不了的？" - quoted alone it argues the opposite of what it came
# from. Pronouns are fine: plenty of real claims start with 我 or 它.
CLAUSE_HEAD_STOP = set("是的了在和也都就而但又只过着之其把被与及等更还")


def clauses(text: str) -> list[str]:
    """Split on punctuation into pieces short enough to quote."""
    out = []
    for piece in _CLAUSE.split(clean(text or "")):
        piece = piece.strip(" \t\"'“”《》()（）")
        if not CLAUSE_MIN_CHARS <= len(piece) <= CLAUSE_MAX_CHARS:
            continue
        if piece[0] in CLAUSE_HEAD_STOP:
            continue
        # The split can land inside a parenthesis, leaving "...替代了（老板
        # 不知道" - a quote that visibly stops mid-aside. Cut at the opener.
        for opener, closer in (("（", "）"), ("(", ")"), ("【", "】")):
            if opener in piece and closer not in piece:
                piece = piece.split(opener)[0].strip()
        if len(piece) >= CLAUSE_MIN_CHARS:
            out.append(piece)
    return out


def is_cjk_text(text: str, ratio: float = 0.3) -> bool:
    stripped = re.sub(r"\s+", "", text or "")
    if not stripped:
        return False
    return len(_CJK.findall(stripped)) / len(stripped) >= ratio


def containment(part: set, whole: set) -> float:
    """How much of `part`'s vocabulary appears in `whole`.

    Not Jaccard: a short clause compared against a long position statement
    scores near zero on Jaccard however well it matches, because the sizes
    differ. Containment asks the question we actually mean.
    """
    if not part:
        return 0.0
    return len(part & whole) / len(part)


def mine_clauses(texts: list[str], *, limit: int = 3,
                 similarity: float = 0.3, about: str = "") -> list[str]:
    """Clauses that several documents say in近-enough words.

    Chinese has no spaces, so an unsupervised n-gram miner cuts words in
    half: "码能力已经" is 编|码能力已经. A clause is the smallest unit that
    survives having no segmenter, because punctuation marks its boundaries
    for us.

    Two things rank a clause. Support is how many *other documents* say
    something close to it, matched on character shingles rather than exact
    equality, because two people making one argument never phrase it
    identically. `about` is the position this bloc is supposed to hold, and
    clauses whose vocabulary sits inside it are the ones actually stating
    that position. Without `about`, and with nothing echoed, ranking falls
    back to length and picks whichever sentence rambled longest - which is
    how "所有接受过正规科班计算机科学教育和..." became a bloc's headline
    reason before this argument existed.
    """
    target = shingles(about) if about else set()
    pool: list[tuple[str, int, set]] = []
    for doc_i, text in enumerate(texts):
        for clause in clauses(text):
            pool.append((clause, doc_i, shingles(clause)))
    if not pool:
        return []

    documents = len({doc_i for _, doc_i, _ in pool}) or 1
    scored = []
    for clause, doc_i, grams in pool:
        support = len({
            other_i for other, other_i, other_grams in pool
            if other_i != doc_i and jaccard(grams, other_grams) >= similarity
        })
        rank = support / documents + 2.0 * containment(grams, target)
        scored.append((rank, -len(clause), clause, grams))
    scored.sort(key=lambda row: (-row[0], row[1]))

    picked: list[str] = []
    taken: list[set] = []
    for _, _, clause, grams in scored:
        if any(jaccard(grams, seen) >= similarity for seen in taken):
            continue
        taken.append(grams)
        picked.append(clause)
        if len(picked) >= limit:
            break
    return picked
