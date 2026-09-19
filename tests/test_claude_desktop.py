import json
from datetime import datetime, timedelta, timezone

from claude_burndown.providers import claude_desktop

UTC = timezone.utc
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def reading(minutes, fh, sd, org="enterprise-org-1"):
    return {
        "t": int((T0 + timedelta(minutes=minutes)).timestamp() * 1000),
        "org": org,
        "u": {"fh": fh, "sd": sd},
    }


def write_history(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "samples": samples}), encoding="utf-8")


def test_claude_desktop_collect(tmp_path):
    hist_file = tmp_path / "plan-usage-history.json"
    state_file = tmp_path / "state.json"

    write_history(hist_file, [
        reading(0, 0, 10),
        reading(15, 5, 10),
    ])

    samples, warnings, stats = claude_desktop.collect(state_file, hist_file)
    assert warnings == []
    assert stats["present"] is True
    assert stats["readings"] == 2
    assert len(samples) == 4  # 5h & 7d for both readings

    # Re-collect with unchanged file returns no new samples
    samples2, _, stats2 = claude_desktop.collect(state_file, hist_file)
    assert samples2 == []
    assert stats2["new"] == 0
