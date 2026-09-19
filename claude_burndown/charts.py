"""Data structures and math behind Claude usage and rate-limit burndown charts."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from .model import Burndown, canonical_samples, find_instance, group_instances
from .store import Sample
from .util import fmt_local, parse_iso, to_local

TARGET_POINTS = 300
ACTIVE_STATUSES = ("over", "under", "on-pace", "early", "exhausted")
PROJECTED_STATUSES = ("over", "under", "on-pace")
MAX_TOKEN_BARS = 60
TOKEN_BAR_STEPS = tuple(timedelta(minutes=m) for m in (5, 10, 15, 30, 60, 120, 180, 360, 720, 1440))
TOKEN_SCALE_STEPS = (1, 2, 4, 5, 8, 10)


@dataclass(frozen=True)
class Point:
    ts: datetime
    used: float


@dataclass
class Segment:
    points: list[Point]
    current: bool


@dataclass(frozen=True)
class ResetMark:
    ts: datetime
    current: bool


@dataclass(frozen=True)
class Line:
    start: tuple[datetime, float]
    end: tuple[datetime, float]


@dataclass(frozen=True)
class Tick:
    ts: datetime
    label: str


@dataclass(frozen=True)
class TokenBar:
    start: datetime
    end: datetime
    tokens: int
    by_model: dict[str, int] = field(default_factory=dict)


@dataclass
class ChartData:
    key: str
    provider: str
    window: str
    span_start: datetime
    span_end: datetime
    now: datetime
    segments: list[Segment]
    resets: list[ResetMark]
    pace: Line | None
    projection: Line | None
    x_ticks: list[Tick]
    span_key: str
    span_label: str
    token_bars: list[TokenBar] = field(default_factory=list)
    token_step: timedelta | None = None
    token_max: int = 0


def default_span_key(window_min: int) -> str:
    return "10h" if window_min <= 300 else "14d"


def span_for(window_min: int) -> timedelta:
    return timedelta(minutes=window_min * 2)


def bucket(points: list[Point], width: timedelta) -> list[Point]:
    if not points or width <= timedelta(0):
        return points
    out: list[Point] = []
    bucket_start = points[0].ts
    bucket_points: list[Point] = []
    for p in points:
        if p.ts - bucket_start >= width:
            if bucket_points:
                avg_used = sum(pt.used for pt in bucket_points) / len(bucket_points)
                out.append(Point(bucket_start + width / 2, avg_used))
            bucket_start = p.ts
            bucket_points = [p]
        else:
            bucket_points.append(p)
    if bucket_points:
        avg_used = sum(pt.used for pt in bucket_points) / len(bucket_points)
        out.append(Point(bucket_start + width / 2, avg_used))
    return out


def x_ticks(start: datetime, end: datetime, span: timedelta) -> list[Tick]:
    ticks = []
    if span <= timedelta(hours=12):
        step = timedelta(hours=2)
        fmt = "%H:%M"
    elif span <= timedelta(days=2):
        step = timedelta(hours=6)
        fmt = "%a %H:%M"
    else:
        step = timedelta(days=2)
        fmt = "%b %d"
    cur = start
    while cur <= end:
        ticks.append(Tick(cur, fmt_local(cur, fmt)))
        cur += step
    return ticks


def token_scale(max_tokens: int) -> int:
    if max_tokens <= 0:
        return 1000
    magnitude = 10 ** (len(str(max_tokens)) - 1)
    base = max_tokens / magnitude
    for step in TOKEN_SCALE_STEPS:
        if base <= step:
            return step * magnitude
    return 10 * magnitude


def token_bars(
    conn: sqlite3.Connection,
    span_start: datetime,
    span_end: datetime,
) -> tuple[list[TokenBar], timedelta]:
    total_span = span_end - span_start
    step = TOKEN_BAR_STEPS[0]
    for s in TOKEN_BAR_STEPS:
        if total_span / s <= MAX_TOKEN_BARS:
            step = s
            break

    cur = span_start
    bars: list[TokenBar] = []
    bar_map: dict[datetime, TokenBar] = {}
    while cur < span_end:
        bar = TokenBar(cur, cur + step, 0, {})
        bars.append(bar)
        bar_map[cur] = bar
        cur += step

    from . import ledger

    rows = ledger.rows(conn, since=span_start, until=span_end, kind=ledger.REQUEST)
    for row in rows:
        ts = parse_iso(row["ts"])
        if not ts:
            continue
        idx = int((ts - span_start).total_seconds() // step.total_seconds())
        if 0 <= idx < len(bars):
            target = bars[idx]
            tok = int(row["total_tokens"] or 0)
            target = TokenBar(
                target.start,
                target.end,
                target.tokens + tok,
                {**target.by_model, row["model"] or "Unknown": target.by_model.get(row["model"] or "Unknown", 0) + tok},
            )
            bars[idx] = target

    return bars, step


def _dedupe(samples: list[Sample]) -> list[Sample]:
    seen = set()
    out = []
    for s in samples:
        k = (s.ts, s.key, s.used, s.resets_at)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def build(
    bd: Burndown,
    samples: list[Sample],
    now: datetime,
    conn: sqlite3.Connection | None = None,
) -> ChartData | None:
    if bd.window_min <= 0:
        return None
    span_key = default_span_key(bd.window_min)
    span = span_for(bd.window_min)
    active = bd.status in ACTIVE_STATUSES and bd.start is not None and bd.resets_at is not None and bd.resets_at > now
    span_end = bd.resets_at if active else now
    span_start = span_end - span

    history = _dedupe(canonical_samples([s for s in samples if s.key == bd.key] + list(bd.samples)))
    all_instances = group_instances(history).get(bd.key, [])
    instances = [
        inst for inst in all_instances if inst.samples and inst.samples[-1].ts >= span_start and inst.samples[0].ts <= span_end
    ]
    current_inst = find_instance(instances, bd.resets_at) if active else None
    width = span / TARGET_POINTS

    segments: list[Segment] = []
    for inst in instances:
        inside = [Point(s.ts, s.used) for s in inst.samples if span_start <= s.ts <= span_end]
        earlier = [s for s in inst.samples if s.ts < span_start]
        if earlier:
            inside.insert(0, Point(span_start, earlier[-1].used))
        if inside:
            segments.append(Segment(bucket(inside, width), inst is current_inst))

    resets = [ResetMark(inst.resets_at, inst is current_inst) for inst in all_instances if span_start <= inst.resets_at <= span_end]

    loose = [Point(s.ts, s.used) for s in history if s.resets_at is None and span_start <= s.ts <= span_end]
    gap = span / 24
    run: list[Point] = []
    for point in loose:
        if run and point.ts - run[-1].ts > gap:
            segments.append(Segment(bucket(run, width), False))
            run = []
        run.append(point)
    if run:
        segments.append(Segment(bucket(run, width), False))
    segments.sort(key=lambda seg: seg.points[0].ts)
    if not segments:
        return None

    pace = projection = None
    if active and bd.start:
        pace = Line((bd.start, 0.0), (bd.resets_at, 100.0))
        if bd.status in PROJECTED_STATUSES and bd.rate_per_hour > 0:
            if bd.exhausts_before_reset and bd.exhaust_at:
                end = (bd.exhaust_at, 100.0)
            else:
                end = (bd.resets_at, min(bd.projected_end, 100.0))
            projection = Line((now, bd.used), end)

    bars: list[TokenBar] = []
    step: timedelta | None = None
    if conn is not None:
        bars, step = token_bars(conn, span_start, span_end)

    return ChartData(
        key=bd.key,
        provider="claude",
        window=bd.window,
        span_start=span_start,
        span_end=span_end,
        now=now,
        segments=segments,
        resets=resets,
        pace=pace,
        projection=projection,
        x_ticks=x_ticks(span_start, span_end, span),
        span_key=span_key,
        span_label=f"two cycles ({span_key})",
        token_bars=bars,
        token_step=step if bars else None,
        token_max=token_scale(max((bar.tokens for bar in bars), default=0)),
    )
