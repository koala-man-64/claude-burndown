# Claude Burndown (Enterprise Edition)

A dedicated local capacity service, real-time rate-limit burndown dashboard, and token usage ledger built specifically for **Claude in Enterprise environments** (Claude Code CLI and Claude Desktop). Python 3.10+, standard library only.

---

## Highlights

- **Pure Claude Focus**: Completely free of extraneous multi-model dependencies (no Codex, no Antigravity).
- **Enterprise-Ready**:
  - **Account & Org Scoping**: Handles Enterprise SSO organizations (`organization_id` / `org_id`) and project workspaces cleanly.
  - **Zero Telemetry & Strict Loopback**: Runs purely on `127.0.0.1` with Origin and Host verification. Never sends prompts, tokens, or logs to external servers.
  - **Non-Admin Enterprise Friendly**: Built-in fallback to per-user Startup supervisor when Windows Task Scheduler (`schtasks`) is locked down by corporate IT / Group Policy.
- **Dual Monitoring**:
  - **Rate-Limit Burndown**: Real-time 5-hour session and 7-day weekly rate limit tracking, linear pacing, burn rate (%/hr), and runway to reserve.
  - **Token Usage Ledger**: Local SQLite database recording exact input, cache read, cache creation, output, and extended thinking/reasoning tokens (Claude 3.7 Sonnet).
- **Subagents & Workflows**: Full token attribution across Claude Code main threads, subagents (`/subagents/`), and workflows (`/workflows/`).
- **Raw Prompt & Response Viewer**: Inspect and download recent prompts and responses on demand directly from local transcripts.

---

## Quick Start

```powershell
# Start the live loopback service & dashboard (http://127.0.0.1:8787/)
py -B claude-burndown.py serve

# Query live capacity as JSON
py -B claude-burndown.py capacity --json

# View token accounting for the past 7 days
py -B claude-burndown.py usage --days 7

# Install Claude Code statusline hook
py -B claude-burndown.py install --statusline --apply

# Install autostart service (auto-detects Task Scheduler vs. Enterprise Startup folder)
py -B claude-burndown.py install --service --apply
```

---

## Architecture & Data Flow

```
[ Claude Code CLI ] ---- (statusline stdin) ----> [ statusline-handoff/ ]
         |                                                 |
         v (transcripts)                                   v
[ ~/.claude/projects/ ] ---> [ usage.sqlite ] <--- [ claude-burndown service ]
                                                           |
[ Claude Desktop ] -------- (plan-usage-history) --------> +--> [ GET /v1/capacity ]
                                                           +--> [ GET /v1/usage ]
                                                           +--> [ HTML Dashboard ]
```

1. **Statusline Handoff**: When Claude Code updates its statusline, `claude-burndown statusline` captures `rate_limits` from stdin into an atomic, deduplicated handoff file (`~/.claude-burndown/statusline-handoff/`).
2. **Desktop History**: If Claude Desktop is present, usage percentages are incrementally ingested from `plan-usage-history.json` (supporting both regular AppData and virtualized MSIX LocalCache).
3. **Usage Ledger**: Background worker periodically indexes `.jsonl` session transcripts under `~/.claude/projects/` into `usage.sqlite`.
4. **Dashboard**: A live, self-contained dashboard served at `http://127.0.0.1:8787/` with real-time SSE stream (`/v1/capacity/events`).

---

## CLI Reference

### `serve`
Runs the loopback HTTP service.
```powershell
py -B claude-burndown.py serve [--host 127.0.0.1] [--port 8787] [--supervise]
```

### `capacity`
Reads current capacity snapshot.
```powershell
py -B claude-burndown.py capacity           # Human-readable summary
py -B claude-burndown.py capacity --json    # Raw JSON snapshot
py -B claude-burndown.py capacity --watch   # Streaming JSON lines
```

### `usage`
Queries the SQLite token usage ledger.
```powershell
py -B claude-burndown.py usage --days 7
py -B claude-burndown.py usage --by model_effort,day
py -B claude-burndown.py usage --since 2026-09-01 --until 2026-09-15
py -B claude-burndown.py usage --csv export.csv
```

### `backfill`
Indexes Claude Code transcripts.
```powershell
py -B claude-burndown.py backfill --usage
py -B claude-burndown.py backfill --rescan   # Re-index all files
```

### `install` / `uninstall`
Configures Claude Code and Windows logon autostart.
```powershell
py -B claude-burndown.py install --all --apply
py -B claude-burndown.py uninstall --apply
```

---

## Claude Code Plugin & Skill

Claude Burndown includes a ready-to-use Claude Code plugin:
- Definition: `.claude-plugin/plugin.json`
- Skill: `skills/burndown/SKILL.md` (`/burndown`)

To load the skill in Claude Code:
```powershell
claude --plugin-dir c:\Users\rdpro\Projects\claude-burndown
```

---

## Running Tests

```powershell
py -B -m pytest -q
```
