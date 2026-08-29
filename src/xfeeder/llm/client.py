"""Anthropic wrapper: structured output, disk cache, prompt caching.

Two things matter here.

1. Every call is cached on disk by a hash of (model, prompt version, payload).
   Re-running `synthesize` after a crash, or re-rendering a report, costs
   nothing. Extraction is the token-hungry stage and it is per-post
   idempotent, so the cache is what makes hourly monitoring affordable.

2. The long, frozen part of every prompt (the issue definition, the rubric)
   is sent as a cached system block. The volatile part (the posts) goes in
   the user turn, after the breakpoint.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

PROMPT_VERSION = "2026-08-29.1"


class LLMUnavailable(RuntimeError):
    """Raised when no Anthropic credentials are configured."""


class RefusalError(RuntimeError):
    """The model declined the request; the caller degrades to the stub."""


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cache_dir = pathlib.Path(cfg.llm.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = cfg.llm.offline
        self._client = None
        self.usage = {"calls": 0, "cached": 0, "input_tokens": 0,
                      "output_tokens": 0, "cache_read_tokens": 0}

    # ------------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            if self.offline:
                raise LLMUnavailable("llm.offline is set")
            try:
                import anthropic
            except ImportError as e:                      # pragma: no cover
                raise LLMUnavailable("pip install anthropic") from e
            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------------
    def _cache_path(self, key: str) -> pathlib.Path:
        return self.cache_dir / f"{key}.json"

    @staticmethod
    def _key(*parts: str) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(p.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:32]

    # ------------------------------------------------------------------
    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: Type[T],
        model: Optional[str] = None,
        effort: str = "high",
        max_tokens: int = 16000,
        cache: bool = True,
    ) -> T:
        """One structured-output call, validated into `schema`."""
        model = model or self.cfg.llm.synthesize_model
        key = self._key(PROMPT_VERSION, model, effort, schema.__name__, system, user)
        path = self._cache_path(key)

        if cache and path.exists():
            self.usage["cached"] += 1
            return schema.model_validate_json(path.read_text(encoding="utf-8"))

        result = self._call(system=system, user=user, schema=schema, model=model,
                            effort=effort, max_tokens=max_tokens)
        if cache:
            path.write_text(result.model_dump_json(), encoding="utf-8")
        return result

    # ------------------------------------------------------------------
    def _call(self, *, system: str, user: str, schema: Type[T], model: str,
              effort: str, max_tokens: int) -> T:
        client = self.client
        # The system block is the frozen rubric and is identical across every
        # batch, so it is cached; the posts go after it and are billed once.
        system_blocks = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": effort},
        )
        self.usage["calls"] += 1

        if self.cfg.llm.refusal_fallback:
            # Server-side fallback: if a safety classifier declines, the same
            # request is re-run on a fallback model inside the same call
            # rather than returning nothing.
            resp = client.beta.messages.parse(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs,
            )
        else:
            resp = client.messages.parse(**kwargs)

        self._record(resp)
        if getattr(resp, "stop_reason", None) == "refusal":
            detail = getattr(getattr(resp, "stop_details", None), "category", None)
            raise RefusalError(f"model declined this batch (category={detail})")

        parsed = getattr(resp, "parsed_output", None)
        if parsed is not None:
            return parsed
        return schema.model_validate_json(_first_text(resp))

    def _record(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if not u:
            return
        self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        self.usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0

    def cost_estimate(self) -> float:
        """Rough USD at Opus 5 list pricing ($5 / $25 per MTok, cache reads
        at 0.1x). Indicative only - it does not know about cache writes."""
        return (self.usage["input_tokens"] * 5.0
                + self.usage["cache_read_tokens"] * 0.5
                + self.usage["output_tokens"] * 25.0) / 1e6


def _first_text(resp) -> str:
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError("model returned no text block")
