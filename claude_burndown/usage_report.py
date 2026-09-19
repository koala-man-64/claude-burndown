"""Text tables, CSV, and JSON views over the Claude usage ledger."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence

from . import ledger
from .ledger import Totals
from .util import iso, now_utc, to_local

DIMENSIONS = ("model", "effort", "model_effort", "day", "session", "thread")
DEFAULT_DIMENSIONS = ("model_effort", "day")
KEYS: dict[str, Callable[[sqlite3.Row], str]] = {
    "model": lambda r: r["model"] or "Unknown model",
    "effort": lambda r: r["effort"] or "default",
    "model_effort": lambda r: f"{r['model'] or 'Unknown'} @ {r['effort'] or 'default'}",
    "day": ledger.day_utc,
    "session": lambda r: r["session_id"][:12] if r["session_id"] else "default",
    "thread": lambda r: r["thread"] or "main",
}
TITLES = {
    "model": "by model",
    "effort": "by reasoning effort",
    "model_effort": "by model x effort",
    "day": "by day (UTC, most recent first)",
    "session": "by session",
    "thread": "by thread type",
}
PERIODS = ("today", "7d", "30d")


def fmt_n(value: int, raw: bool = False) -> str:
    if raw:
        return f"{value:,}"
    if value >= 1_000_000_000:
        return f"{value / 1e9:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1e6:.1f}M"
    if value >= 1_000:
        return f"{value / 1e3:.0f}K"
    return str(value)


def sorted_totals(totals: dict[str, Totals], dimension: str) -> list[tuple[str, Totals]]:
    if dimension == "day":
        return sorted(totals.items(), key=lambda kv: kv[0], reverse=True)
    return sorted(totals.items(), key=lambda kv: (-kv[1].total, -kv[1].prompts, kv[0]))


def table(title: str, totals: dict[str, Totals], dimension: str = "", raw: bool = False, top: int | None = 20) -> str:
    items = sorted_totals(totals, dimension)
    shown = items[:top] if top else items
    grand = sum(t.total for _, t in items) or 1
    width = min(max([len(k) for k, _ in shown] + [8]), 60)
    header = f"{'':<{width}}  {'prompts':>7} {'reqs':>6} {'input':>8} {'cache_r':>8} {'cache_w':>8} {'output':>8} {'reasoning':>9} {'total':>8} {'%tok':>5} {'hit%':>5}"
    lines = [f"== {title} ==", header, "-" * len(header)]
    for key, t in shown:
        label = key if len(key) <= 60 else key[:57] + "..."
        lines.append(
            f"{label:<{width}}  {t.prompts:>7} {t.requests:>6} {fmt_n(t.input, raw):>8} {fmt_n(t.cache_read, raw):>8} "
            f"{fmt_n(t.cache_write, raw):>8} {fmt_n(t.output, raw):>8} {fmt_n(t.reasoning, raw):>9} {fmt_n(t.total, raw):>8} {100 * t.total / grand:>4.0f}% {t.cache_hit_rate_pct:>4.0f}%"
        )
    if top and len(items) > top:
        rest = items[top:]
        lines.append(
            f"{'(+%d more)' % len(rest):<{width}}  {sum(t.prompts for _, t in rest):>7} {sum(t.requests for _, t in rest):>6} {'':>8} {'':>8} {'':>8} {'':>8} {'':>9} {fmt_n(sum(t.total for _, t in rest), raw):>8}"
        )
    return "\n".join(lines)


def summary(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, Totals]:
    now = now or now_utc()
    today = to_local(now).strftime("%Y-%m-%d")
    week = now - timedelta(days=7)
    out: dict[str, Totals] = {period: Totals() for period in PERIODS}
    for row in ledger.rows(conn, since=now - timedelta(days=30)):
        out["30d"].add(row)
        if ledger.row_ts(row) >= week:
            out["7d"].add(row)
        if ledger.day_local(row) == today:
            out["today"].add(row)
    return out


def summary_dict(summary_: dict[str, Totals]) -> dict:
    return {period: totals.to_dict() for period, totals in summary_.items()}


def _period_text(t: Totals, raw: bool = False) -> str:
    return f"{t.prompts} prompts, {t.requests} requests, {fmt_n(t.total, raw)} tokens ({t.cache_hit_rate_pct}% cache hits)"


def status_lines(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    s = summary(conn, now)
    if not any(t.prompts or t.requests for t in s.values()):
        return []
    return [f"Claude usage today: {_period_text(s['today'])}; 7d: {_period_text(s['7d'])}"]


def _range(now: datetime, days: int | None, since: str | None, until: str | None) -> tuple[datetime | None, datetime | None]:
    start = end = None
    if since:
        start = datetime.fromisoformat(since).replace(tzinfo=now.tzinfo)
    if until:
        end = datetime.fromisoformat(until).replace(tzinfo=now.tzinfo) + timedelta(days=1)
    if start is None and end is None and days:
        start = now - timedelta(days=days)
    return start, end


def render_text(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    days: int | None = 7,
    since: str | None = None,
    until: str | None = None,
    dimensions: Sequence[str] = DEFAULT_DIMENSIONS,
    raw: bool = False,
    top: int = 20,
) -> str:
    now = now or now_utc()
    start, end = _range(now, days, since, until)
    rows = ledger.rows(conn, since=start, until=end)
    if not rows:
        return "no usage recorded for that range; run: claude-burndown backfill --usage"
    grand = Totals()
    for row in rows:
        grand.add(row)
    span = (
        f"{iso(start)[:10]} -> {iso(end - timedelta(seconds=1))[:10]}"
        if start and end
        else f"since {iso(start)[:10]}"
        if start
        else f"until {iso(end)[:10]}"
        if end
        else "all time"
    )
    head = [
        f"Claude Usage Ledger - {span} (UTC): {grand.prompts:,} prompts, {grand.requests:,} requests, {len(rows):,} rows",
        f"  Total tokens {grand.total:,}: input {grand.input:,} | cache read {grand.cache_read:,} | cache write {grand.cache_write:,} | output {grand.output:,} (reasoning {grand.reasoning:,})",
        f"  Cache hit rate: {grand.cache_hit_rate_pct}%",
    ]
    blocks = ["\n".join(head)]
    for dimension in dimensions:
        blocks.append(table(TITLES[dimension], ledger.rollup(rows, KEYS[dimension]), dimension, raw, top))
    return "\n\n".join(blocks)


def row_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def export_rows(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    days: int | None = 7,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    now = now or now_utc()
    start, end = _range(now, days, since, until)
    return [row_dict(r) for r in ledger.rows(conn, since=start, until=end)]


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = ledger.COLUMNS
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def payload(conn: sqlite3.Connection, now: datetime | None = None, days: int = 7, recent: int = 50) -> dict:
    now = now or now_utc()
    rows = ledger.rows(conn, since=now - timedelta(days=days))
    return {
        "generated_at": iso(now),
        "days": days,
        "summary": summary_dict(summary(conn, now)),
        "by_model_effort": [
            {"key": key, **t.to_dict()} for key, t in sorted_totals(ledger.rollup(rows, KEYS["model_effort"]), "model_effort")
        ],
        "recent": [row_dict(r) for r in ledger.recent_requests(conn, recent)],
    }


def _efficiency_key(row: sqlite3.Row, dimension: str) -> str:
    if dimension == "session":
        return row["session_id"] or "default"
    return f"{row['model'] or 'Unknown'} @ {row['effort'] or 'default'}"


def _anchor(key: str) -> str:
    return "efficiency-session-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def efficiency_rows(rows: Sequence[sqlite3.Row], dimension: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        if row["kind"] != ledger.REQUEST:
            continue
        key = _efficiency_key(row, dimension)
        item = groups.setdefault(
            key,
            {
                "key": key,
                "anchor": _anchor(key) if dimension == "session" else None,
                "provider": "claude",
                "requests": 0,
                "input": 0,
                "cache_read": 0,
                "cache_write": 0,
                "output": 0,
                "reasoning": 0,
                "total": 0,
                "tokens_per_request": [],
            },
        )
        item["requests"] += 1
        inp = int(row["input_tokens"] or 0)
        cr = int(row["cache_read_tokens"] or 0)
        cw = int(row["cache_write_tokens"] or 0)
        out = int(row["output_tokens"] or 0)
        rsn = int(row["reasoning_tokens"] or 0)
        tot = int(row["total_tokens"] or 0)
        item["input"] += inp
        item["cache_read"] += cr
        item["cache_write"] += cw
        item["output"] += out
        item["reasoning"] += rsn
        item["total"] += tot
        item["tokens_per_request"].append(tot)

    result = []
    for item in groups.values():
        tpr = item["tokens_per_request"]
        denom = item["input"] + item["cache_read"]
        hit_rate = round(100.0 * item["cache_read"] / denom, 1) if denom > 0 else 0.0
        avg_tokens = round(sum(tpr) / len(tpr), 1) if tpr else 0.0
        result.append(
            {
                "key": item["key"],
                "anchor": item["anchor"],
                "provider": "claude",
                "requests": item["requests"],
                "input": item["input"],
                "cache_read": item["cache_read"],
                "cache_write": item["cache_write"],
                "output": item["output"],
                "reasoning": item["reasoning"],
                "total": item["total"],
                "cache_hit_rate_pct": hit_rate,
                "avg_tokens_per_request": avg_tokens,
            }
        )
    return sorted(result, key=lambda r: (-r["total"], r["key"]))
