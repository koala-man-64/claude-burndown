import json

from claude_burndown.handoff import capture, drain, read


def payload(used=15, org="org-enterprise-corp"):
    return json.dumps({
        "session_id": "session-enterprise-123",
        "organization_id": org,
        "rate_limits": {
            "five_hour": {"used_percentage": used, "resets_at": "2026-09-10T17:00:00Z"},
            "seven_day": {"used_percentage": 42.5, "resets_at": "2026-09-17T12:00:00Z"},
        },
    })


def test_handoff_capture_and_dedup(tmp_path):
    assert capture(payload(used=15), tmp_path) is True
    records = read(tmp_path)
    assert len(records) == 1
    assert records[0]["quota"]["five_hour"]["used_percentage"] == 15.0
    assert records[0]["org_id"] == "org-enterprise-corp"

    # Redraw with same values is deduped
    assert capture(payload(used=15), tmp_path) is False
    assert len(read(tmp_path)) == 1

    # New reading with changed quota is accepted
    assert capture(payload(used=16), tmp_path) is True
    assert len(read(tmp_path)) == 2


def test_handoff_invalid_payload(tmp_path):
    assert capture("not json", tmp_path) is False
    assert capture(json.dumps({"no_rate_limits": True}), tmp_path) is False
    assert len(read(tmp_path)) == 0
