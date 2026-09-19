"""Time, file, JSON, and process coordination helpers."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def from_epoch(value) -> datetime | None:
    """Epoch seconds or milliseconds to an aware UTC datetime."""
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds > 1e12:  # milliseconds
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_local(dt: datetime) -> datetime:
    return dt.astimezone()


def fmt_local(dt: datetime, fmt: str = "%a %H:%M") -> str:
    return to_local(dt).strftime(fmt)


def fmt_minutes(minutes: float) -> str:
    total = max(int(round(minutes)), 0)
    days, rem = divmod(total, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


class WriterLease(AbstractContextManager):
    """OS-held lock, released automatically on process termination."""

    def __init__(self, home: Path, filename: str = ".claude-writer.lock"):
        self.path = home / filename
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            handle.close()
            raise BlockingIOError(f"Another process holds the lock on {self.path}") from exc
        self.file = handle
        try:
            self.file.seek(0)
            self.file.truncate()
            self.file.write(f"pid={os.getpid()}\n")
            self.file.flush()
        except OSError:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                try:
                    self.file.close()
                except OSError:
                    pass
                self.file = None
