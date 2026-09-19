"""Paths, environment configuration, and constants for Claude Burndown."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "CLAUDE_BURNDOWN_HOME"
TASK_NAME = "ClaudeBurndownService"
WATCHDOG_TASK_NAME = "ClaudeBurndownWatchdog"
DEFAULT_PORT = 8787

# Rate limit window durations
WINDOW_MINUTES = {"5h": 300, "7d": 10080}


def label_for_minutes(minutes: int | None) -> str:
    for label, m in WINDOW_MINUTES.items():
        if m == minutes:
            return label
    return f"{minutes}m" if minutes else "?"


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def samples(self) -> Path:
        return self.home / "samples.jsonl"

    @property
    def latest(self) -> Path:
        return self.home / "latest.json"

    @property
    def claude_desktop_state(self) -> Path:
        return self.home / "claude_desktop_state.json"

    @property
    def html(self) -> Path:
        return self.home / "burndown.html"

    @property
    def usage_db(self) -> Path:
        return self.home / "usage.sqlite"

    @property
    def log(self) -> Path:
        return self.home / "collect.log"

    @property
    def lock(self) -> Path:
        return self.home / ".lock"


def default_home() -> Path:
    env = os.environ.get(ENV_HOME) or os.environ.get("QUOTA_BURNDOWN_HOME")
    return Path(env).expanduser() if env else Path.home() / ".claude-burndown"


def get_paths(home: Path | str | None = None) -> Paths:
    paths = Paths(Path(home).expanduser() if home else default_home())
    paths.home.mkdir(parents=True, exist_ok=True)
    return paths


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def claude_home() -> Path:
    """Location of Claude Code CLI configuration and state."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def claude_projects_dir(home: Path | None = None) -> Path:
    """Directory where Claude Code session transcripts are stored."""
    env = os.environ.get("CLAUDE_PROJECTS_DIR")
    if env:
        return Path(env).expanduser()
    return (home or claude_home()) / "projects"


HISTORY_NAME = "plan-usage-history.json"


def claude_desktop_history_candidates() -> list[Path]:
    """Candidates where Claude desktop app usage history may live across platforms and MSIX caches."""
    override = os.environ.get("CLAUDE_DESKTOP_HISTORY") or os.environ.get("QUOTA_BURNDOWN_CLAUDE_DESKTOP_HISTORY")
    if override:
        return [Path(override).expanduser()]
    if os.name == "nt":
        roaming = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        packaged = sorted((local / "Packages").glob("Claude_*/LocalCache/Roaming/Claude/" + HISTORY_NAME))
        return [roaming / "Claude" / HISTORY_NAME, *packaged]
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / "Claude" / HISTORY_NAME]
    return [Path.home() / ".config" / "Claude" / HISTORY_NAME]


def claude_desktop_history() -> Path:
    """Return the newest existing history file among candidates, or the primary path."""
    candidates = claude_desktop_history_candidates()
    existing = [p for p in candidates if p.is_file()]
    if existing:
        return max(existing, key=lambda p: p.stat().st_mtime)
    return candidates[0]
