"""Client for TypeSafe's System One API (the Jev model).

The whole API is one endpoint: POST /v1/systemone with a `state` and a map
of typed `questions`, returning one typed `answer` per question. There is
no conversation, no streaming, and no text to parse, so there is nothing
here but transport, a cache, and a thread pool.

Three things this wrapper adds:

1. **One request per document, many questions.** All the questions about a
   document are asked in a single call. They are evaluated in parallel
   against a state that is ingested once, which is both cheaper and faster
   than one call per judgement.

2. **A disk cache** keyed by (model, question version, questions, state).
   Re-running a stage after a crash, re-rendering a report, or widening a
   window costs nothing for documents already judged.

3. **Retries that respect the documented failure modes**: 429 and 529 are
   backed off and retried, 401 and 422 are raised immediately because
   retrying a malformed question just fails four more times.

Offline mode routes every call to `jev/offline.py`, which answers the same
question shapes deterministically from the criteria. That is what makes
`chorus demo` run with no key: the arithmetic downstream is identical, only
the judgement is crude.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Optional, Sequence

from ..http import HttpError, request
from . import offline as offline_answerer

# Bumped whenever the wording of a question changes. It is part of every
# cache key and of every stored judgement, so a reworded question
# invalidates exactly the answers it could have changed and nothing else.
QUESTION_VERSION = "2026-09-18.1"


class JevUnavailable(RuntimeError):
    """No API key configured, or offline mode is on."""


class JevError(RuntimeError):
    """The service rejected the request and retrying would not help."""


# ----------------------------------------------------------------------
class Response:
    """Typed accessors over one `answers` map.

    Jev answers carry probability distributions, and the callers in this
    package want them whole - a stance that came back 0.51/0.49 should not
    be indistinguishable from one that came back 0.99/0.01 by the time it
    reaches the weighting stage.
    """

    def __init__(self, payload: dict[str, Any], *, cached: bool = False,
                 stub: bool = False):
        self.payload = payload
        self.answers: dict[str, Any] = payload.get("answers") or {}
        self.model: str = payload.get("model", "")
        self.usage: dict[str, Any] = payload.get("usage") or {}
        self.cached = cached
        self.stub = stub

    def __contains__(self, key: str) -> bool:
        return key in self.answers

    def noul(self, key: str, default: float = 0.0) -> float:
        a = self.answers.get(key)
        if not a or a.get("type") != "noul":
            return default
        return float(a.get("noul", default))

    def choice(self, key: str) -> tuple[str, dict[str, float], float]:
        """-> (chosen label, probabilities, confidence)."""
        a = self.answers.get(key)
        if not a or a.get("type") != "choice":
            return "", {}, 0.0
        probs = {str(k): float(v) for k, v in (a.get("probabilities") or {}).items()}
        return str(a.get("choice", "")), probs, float(a.get("confidence", 0.0))

    def score(self, key: str) -> tuple[Optional[float], int, float]:
        """-> (raw score, number of levels, confidence).

        Levels come back in the answer's own legend rather than being
        assumed, so a question whose rubric changed cannot be silently
        rescaled against the wrong denominator.
        """
        a = self.answers.get(key)
        if not a or a.get("type") != "score":
            return None, 0, 0.0
        legend = a.get("legend") or {}
        levels = len(legend) or len(a.get("probabilities") or {})
        return float(a.get("score", 0.0)), levels, float(a.get("confidence", 0.0))

    def unit_score(self, key: str, default: float = 0.5) -> float:
        """A Score mapped onto 0..1 by its own rubric.

        Only ever used for ordering and thresholding. Jev's score levels are
        not numerically calibrated between levels, so this number must never
        be read as a measured magnitude.
        """
        raw, levels, _ = self.score(key)
        if raw is None or levels < 2:
            return default
        return max(0.0, min(1.0, raw / (levels - 1)))


# ----------------------------------------------------------------------
class JevClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.jev = cfg.jev
        self.cache_dir = pathlib.Path(self.jev.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = os.environ.get(self.jev.api_key_env, "")
        self.offline = bool(self.jev.offline) or not self.api_key
        self._lock = threading.Lock()
        self.usage = {"requests": 0, "cached": 0, "stubbed": 0,
                      "input_tokens": 0, "questions": 0}

    # -- plumbing ------------------------------------------------------
    @property
    def live(self) -> bool:
        return not self.offline

    def why_offline(self) -> str:
        if self.jev.offline:
            return "jev.offline is set in the config"
        if not self.api_key:
            return f"{self.jev.api_key_env} is not set"
        return ""

    def _key(self, state: Any, questions: dict) -> str:
        blob = json.dumps(
            {"v": QUESTION_VERSION, "m": self.jev.model,
             "s": state, "q": questions},
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def _record(self, payload: dict, n_questions: int) -> None:
        usage = payload.get("usage") or {}
        with self._lock:
            self.usage["requests"] += 1
            self.usage["questions"] += n_questions
            self.usage["input_tokens"] += int(usage.get("input_tokens") or 0)

    def cost_estimate(self) -> float:
        """USD at the published input price. Output tokens are free."""
        return self.usage["input_tokens"] * self.jev.price_in_usd_per_mtok / 1e6

    # -- the call ------------------------------------------------------
    def ask(self, state: Any, questions: dict, *, cache: bool = True) -> Response:
        """One request: one state, many questions, one typed answer each."""
        if not questions:
            return Response({"answers": {}}, stub=True)

        key = self._key(state, questions)
        path = self.cache_dir / f"{key}.json"
        if cache and path.exists():
            with self._lock:
                self.usage["cached"] += 1
            try:
                return Response(json.loads(path.read_text(encoding="utf-8")),
                                cached=True)
            except json.JSONDecodeError:
                path.unlink(missing_ok=True)     # a truncated write, not a verdict

        if self.offline:
            payload = offline_answerer.answer(state, questions)
            with self._lock:
                self.usage["stubbed"] += 1
                self.usage["questions"] += len(questions)
            return Response(payload, stub=True)

        payload = self._post(state, questions)
        self._record(payload, len(questions))
        if cache:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)                     # atomic: no half-written cache hits
        return Response(payload)

    def _post(self, state: Any, questions: dict) -> dict:
        try:
            return request(
                "POST", f"{self.jev.base_url.rstrip('/')}/systemone",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json_body={"state": state, "model": self.jev.model,
                           "questions": questions},
                timeout=self.jev.timeout,
                retries=self.jev.retries,
            )
        except HttpError as e:
            if e.status == 401:
                raise JevError(
                    f"TypeSafe rejected the key in {self.jev.api_key_env} (401)."
                ) from e
            if e.status == 422:
                # The body names the offending field; surfacing it beats a
                # generic failure, because a malformed question is a bug in
                # this repo, not a transient condition.
                raise JevError(f"Jev rejected the question spec (422): "
                               f"{e.body[:400]}") from e
            raise

    # -- fan-out -------------------------------------------------------
    def ask_many(
        self,
        items: Sequence[tuple[Any, Any, dict]],
        *,
        on_error: Optional[Callable[[Any, Exception], None]] = None,
    ) -> dict[Any, Response]:
        """Ask about many states concurrently. `items` is (key, state, questions).

        A failure on one item is reported and skipped rather than failing
        the batch: losing one document's judgement costs a little coverage,
        losing the run costs the snapshot.
        """
        out: dict[Any, Response] = {}
        if not items:
            return out
        workers = max(1, min(self.jev.max_concurrency, len(items)))

        def run(item):
            key, state, questions = item
            return key, self.ask(state, questions)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run, item) for item in items]
            for item, future in zip(items, futures):
                try:
                    key, resp = future.result()
                    out[key] = resp
                except Exception as e:                            # noqa: BLE001
                    if on_error is not None:
                        on_error(item[0], e)
                    else:
                        raise
        return out
