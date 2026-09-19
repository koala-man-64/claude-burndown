from datetime import datetime, timedelta, timezone

from claude_burndown.model import (
    Burndown,
    WindowInstance,
    canonical_sample,
    canonical_samples,
    compute,
    current,
    from_latest,
    idle,
)
from claude_burndown.store import Sample

UTC = timezone.utc
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_canonical_sample_maps_aliases():
    s1 = Sample(T0, "claude", "300m", 25.0, T0 + timedelta(hours=5), 300, "statusline")
    assert canonical_sample(s1).window == "5h"

    s2 = Sample(T0, "claude", "10080m:claude", 50.0, T0 + timedelta(days=7), 10080, "statusline")
    assert canonical_sample(s2).window == "7d"


def test_compute_burndown_on_pace_and_exhausted():
    # Window of 300 min (5 hours), 2.5 hours elapsed -> pace is 50%
    start = T0
    resets_at = T0 + timedelta(hours=5)
    now = T0 + timedelta(hours=2.5)

    s = Sample(now, "claude", "5h", 50.0, resets_at, 300, "statusline")
    inst = WindowInstance("claude:5h", "claude", "5h", 300, resets_at, [s])
    bd = compute(inst, now)

    assert bd.status == "on-pace"
    assert bd.pace == 50.0
    assert bd.delta == 0.0
    assert bd.used == 50.0

    # Over pace
    s_over = Sample(now, "claude", "5h", 70.0, resets_at, 300, "statusline")
    inst_over = WindowInstance("claude:5h", "claude", "5h", 300, resets_at, [s_over])
    bd_over = compute(inst_over, now)
    assert bd_over.status == "over"
    assert bd_over.delta == 20.0

    # Exhausted
    s_ex = Sample(now, "claude", "5h", 100.0, resets_at, 300, "statusline")
    inst_ex = WindowInstance("claude:5h", "claude", "5h", 300, resets_at, [s_ex])
    bd_ex = compute(inst_ex, now)
    assert bd_ex.status == "exhausted"


def test_idle_and_from_latest():
    s = Sample(T0, "claude", "5h", 0.0, None, 300, "desktop")
    b_idle = idle(s, T0)
    assert b_idle.status == "idle"

    latest = {"claude:5h": s}
    res = from_latest(latest, T0)
    assert len(res) == 1
    assert res[0].status == "idle"
