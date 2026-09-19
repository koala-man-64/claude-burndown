from datetime import datetime, timedelta, timezone

from claude_burndown.config import Paths
from claude_burndown.store import Sample, Store

UTC = timezone.utc
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_store_append_and_latest(tmp_path):
    paths = Paths(tmp_path)
    store = Store(paths)

    s1 = Sample(T0, "claude", "5h", 10.0, T0 + timedelta(hours=5), 300, "statusline")
    store.append([s1])

    latest = store.latest()
    assert "claude:5h" in latest
    assert latest["claude:5h"].used == 10.0

    # Identical sample is not duplicated
    store.append([s1])
    samples = store.read_samples()
    assert len(samples) == 1

    # Newer sample updates latest and appends
    s2 = Sample(T0 + timedelta(minutes=15), "claude", "5h", 15.0, T0 + timedelta(hours=5), 300, "statusline")
    store.append([s2])
    latest2 = store.latest()
    assert latest2["claude:5h"].used == 15.0
    assert len(store.read_samples()) == 2
