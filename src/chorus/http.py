"""Tiny stdlib HTTP helper.

Deliberately dependency-free: every service this project talks to - Reddit,
Hacker News, Google News, arbitrary RSS, and Jev - is a plain JSON or XML
endpoint over HTTPS, so there is nothing for a client library to do that is
worth a dependency. Honours HTTP(S)_PROXY from the environment.

`request` returns parsed JSON; `fetch_text` returns the raw body, which is
what the feed parsers want.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


DEFAULT_USER_AGENT = "chorus/0.2 (+https://github.com/)"


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
    form_body: Optional[dict] = None,
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
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    hdrs.setdefault("User-Agent", DEFAULT_USER_AGENT)

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
            # 429 (rate limited) and 529 (overloaded) are both "come back
            # later"; honour Retry-After when the server sends one, and back
            # off exponentially when it does not.
            if e.code in (429, 529):
                if attempt < retries:
                    time.sleep(_retry_after(hdr, attempt))
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


def _retry_after(headers: dict, attempt: int) -> float:
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw:
        try:
            return max(1.0, min(60.0, float(raw)))
        except ValueError:
            pass
    return min(30.0, 2.0 ** attempt)


def fetch_text(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 30.0,
    retries: int = 2,
) -> str:
    """GET a document and return its body as text (RSS/Atom, mostly)."""
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"
    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", DEFAULT_USER_AGENT)

    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            hdr = dict(e.headers or {})
            if e.code in (429, 529) or e.code >= 500:
                if attempt < retries:
                    time.sleep(_retry_after(hdr, attempt))
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
