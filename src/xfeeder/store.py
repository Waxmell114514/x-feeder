"""SQLite persistence. One file, no server, safe to copy around."""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
from typing import Iterable, Optional

from .models import (
    Alert, Author, CohortAssignment, Extraction, Post, Snapshot,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    id TEXT PRIMARY KEY,
    handle TEXT,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    query_tag TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_created ON posts(created_at);
CREATE INDEX IF NOT EXISTS posts_author ON posts(author_id);
CREATE INDEX IF NOT EXISTS posts_hash ON posts(text_hash);

CREATE TABLE IF NOT EXISTS cohort_assignments (
    author_id TEXT PRIMARY KEY,
    cohort TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    post_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (post_id, issue_id)
);
CREATE INDEX IF NOT EXISTS extractions_issue ON extractions(issue_id);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_issue_ts ON snapshots(issue_id, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cursors (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    query_tag TEXT,
    ts TEXT NOT NULL,
    fetched INTEGER,
    stored INTEGER,
    note TEXT
);
"""


def _iso(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------- authors ----------------
    def upsert_authors(self, authors: Iterable[Author]) -> int:
        now = _iso(dt.datetime.now(dt.timezone.utc))
        rows = [(a.id, a.handle, a.model_dump_json(), now) for a in authors]
        self.conn.executemany(
            "INSERT INTO authors(id, handle, payload, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET handle=excluded.handle, "
            "payload=excluded.payload, updated_at=excluded.updated_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_authors(self, ids: Optional[Iterable[str]] = None) -> dict[str, Author]:
        if ids is None:
            cur = self.conn.execute("SELECT payload FROM authors")
        else:
            ids = list(ids)
            if not ids:
                return {}
            marks = ",".join("?" * len(ids))
            cur = self.conn.execute(
                f"SELECT payload FROM authors WHERE id IN ({marks})", ids
            )
        out = {}
        for row in cur:
            a = Author.model_validate_json(row["payload"])
            out[a.id] = a
        return out

    # ---------------- posts ----------------
    def upsert_posts(self, posts: Iterable[Post], text_hasher) -> int:
        rows = []
        for p in posts:
            rows.append(
                (p.id, p.author_id, _iso(p.created_at), text_hasher(p.text),
                 p.query_tag, p.model_dump_json())
            )
        if not rows:
            return 0
        before = self.conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
        self.conn.executemany(
            "INSERT INTO posts(id, author_id, created_at, text_hash, query_tag, payload) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            rows,
        )
        self.conn.commit()
        after = self.conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
        return after - before

    def posts_in_window(self, since: dt.datetime, query_tags: Optional[list[str]] = None
                        ) -> list[Post]:
        sql = "SELECT payload FROM posts WHERE created_at >= ?"
        args: list = [_iso(since)]
        if query_tags:
            marks = ",".join("?" * len(query_tags))
            sql += f" AND query_tag IN ({marks})"
            args.extend(query_tags)
        sql += " ORDER BY created_at DESC"
        return [Post.model_validate_json(r["payload"]) for r in self.conn.execute(sql, args)]

    def all_posts(self) -> list[Post]:
        return [Post.model_validate_json(r["payload"])
                for r in self.conn.execute("SELECT payload FROM posts")]

    def duplicate_group_sizes(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT text_hash, COUNT(*) n FROM posts GROUP BY text_hash"
        )
        return {r["text_hash"]: r["n"] for r in cur}

    # ---------------- cohorts ----------------
    def upsert_cohorts(self, assignments: Iterable[CohortAssignment]) -> int:
        now = _iso(dt.datetime.now(dt.timezone.utc))
        rows = [(a.author_id, a.cohort, a.method, a.confidence, a.reason, now)
                for a in assignments]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO cohort_assignments(author_id, cohort, method, confidence, reason, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(author_id) DO UPDATE SET "
            "cohort=excluded.cohort, method=excluded.method, confidence=excluded.confidence, "
            "reason=excluded.reason, updated_at=excluded.updated_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_cohorts(self) -> dict[str, CohortAssignment]:
        cur = self.conn.execute("SELECT * FROM cohort_assignments")
        return {
            r["author_id"]: CohortAssignment(
                author_id=r["author_id"], cohort=r["cohort"], method=r["method"],
                confidence=r["confidence"], reason=r["reason"] or "",
            )
            for r in cur
        }

    # ---------------- extractions ----------------
    def upsert_extractions(self, extractions: Iterable[Extraction]) -> int:
        now = _iso(dt.datetime.now(dt.timezone.utc))
        rows = [(e.post_id, e.issue_id, e.model_dump_json(), now) for e in extractions]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO extractions(post_id, issue_id, payload, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(post_id, issue_id) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_extractions(self, issue_id: str) -> dict[str, Extraction]:
        cur = self.conn.execute(
            "SELECT payload FROM extractions WHERE issue_id = ?", (issue_id,)
        )
        out = {}
        for r in cur:
            e = Extraction.model_validate_json(r["payload"])
            out[e.post_id] = e
        return out

    # ---------------- snapshots ----------------
    def add_snapshot(self, snap: Snapshot) -> int:
        cur = self.conn.execute(
            "INSERT INTO snapshots(issue_id, ts, payload) VALUES (?,?,?)",
            (snap.issue_id, _iso(snap.ts), snap.model_dump_json()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_snapshots(self, issue_id: str, limit: int = 2) -> list[Snapshot]:
        cur = self.conn.execute(
            "SELECT payload FROM snapshots WHERE issue_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (issue_id, limit),
        )
        return [Snapshot.model_validate_json(r["payload"]) for r in cur]

    def snapshot_series(self, issue_id: str, limit: int = 60) -> list[Snapshot]:
        snaps = self.conn.execute(
            "SELECT payload FROM snapshots WHERE issue_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (issue_id, limit),
        ).fetchall()
        return [Snapshot.model_validate_json(r["payload"]) for r in reversed(snaps)]

    # ---------------- alerts ----------------
    def add_alerts(self, alerts: Iterable[Alert]) -> int:
        rows = [(a.issue_id, _iso(a.ts), a.kind, a.severity, a.model_dump_json())
                for a in alerts]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO alerts(issue_id, ts, kind, severity, payload) VALUES (?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def recent_alerts(self, issue_id: str, limit: int = 20) -> list[Alert]:
        cur = self.conn.execute(
            "SELECT payload FROM alerts WHERE issue_id = ? ORDER BY id DESC LIMIT ?",
            (issue_id, limit),
        )
        return [Alert.model_validate_json(r["payload"]) for r in cur]

    # ---------------- cursors ----------------
    def get_cursor(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM cursors WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_cursor(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO cursors(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, _iso(dt.datetime.now(dt.timezone.utc))),
        )
        self.conn.commit()

    def log_ingest(self, issue_id: str, tag: str, fetched: int, stored: int, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO ingest_log(issue_id, query_tag, ts, fetched, stored, note) VALUES (?,?,?,?,?,?)",
            (issue_id, tag, _iso(dt.datetime.now(dt.timezone.utc)), fetched, stored, note),
        )
        self.conn.commit()

    def stats(self) -> dict:
        q = lambda s: self.conn.execute(s).fetchone()[0]
        return {
            "posts": q("SELECT COUNT(*) FROM posts"),
            "authors": q("SELECT COUNT(*) FROM authors"),
            "classified": q("SELECT COUNT(*) FROM cohort_assignments"),
            "extractions": q("SELECT COUNT(*) FROM extractions"),
            "snapshots": q("SELECT COUNT(*) FROM snapshots"),
        }
