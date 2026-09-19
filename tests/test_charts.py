from datetime import datetime, timedelta, timezone

from claude_burndown.charts import build
from claude_burndown.model import Burndown
from claude_burndown.store import Sample

UTC = timezone.utc
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_chart_build():
    resets_at = T0 + timedelta(hours=3)
    start = resets_at - timedelta(hours=5)

    samples = [
        Sample(start, "claude", "5h", 0.0, resets_at, 300, "statusline"),
        Sample(T0, "claude", "5h", 40.0, resets_at, 300, "statusline"),
    ]

    bd = Burndown(
        provider="claude",
        window="5h",
        window_min=300,
        now=T0,
        used=40.0,
        status="on-pace",
        resets_at=resets_at,
        start=start,
        pace=40.0,
        delta=0.0,
        samples=samples,
    )

    chart = build(bd, samples, T0)
    assert chart is not None
    assert chart.provider == "claude"
    assert chart.window == "5h"
    assert chart.pace is not None
    assert len(chart.segments) >= 1
