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

## Step-by-Step Setup Guide

### 1. Prerequisites
- **Python 3.10 or newer**: Check with `py --version` or `python3 --version`.
- **Zero package dependencies**: Standard library only. No `pip install`, no virtual environments required.
- **Claude Code CLI** and/or **Claude Desktop** signed in with your Enterprise credentials.

### 2. Verify Your Environment & Backfill Existing Usage
Navigate to the directory and run an initial backfill to index your historical Claude Code transcripts:

```powershell
cd c:\Users\rdpro\Projects\claude-burndown
py -B claude-burndown.py backfill --usage
```

Then check that your tokens are indexed:

```powershell
py -B claude-burndown.py usage --days 7
```

You should see a table breakdown by model, effort level, daily spend, and cache hit rates.

### 3. Connect to Claude Code Statusline
To display live rate limits in your Claude Code CLI prompt (and capture live quota readings whenever Claude Code redraws):

```powershell
# Preview what will change:
py -B claude-burndown.py install --statusline

# Apply the change to ~/.claude/settings.json:
py -B claude-burndown.py install --statusline --apply
```

This registers a lightweight hook in `~/.claude/settings.json`:
```json
"statusLine": {
  "type": "command",
  "command": "py \"c:\\Users\\...\\claude-burndown.py\" statusline",
  "refreshInterval": 60
}
```
*Note: If you already have a custom status line configured, pass `--force` to replace it.*

### 4. Enable Background Service & Live Dashboard
The local service runs on `http://127.0.0.1:8787/` to serve the live dashboard, update rate limits via Server-Sent Events (SSE), and ingest transcripts.

You can launch it interactively or install it as a background service that automatically launches at logon:

#### Option A: Run manually on-demand
```powershell
py -B claude-burndown.py serve
```
Open `http://127.0.0.1:8787/` in your browser.

#### Option B: Install background autostart (Recommended)
```powershell
py -B claude-burndown.py install --service --apply
```
- **On Standard Windows machines**: Registers `ClaudeBurndownService` in Windows Task Scheduler to start at logon.
- **On Enterprise Restricted Laptops (No Admin / GPO Restricted)**: If `schtasks` returns *Access Denied* due to enterprise group policies, the installer **automatically falls back** to creating a non-privileged Startup supervisor shortcut in your per-user Startup folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`). No administrator permissions required!

#### Option C: Install Both at Once
```powershell
py -B claude-burndown.py install --all --apply
```

### 5. Enable the `/burndown` Skill in Claude Code
Claude Burndown includes a skill definition that lets Claude Code query its own quota directly inside chat:

Launch Claude Code pointing to this plugin directory:
```powershell
claude --plugin-dir c:\Users\rdpro\Projects\claude-burndown
```
Inside Claude Code, you can now type `/burndown` or ask questions like *"how much quota do I have left?"*.

---

## Enterprise Environment Guide

### Restricted Permissions (No Admin Rights)
Many enterprise workstations restrict PowerShell execution or disallow Task Scheduler (`schtasks`).
- `claude-burndown` never requires administrative privileges.
- When `install --service --apply` detects that Task Scheduler is blocked, it writes a hidden supervisor shortcut into your user's Startup folder.
- If background services are completely prohibited by company policy, simply use on-demand commands (`claude-burndown capacity`, `claude-burndown usage`) or run `py -B claude-burndown.py serve` in a terminal when you want the browser dashboard.

### Corporate Proxies & VPNs
Because the service binds strictly to loopback (`127.0.0.1`), corporate proxy intercepts could occasionally interfere with `localhost` requests.
- The built-in client automatically bypasses system proxies for loopback connections.
- If your enterprise environment forces all traffic through an HTTP proxy, ensure `NO_PROXY` includes loopback:
  ```powershell
  $env:NO_PROXY = "127.0.0.1,localhost"
  ```

### Custom Transcript Locations
If your enterprise deployment configures Claude Code to save files on a specific network drive or redirected profile:
- Set `CLAUDE_CONFIG_DIR` to your custom configuration path (default: `~/.claude`).
- Set `CLAUDE_PROJECTS_DIR` if your session transcripts are redirected outside `~/.claude/projects/`.

### Multiple Enterprise Organizations / SSO
If you switch between enterprise organizations or workspaces:
- The handoff engine scopes rate-limit samples by `organization_id`.
- Quota metrics from different organizations are kept distinct and do not corrupt one another.

---

## Daily Usage & CLI Reference

### Query Capacity
```powershell
py -B claude-burndown.py capacity           # Human-readable summary
py -B claude-burndown.py capacity --json    # Raw JSON for scripts/orchestrators
py -B claude-burndown.py capacity --watch   # Live JSON-line stream
```

### Query Token Ledger
```powershell
py -B claude-burndown.py usage --days 7                         # Past 7 days
py -B claude-burndown.py usage --by model_effort,day            # Grouped by model & day
py -B claude-burndown.py usage --since 2026-09-01 --until 2026-09-15
py -B claude-burndown.py usage --csv token-export.csv           # Export raw records to CSV
```

### Static Dashboard Export
If you cannot run a background web server, you can generate a standalone static HTML file anytime:
```powershell
py -B claude-burndown.py render
# Generated at ~/.claude-burndown/burndown.html
```

### Uninstallation
To cleanly revert all changes:
```powershell
py -B claude-burndown.py uninstall --apply
```
This removes the `statusLine` configuration from `~/.claude/settings.json` and deletes any Task Scheduler or Startup folder supervisor shortcuts.

---

## Running Automated Tests

To verify everything on your system:
```powershell
py -B -m pytest -q
```

---

## Recreating from Scratch (Agent & Developer Blueprint)

If you are an agent or developer looking to recreate this system on another machine from scratch (or adapt it to a different tech stack such as FastAPI, Node.js, or Go), open the interactive guide:

- **Interactive Checklist & Architecture Blueprint**: [`RECREATE.html`](RECREATE.html)
- Includes step-by-step phases, code snippets, database schemas, burndown formulas, and persistent checkboxes that save your progress as you build.

