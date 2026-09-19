import json
from datetime import datetime, timezone

from claude_burndown.config import Paths
from claude_burndown.statusline import format_line, run
from claude_burndown.store import Sample, Store

UTC = timezone.utc
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_statusline_empty_store(tmp_path):
    paths = Paths(tmp_path)
    store = Store(paths)
    line = run("", store, now=T0, color=False)
    assert "awaiting samples" in line


def test_statusline_with_samples(tmp_path):
    paths = Paths(tmp_path)
    store = Store(paths)
    s1 = Sample(T0, "claude", "5h", 20.0, T0, 300, "statusline")
    store.append([s1])

    stdin_payload = json.dumps({
        "rate_limits": {"five_hour": {"used_percentage": 25.0}}
    })
    line = run(stdin_payload, store, now=T0, color=False)
    assert "Claude" in line
