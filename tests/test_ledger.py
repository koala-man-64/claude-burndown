from datetime import datetime, timezone

from claude_burndown import ledger
from claude_burndown.ledger import Event

UTC = timezone.utc
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_ledger_upsert_and_rollups(tmp_path):
    db_path = tmp_path / "usage.sqlite"
    conn = ledger.connect(db_path)
    try:
        events = [
            Event(
                provider="claude",
                tool="claude-code",
                kind="request",
                event_key="msg_01",
                ts=T0,
                session_id="s1",
                model="claude-3-7-sonnet",
                effort="medium",
                input_tokens=200,
                cache_read_tokens=600,
                cache_write_tokens=50,
                output_tokens=150,
                reasoning_tokens=80,
                total_tokens=1000,
            ),
            Event(
                provider="claude",
                tool="claude-code",
                kind="prompt",
                event_key="p_01",
                ts=T0,
                session_id="s1",
                thread="main",
            ),
        ]
        inserted = ledger.upsert(conn, events)
        assert inserted == 2
        assert ledger.count(conn) == 2

        # Rollup totals
        rows = ledger.rows(conn)
        totals = ledger.Totals()
        for r in rows:
            totals.add(r)
        assert totals.prompts == 1
        assert totals.requests == 1
        assert totals.input == 200
        assert totals.cache_read == 600
        assert totals.reasoning == 80
        assert totals.total == 1000
        assert totals.cache_hit_rate_pct == 75.0  # 600 / (200 + 600)
    finally:
        conn.close()
