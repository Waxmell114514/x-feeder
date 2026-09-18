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


_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*|[0-9]+(?:\.[0-9]+)?%?")


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens. CJK is handled separately by `shingles`.

    One-letter words are kept. They are stopwords and never start or end a
    mined phrase, but dropping them mid-phrase turns "imply a 38% chance"
    into "imply 38% chance" - words the document never put next to each
    other, in something this system presents as the document's own words.
    """
    return [t.lower() for t in _TOKEN.findall(text or "")]


def content_words(text: str, min_len: int = 3) -> list[str]:
    return [t for t in tokens(text) if len(t) >= min_len and t not in STOPWORDS]


def _trim(seq: list[str]) -> list[str]:
    while seq and seq[0] in STOPWORDS:
        seq = seq[1:]
    while seq and seq[-1] in STOPWORDS:
        seq = seq[:-1]
    return seq


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
        for n in range(min_words, max_words + 1):
            for i in range(len(words) - n + 1):
                gram = _trim(words[i:i + n])
                if len(gram) != n or not gram:
                    continue
                if all(w in STOPWORDS for w in gram):
                    continue
                if n == 1 and (gram[0] in STOPWORDS or len(gram[0]) < 4):
                    continue
                phrase = " ".join(gram)
                if phrase in ignored or phrase in seen:
                    continue
                seen.add(phrase)
                df.setdefault(phrase, set()).add(doc_i)

    scored = [(p, len(docs)) for p, docs in df.items() if len(docs) >= min_docs]
    by_len = sorted(scored, key=lambda kv: (-len(kv[0].split()), -kv[1]))
    kept: list[tuple[str, int]] = []
    for phrase, count in by_len:
        if any(phrase in longer and count <= longer_count
               for longer, longer_count in kept):
            continue
        kept.append((phrase, count))

    kept.sort(key=lambda kv: (-kv[1], -len(kv[0])))
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
        text = picker(doc)
        if not text:
            continue
        key = text_hash(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out
