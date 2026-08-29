"""Text normalisation, hashing and cheap language detection."""
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
