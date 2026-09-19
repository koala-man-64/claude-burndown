"""Extract recorded user prompts and Claude model responses on demand without retaining text in the ledger."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .usage import claude
from .util import iso

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 256 * 1024


class TextUnavailable(ValueError):
    pass


def row_id(row) -> str:
    return hashlib.sha256(json.dumps([row["provider"], row["event_key"], row["source_file"]]).encode()).hexdigest()


def _entries(path: Path):
    with path.open("rb") as source:
        consumed = 0
        for index in range(1, 1_000_001):
            line = source.readline(MAX_LINE_BYTES + 1)
            if not line:
                return
            consumed += len(line)
            if len(line) > MAX_LINE_BYTES or consumed > MAX_SOURCE_BYTES:
                raise TextUnavailable("Transcript exceeds the safe viewer read limit.")
            try:
                entry = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(entry, dict):
                yield index, entry
        raise TextUnavailable("Transcript exceeds the safe viewer record limit.")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("text", "input_text", "output_text") and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _result(prompt: str, response: str, note: str = "") -> dict:
    if len(prompt) + len(response) > MAX_TEXT_CHARS:
        raise TextUnavailable("Recorded text exceeds the safe viewer display limit.")
    return {
        "status": "available" if prompt and response else "partial",
        "request": prompt,
        "response": response,
        "note": note
        or (
            "Recorded user prompt and response only; full API input, hidden reasoning, tool payloads, and prior conversation context are not reconstructed."
            if prompt and response
            else "Partial text: a prompt or response could not be matched to this call. Missing text is not reconstructed from neighboring calls."
        ),
    }


def read_request(row) -> dict:
    try:
        source = row["source_file"]
        if not source:
            raise TextUnavailable("No source transcript was recorded for this request.")
        path = Path(source)
        if not path.is_file():
            raise TextUnavailable("Source transcript file is no longer available on disk.")

        prompt = ""
        selected_prompt = ""
        found = False
        parts = []
        for _, entry in _entries(path):
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            if entry.get("type") == "user" and claude._is_prompt(entry) and str(entry.get("sessionId") or "") == row["session_id"]:
                text = _text(message.get("content"))
                if text:
                    prompt = text
            elif entry.get("type") == "assistant":
                key = str(message.get("id") or entry.get("requestId") or entry.get("uuid") or "")
                if key == row["event_key"] and str(entry.get("sessionId") or "") == row["session_id"]:
                    if not found:
                        selected_prompt = prompt
                    found = True
                    text = _text(message.get("content"))
                    if text and text not in parts:
                        if parts and text.startswith(parts[-1]):
                            parts[-1] = text
                        else:
                            parts.append(text)
                    if len(selected_prompt) + sum(map(len, parts)) > MAX_TEXT_CHARS:
                        raise TextUnavailable("Recorded text exceeds the safe viewer display limit.")
        if not found:
            raise TextUnavailable("The recorded request could not be matched in its source transcript.")
        return _result(selected_prompt, "\n\n".join(parts))
    except TextUnavailable as exc:
        return {"status": "unavailable", "note": str(exc)}
    except (OSError, Exception) as exc:
        return {"status": "unavailable", "note": f"Error reading transcript: {type(exc).__name__}"}
