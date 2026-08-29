"""Embeddings for claim clustering.

Three providers. `hashing` is the default because it needs no key and no
network: claims arriving here have already been normalised by the extractor
into canonical one-sentence form and partitioned by stance, so a character
n-gram model separates them adequately. Switch to `voyage` when you want
paraphrases in different languages to collide properly.
"""
from __future__ import annotations

import hashlib
import os
import re

import numpy as np

from ..http import request


def embed(texts: list[str], cfg) -> np.ndarray:
    if not texts:
        return np.zeros((0, cfg.embeddings.dim), dtype=np.float32)
    provider = cfg.embeddings.provider
    if provider == "hashing":
        return _hashing(texts, cfg.embeddings.dim)
    if provider == "voyage":
        return _voyage(texts, cfg)
    if provider == "openai":
        return _openai(texts, cfg)
    raise ValueError(f"unknown embedding provider {provider!r}")


# ----------------------------------------------------------------------
_CJK = re.compile(r"[一-鿿]")


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.lower().strip())
    out: list[str] = []
    words = re.findall(r"[a-z0-9']+", text)
    out.extend(words)
    out.extend(f"{a}_{b}" for a, b in zip(words, words[1:]))
    han = _CJK.findall(text)
    out.extend(han)
    out.extend(a + b for a, b in zip(han, han[1:]))          # bigrams
    out.extend(a + b + c for a, b, c in zip(han, han[1:], han[2:]))
    return out


def _hashing(texts: list[str], dim: int) -> np.ndarray:
    mat = np.zeros((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        for tok in _tokens(t):
            h = int.from_bytes(hashlib.md5(tok.encode("utf-8")).digest()[:8], "little")
            idx = h % dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            mat[i, idx] += sign
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def _voyage(texts: list[str], cfg) -> np.ndarray:
    key = os.environ.get("VOYAGE_API_KEY", "")
    if not key:
        raise RuntimeError("VOYAGE_API_KEY not set (embeddings.provider = voyage)")
    vecs: list[list[float]] = []
    for i in range(0, len(texts), 128):
        payload = request(
            "POST", "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json_body={"model": cfg.embeddings.model, "input": texts[i:i + 128],
                       "input_type": "document"},
        )
        vecs.extend(d["embedding"] for d in sorted(payload["data"],
                                                   key=lambda d: d["index"]))
    return _l2(np.asarray(vecs, dtype=np.float32))


def _openai(texts: list[str], cfg) -> np.ndarray:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set (embeddings.provider = openai)")
    vecs: list[list[float]] = []
    for i in range(0, len(texts), 128):
        payload = request(
            "POST", "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json_body={"model": cfg.embeddings.model, "input": texts[i:i + 128]},
        )
        vecs.extend(d["embedding"] for d in sorted(payload["data"],
                                                   key=lambda d: d["index"]))
    return _l2(np.asarray(vecs, dtype=np.float32))


def _l2(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms
