"""Per-request token usage ledger for Claude.

Stores events in SQLite:
- `request`: An API response from Claude (with exact token usage, thinking tokens, cache hit metrics)
- `prompt`: A human or agent turn

Deduplicates on (provider, kind, event_key), tracks file scan state for incremental parsing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .util import iso, now_utc, parse_iso, to_local

PROVIDER = "claude"
REQUEST = "request"
PROMPT = "prompt"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  provider TEXT NOT NULL,
  tool TEXT NOT NULL,
  kind TEXT NOT NULL,
  event_key TEXT NOT NULL,
  ts TEXT NOT NULL,
  session_id TEXT NOT NULL DEFAULT '',
  thread TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  effort TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_write_tokens INTEGER,
  output_tokens INTEGER,
  reasoning_tokens INTEGER,
  total_tokens INTEGER,
  source_file TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (provider, kind, event_key)
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events (kind, ts);
CREATE INDEX IF NOT EXISTS events_session ON events (session_id);
CREATE TABLE IF NOT EXISTS scan_state (
  path TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  scanned_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Event:
    provider: str
    tool: str
    kind: str
    event_key: str
    ts: datetime
    session_id: str = ""
    thread: str = ""
    model: str = ""
    effort: str = ""
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    source_file: str = ""

    def with_model(self, model: str, effort: str) -> "Event":
        return replace(self, model=model or self.model, effort=effort or self.effort)

    def to_row(self) -> tuple:
        return tuple(iso(self.ts) if name == "ts" else getattr(self, name) for name in COLUMNS)


COLUMNS = [f.name for f in fields(Event)]
_KEY = ("provider", "kind", "event_key")
_UPSERT = (
    f"INSERT INTO events ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)}) "
    f"ON CONFLICT({', '.join(_KEY)}) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c not in _KEY)
    + " WHERE coalesce(excluded.output_tokens, 0) > coalesce(events.output_tokens, 0)"
    + " OR (coalesce(excluded.output_tokens, 0) = coalesce(events.output_tokens, 0) AND ("
    + "excluded.total_tokens IS NOT events.total_tokens"
    + " OR (events.model = '' AND excluded.model <> '')"
    + " OR (events.effort = '' AND excluded.effort <> '')"
    + " OR (events.thread = 'subagent' AND excluded.thread = 'root')))"
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, events: Iterable[Event]) -> int:
    rows_ = [e.to_row() for e in events]
    if not rows_:
        return 0
    with conn:
        cur = conn.executemany(_UPSERT, rows_)
    return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(rows_)


def file_changed(conn: sqlite3.Connection, path: Path | str, size: int, mtime: float) -> bool:
    row = conn.execute("SELECT size, mtime FROM scan_state WHERE path = ?", (str(path),)).fetchone()
    return row is None or row["size"] != size or row["mtime"] != mtime


def mark_scanned(conn: sqlite3.Connection, path: Path | str, size: int, mtime: float, now: datetime | None = None) -> None:
    with conn:
        conn.execute(
            "INSERT INTO scan_state (path, size, mtime, scanned_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET size = excluded.size, mtime = excluded.mtime, scanned_at = excluded.scanned_at",
            (str(path), size, mtime, iso(now or now_utc())),
        )


def forget_scans(conn: sqlite3.Connection, contains: str | None = None) -> int:
    with conn:
        if contains:
            cur = conn.execute("DELETE FROM scan_state WHERE instr(path, ?) > 0", (contains,))
        else:
            cur = conn.execute("DELETE FROM scan_state")
    return cur.rowcount


def rows(
    conn: sqlite3.Connection,
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM events WHERE 1 = 1"
    args: list = []
    if since is not None:
        sql += " AND ts >= ?"
        args.append(iso(since))
    if until is not None:
        sql += " AND ts < ?"
        args.append(iso(until))
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY ts, event_key"
    return conn.execute(sql, args).fetchall()


def recent_requests(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    if limit is None:
        return conn.execute("SELECT * FROM events WHERE kind = ? ORDER BY ts DESC", (REQUEST,)).fetchall()
    return conn.execute("SELECT * FROM events WHERE kind = ? ORDER BY ts DESC LIMIT ?", (REQUEST, limit)).fetchall()


def count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM events").fetchone()[0])


def row_ts(row: sqlite3.Row) -> datetime:
    return parse_iso(row["ts"]) or now_utc()


def day_utc(row: sqlite3.Row) -> str:
    return str(row["ts"])[:10]


def day_local(row: sqlite3.Row) -> str:
    return to_local(row_ts(row)).strftime("%Y-%m-%d")


HUMAN_THREADS = ("main", "root", "")


@dataclass
class Totals:
    prompts: int = 0
    agent_prompts: int = 0
    requests: int = 0
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0
    total: int = 0

    def add(self, row: sqlite3.Row) -> None:
        if row["kind"] == PROMPT:
            if row["thread"] in HUMAN_THREADS:
                self.prompts += 1
            else:
                self.agent_prompts += 1
            return
        self.requests += 1
        self.input += int(row["input_tokens"] or 0)
        self.cache_read += int(row["cache_read_tokens"] or 0)
        self.cache_write += int(row["cache_write_tokens"] or 0)
        self.output += int(row["output_tokens"] or 0)
        self.reasoning += int(row["reasoning_tokens"] or 0)
        self.total += int(row["total_tokens"] or 0)

    @property
    def cache_hit_rate_pct(self) -> float:
        denom = self.input + self.cache_read
        return round(100.0 * self.cache_read / denom, 1) if denom > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "prompts": self.prompts,
            "agent_prompts": self.agent_prompts,
            "requests": self.requests,
            "input": self.input,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "output": self.output,
            "reasoning": self.reasoning,
            "total": self.total,
            "cache_hit_rate_pct": self.cache_hit_rate_pct,
        }


def rollup(items: Iterable[sqlite3.Row], key: Callable[[sqlite3.Row], str]) -> dict[str, Totals]:
    out: dict[str, Totals] = {}
    for row in items:
        out.setdefault(key(row), Totals()).add(row)
    return out
