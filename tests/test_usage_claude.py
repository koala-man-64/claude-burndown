import json

from claude_burndown.usage import claude


def test_parse_transcript(tmp_path):
    transcript = tmp_path / "session-1.jsonl"
    lines = [
        # User prompt
        json.dumps({
            "type": "user",
            "uuid": "u1",
            "sessionId": "s1",
            "timestamp": "2026-09-10T12:00:00Z",
            "message": {"content": "Hello Claude, optimize this algorithm"},
        }),
        # Assistant response with extended thinking
        json.dumps({
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-09-10T12:00:05Z",
            "effort": "high",
            "message": {
                "id": "msg_01",
                "model": "claude-3-7-sonnet-20250219",
                "content": "Here is the optimized code...",
                "usage": {
                    "input_tokens": 120,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 50,
                    "output_tokens": 300,
                    "output_tokens_details": {"thinking_tokens": 150},
                },
            },
        }),
    ]
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    events = claude.parse_file(transcript)
    assert len(events) == 2

    # Request event
    req = [e for e in events if e.kind == "request"][0]
    assert req.event_key == "msg_01"
    assert req.model == "claude-3-7-sonnet-20250219"
    assert req.effort == "high"
    assert req.input_tokens == 120
    assert req.cache_read_tokens == 800
    assert req.output_tokens == 300
    assert req.reasoning_tokens == 150
    assert req.total_tokens == 120 + 800 + 50 + 300

    # Prompt event inherits model and effort
    prompt = [e for e in events if e.kind == "prompt"][0]
    assert prompt.event_key == "u1"
    assert prompt.model == "claude-3-7-sonnet-20250219"
    assert prompt.effort == "high"
