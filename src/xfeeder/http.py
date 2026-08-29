"""Tiny stdlib HTTP helper.

Deliberately dependency-free so the package works with nothing but the
Anthropic SDK installed. Honours HTTP(S)_PROXY from the environment.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, headers: Optional[dict] = None):
        super().__init__(f"HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body
        self.headers = headers or {}


def request(
    method: str,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    json_body: Optional[dict] = None,
    timeout: float = 30.0,
    retries: int = 3,
) -> dict:
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"

    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("User-Agent", "xfeeder/0.1")

    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:            # noqa: PERF203
            body = e.read().decode("utf-8", "replace")
            hdr = dict(e.headers or {})
            if e.code == 429:
                reset = hdr.get("x-rate-limit-reset")
                wait = 15.0
                if reset:
                    try:
                        wait = max(1.0, float(reset) - time.time())
                    except ValueError:
                        pass
                wait = min(wait, 120.0)
                if attempt < retries:
                    time.sleep(wait)
                    last = HttpError(e.code, body, hdr)
                    continue
            if e.code >= 500 and attempt < retries:
                time.sleep(2 ** attempt)
                last = HttpError(e.code, body, hdr)
                continue
            raise HttpError(e.code, body, hdr) from e
        except urllib.error.URLError as e:
            last = e
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise
    raise last if last else RuntimeError("unreachable")
