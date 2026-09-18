"""SQLite persistence. One file, no server, safe to copy around.

Everything expensive is keyed so that re-running a stage is free: documents
by id, readings by (document, issue, question version), panel assignments
by (document, issue, panel version). A crash, a re-render, or a widened
window re-uses what is already judged and pays only for what is new.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3
from typing import Iterable, Optional

from .models import (
    Alert, ChannelTier, Document, Panel, PanelAssignment, Reading, SearchPlan,
    Snapshot,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    channel TEXT NOT NULL,
    author TEXT,
    created_at TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    query_tag TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_created ON documents(created_at);
CREATE INDEX IF NOT EXISTS documents_channel ON documents(channel);
CREATE INDEX IF NOT EXISTS documents_hash ON documents(text_hash);

CREATE TABLE IF NOT EXISTS channel_tiers (
    channel TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS readings (
    doc_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, issue_id)
);
CREATE INDEX IF NOT EXISTS readings_issue ON readings(issue_id);

CREATE TABLE IF NOT EXISTS assignments (
    doc_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, issue_id)
);

CREATE TABLE IF NOT EXISTS panels (
    issue_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    issue_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------- documents ----------------
    def upsert_documents(self, documents: Iterable[Document], text_hasher) -> int:
        rows = [
            (d.id, d.source, d.channel, d.author, _iso(d.created_at),
             text_hasher(d.body), d.query_tag, d.model_dump_json())
            for d in documents
        ]
        if not rows:
            return 0
        before = self.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        self.conn.executemany(
            "INSERT INTO documents(id, source, channel, author, created_at, "
            "text_hash, query_tag, payload) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
            "query_tag=excluded.query_tag",
            rows,
        )
        self.conn.commit()
        after = self.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        return after - before

    def documents_in_window(self, since: dt.datetime,
                            query_tags: Optional[list[str]] = None) -> list[Document]:
        sql = "SELECT payload FROM documents WHERE created_at >= ?"
        args: list = [_iso(since)]
        if query_tags:
            marks = ",".join("?" * len(query_tags))
            sql += f" AND query_tag IN ({marks})"
            args.extend(query_tags)
        sql += " ORDER BY created_at DESC"
        return [Document.model_validate_json(r["payload"])
                for r in self.conn.execute(sql, args)]

    def duplicate_group_sizes(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT text_hash, COUNT(*) n FROM documents GROUP BY text_hash"
        )
        return {r["text_hash"]: r["n"] for r in cur}

    def channel_samples(self, channel: str, limit: int = 5) -> list[str]:
        cur = self.conn.execute(
            "SELECT payload FROM documents WHERE channel = ? "
            "ORDER BY created_at DESC LIMIT ?", (channel, limit),
        )
        out = []
        for row in cur:
            doc = Document.model_validate_json(row["payload"])
            out.append(doc.title or doc.body[:160])
        return out

    # ---------------- channel tiers ----------------
    def upsert_channel_tiers(self, rows: Iterable[ChannelTier]) -> int:
        now = _iso(dt.datetime.now(dt.timezone.utc))
        values = [(r.channel, r.tier, r.method, r.confidence, r.reason, now)
                  for r in rows]
        if not values:
            return 0
        self.conn.executemany(
            "INSERT INTO channel_tiers(channel, tier, method, confidence, reason, "
            "updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(channel) DO UPDATE SET "
            "tier=excluded.tier, method=excluded.method, "
            "confidence=excluded.confidence, reason=excluded.reason, "
            "updated_at=excluded.updated_at",
            values,
        )
        self.conn.commit()
        return len(values)

    def get_channel_tiers(self) -> dict[str, ChannelTier]:
        cur = self.conn.execute("SELECT * FROM channel_tiers")
        return {
            r["channel"]: ChannelTier(
                channel=r["channel"], tier=r["tier"], method=r["method"],
                confidence=r["confidence"], reason=r["reason"] or "",
            )
            for r in cur
        }

    # ---------------- readings ----------------
    def upsert_readings(self, readings: Iterable[Reading]) -> int:
        now = _iso(dt.datetime.now(dt.timezone.utc))
        rows = [(r.doc_id, r.issue_id, r.model_dump_json(), now) for r in readings]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO readings(doc_id, issue_id, payload, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(doc_id, issue_id) DO UPDATE SET "
            "payload=excluded.payload, updated_at=excluded.updated_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_readings(self, issue_id: str) -> dict[str, Reading]:
        cur = self.conn.execute(
            "SELECT payload FROM readings WHERE issue_id = ?", (issue_id,))
        out = {}
        for r in cur:
            reading = Reading.model_validate_json(r["payload"])
            out[reading.doc_id] = reading
        return out

    # ---------------- panel assignments ----------------
    def upsert_assignments(self, items: Iterable[PanelAssignment]) -> int:
        now = _iso(dt.datetime.now(dt.timezone.utc))
        rows = [(a.doc_id, a.issue_id, a.model_dump_json(), now) for a in items]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO assignments(doc_id, issue_id, payload, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(doc_id, issue_id) DO UPDATE SET "
            "payload=excluded.payload, updated_at=excluded.updated_at",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_assignments(self, issue_id: str) -> dict[str, PanelAssignment]:
        cur = self.conn.execute(
            "SELECT payload FROM assignments WHERE issue_id = ?", (issue_id,))
        out = {}
        for r in cur:
            a = PanelAssignment.model_validate_json(r["payload"])
            out[a.doc_id] = a
        return out

    # ---------------- panel & plan ----------------
    def save_panel(self, panel: Panel) -> None:
        self.conn.execute(
            "INSERT INTO panels(issue_id, payload, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(issue_id) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at",
            (panel.issue_id, panel.model_dump_json(),
             _iso(dt.datetime.now(dt.timezone.utc))),
        )
        self.conn.commit()

    def get_panel(self, issue_id: str) -> Optional[Panel]:
        row = self.conn.execute(
            "SELECT payload FROM panels WHERE issue_id = ?", (issue_id,)).fetchone()
        return Panel.model_validate_json(row["payload"]) if row else None

    def save_plan(self, plan: SearchPlan) -> None:
        self.conn.execute(
            "INSERT INTO plans(issue_id, payload, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(issue_id) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at",
            (plan.issue_id, plan.model_dump_json(),
             _iso(dt.datetime.now(dt.timezone.utc))),
        )
        self.conn.commit()

    def get_plan(self, issue_id: str) -> Optional[SearchPlan]:
        row = self.conn.execute(
            "SELECT payload FROM plans WHERE issue_id = ?", (issue_id,)).fetchone()
        return SearchPlan.model_validate_json(row["payload"]) if row else None

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
            "SELECT payload FROM snapshots WHERE issue_id = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?", (issue_id, limit),
        )
        return [Snapshot.model_validate_json(r["payload"]) for r in cur]

    def snapshot_series(self, issue_id: str, limit: int = 60) -> list[Snapshot]:
        rows = self.conn.execute(
            "SELECT payload FROM snapshots WHERE issue_id = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?", (issue_id, limit),
        ).fetchall()
        return [Snapshot.model_validate_json(r["payload"]) for r in reversed(rows)]

    # ---------------- alerts ----------------
    def add_alerts(self, alerts: Iterable[Alert]) -> int:
        rows = [(a.issue_id, _iso(a.ts), a.kind, a.severity, a.model_dump_json())
                for a in alerts]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO alerts(issue_id, ts, kind, severity, payload) "
            "VALUES (?,?,?,?,?)", rows,
        )
        self.conn.commit()
        return len(rows)

    def recent_alerts(self, issue_id: str, limit: int = 20) -> list[Alert]:
        cur = self.conn.execute(
            "SELECT payload FROM alerts WHERE issue_id = ? ORDER BY id DESC LIMIT ?",
            (issue_id, limit),
        )
        return [Alert.model_validate_json(r["payload"]) for r in cur]

    # ---------------- bookkeeping ----------------
    def log_ingest(self, issue_id: str, tag: str, fetched: int, stored: int,
                   note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO ingest_log(issue_id, query_tag, ts, fetched, stored, note) "
            "VALUES (?,?,?,?,?,?)",
            (issue_id, tag, _iso(dt.datetime.now(dt.timezone.utc)), fetched,
             stored, note),
        )
        self.conn.commit()

    def stats(self) -> dict:
        q = lambda s: self.conn.execute(s).fetchone()[0]          # noqa: E731
        return {
            "documents": q("SELECT COUNT(*) FROM documents"),
            "channels": q("SELECT COUNT(DISTINCT channel) FROM documents"),
            "tiered": q("SELECT COUNT(*) FROM channel_tiers"),
            "readings": q("SELECT COUNT(*) FROM readings"),
            "assignments": q("SELECT COUNT(*) FROM assignments"),
            "snapshots": q("SELECT COUNT(*) FROM snapshots"),
        }
