from datetime import datetime, timedelta, timezone

from claude_burndown.capacity import CapacityState, Observation

UTC = timezone.utc
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_capacity_state_ingest_and_publish(tmp_path):
    cs = CapacityState(tmp_path, clock=lambda: T0)

    obs1 = Observation(
        provider="claude",
        account_scope="org-acme",
        limit_id="claude",
        window="5h",
        window_min=300,
        used_pct=30.0,
        resets_at=T0 + timedelta(hours=3),
        observed_at=T0,
        received_at=T0,
        source="statusline",
    )
    changed = cs.ingest([obs1])
    assert changed is True

    snap = cs.publish(persist=True)
    assert snap["schema_version"] == 1
    assert len(snap["pools"]) == 1
    pool = snap["pools"][0]
    assert pool["provider"] == "claude"
    assert pool["account_scope"] == "org-acme"
    assert len(pool["windows"]) == 1
    w = pool["windows"][0]
    assert w["used_pct"] == 30.0
    assert w["remaining_pct"] == 70.0
    assert w["usable_pct"] == 60.0  # default 10% reserve -> 100 - 10 - 30 = 60

    # Verify provider_groups
    groups = snap["provider_groups"]
    assert len(groups) == 1
    assert groups[0]["provider"] == "claude"
    assert len(groups[0]["limits"]) >= 2
