---
name: burndown
description: Read live Claude rate-limit capacity and freshness, inspect token accounting, and view local burndown. Use for allowance remaining, burn rate, reserves, session/model usage or /burndown.
---

# Live Claude Capacity and Token Usage

Read capacity and rate-limit status:

```powershell
py -B "${CLAUDE_PLUGIN_ROOT}/claude-burndown.py" capacity --json
```

Report Claude session and weekly allowance remaining, reserve-adjusted allowance, burn rate, runway, and freshness.

For token accounting across models, subagents, and sessions:

```powershell
py -B "${CLAUDE_PLUGIN_ROOT}/claude-burndown.py" usage --days 7
```

To view the live web dashboard, check the URL in `~/.claude-burndown/capacity-service.json` (default `http://127.0.0.1:8787/`). If the service is not currently running, start it with:

```powershell
py -B "${CLAUDE_PLUGIN_ROOT}/claude-burndown.py" serve
```

To install or configure Claude Code statusline:

```powershell
py -B "${CLAUDE_PLUGIN_ROOT}/claude-burndown.py" install --statusline --apply
```
