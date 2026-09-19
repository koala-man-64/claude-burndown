"""Append-only sample store: samples.jsonl plus a latest.json snapshot per window."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Paths
from .util import WriterLease, atomic_write_text, iso, parse_iso, read_json


@dataclass(frozen=True)
class Sample:
    ts: datetime
    provider: str
    window: str
    used: float
    resets_at: datetime | None
    window_min: int
    source: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.window}"

    def to_dict(self) -> dict:
        return {
            "ts": iso(self.ts),
            "provider": self.provider,
            "window": self.window,
            "used": round(self.used, 2),
            "resets_at": iso(self.resets_at) if self.resets_at else None,
            "window_min": self.window_min,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Sample | None":
        ts = parse_iso(data.get("ts"))
        if ts is None or not data.get("provider") or not data.get("window"):
            return None
        try:
            used = float(data.get("used"))
            window_min = int(data.get("window_min") or 0)
        except (TypeError, ValueError):
            return None
        return cls(
            ts=ts,
            provider=str(data["provider"]),
            window=str(data["window"]),
            used=used,
            resets_at=parse_iso(data.get("resets_at")),
            window_min=window_min,
            source=str(data.get("source") or ""),
        )

    def same_reading(self, other: "Sample") -> bool:
        return self.used == other.used and self.resets_at == other.resets_at


class _Lock:
    """OS-owned sample lock."""

    def __init__(self, path: Path, timeout: float = 3.0):
        self.path = path
        self.timeout = timeout
        self.lease = None

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.lease = WriterLease(self.path.parent, self.path.name)
                self.lease.__enter__()
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("quota sample store is locked by another writer") from None
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.lease:
            self.lease.__exit__(*exc)


class Store:
    def __init__(self, paths: Paths):
        self.paths = paths

    def latest(self) -> dict[str, Sample]:
        raw = read_json(self.paths.latest, {})
        out: dict[str, Sample] = {}
        if isinstance(raw, dict):
            for key, item in raw.items():
                sample = Sample.from_dict(item) if isinstance(item, dict) else None
                if sample is not None:
                    out[key] = sample
        return out

    def append(self, samples: list[Sample]) -> None:
        if not samples:
            return
        with _Lock(self.paths.lock):
            latest = self.latest()
            to_append: list[Sample] = []
            for s in samples:
                prior = latest.get(s.key)
                if prior is None or not prior.same_reading(s) or s.ts > prior.ts:
                    latest[s.key] = s
                    to_append.append(s)
            if not to_append:
                return
            self.paths.samples.parent.mkdir(parents=True, exist_ok=True)
            with open(self.paths.samples, "a", encoding="utf-8") as fh:
                for s in to_append:
                    fh.write(json.dumps(s.to_dict()) + "\n")
            atomic_write_text(
                self.paths.latest,
                json.dumps({k: v.to_dict() for k, v in latest.items()}, indent=2) + "\n",
            )

    def read_samples(self, since: datetime | None = None) -> list[Sample]:
        if not self.paths.samples.exists():
            return []
        out: list[Sample] = []
        with open(self.paths.samples, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = Sample.from_dict(json.loads(line))
                except ValueError:
                    continue
                if s is not None and (since is None or s.ts >= since):
                    out.append(s)
        return out
