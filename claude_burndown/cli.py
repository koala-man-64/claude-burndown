"""Command-line interface for Claude Burndown (Enterprise Edition)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, client, install, ledger, render, statusline, usage, usage_report
from .config import DEFAULT_PORT, get_paths
from .providers import claude_desktop
from .service import LoopbackService, supervise
from .store import Store
from .util import atomic_write_text, now_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude-burndown", description="Enterprise Claude rate limit burndown and token usage ledger.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--home", type=Path, help="Data directory (default: ~/.claude-burndown)")
    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    p_serve = sub.add_parser("serve", help="Run the loopback capacity service and live dashboard")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port (default: 8787)")
    p_serve.add_argument("--supervise", action="store_true", help="Run user supervisor fallback")

    # capacity
    p_cap = sub.add_parser("capacity", help="Query or watch capacity")
    p_cap.add_argument("--json", action="store_true", help="Output raw JSON snapshot")
    p_cap.add_argument("--watch", action="store_true", help="Stream updates as JSON lines")
    p_cap.add_argument("--url", help="Service URL override")

    # statusline
    p_status = sub.add_parser("statusline", help="Claude Code statusLine hook")
    p_status.add_argument("--no-color", action="store_true", help="Disable ANSI color codes")

    # usage
    p_usage = sub.add_parser("usage", help="Report token usage from the ledger")
    p_usage.add_argument("--days", type=int, default=7, help="Rolling days window (default: 7)")
    p_usage.add_argument("--since", help="Start date (YYYY-MM-DD)")
    p_usage.add_argument("--until", help="End date (YYYY-MM-DD)")
    p_usage.add_argument("--by", default="model_effort,day", help="Comma-separated dimensions")
    p_usage.add_argument("--raw", action="store_true", help="Show exact token counts")
    p_usage.add_argument("--json", action="store_true", help="Output JSON")
    p_usage.add_argument("--csv", type=Path, help="Export events to CSV file")

    # backfill
    p_backfill = sub.add_parser("backfill", help="Scan and index Claude Code transcripts")
    p_backfill.add_argument("--usage", action="store_true", default=True, help="Index transcripts")
    p_backfill.add_argument("--since-days", type=float, default=30, help="Scan transcripts modified in last N days")
    p_backfill.add_argument("--rescan", action="store_true", help="Force re-scan of all files")

    # collect
    p_collect = sub.add_parser("collect", help="Collect samples and render fallback")
    p_collect.add_argument("--render", action="store_true", help="Render static burndown.html")

    # render
    p_render = sub.add_parser("render", help="Generate static burndown.html")

    # install
    p_install = sub.add_parser("install", help="Configure Claude Code statusline or background autostart")
    p_install.add_argument("--statusline", action="store_true", help="Install Claude Code statusLine")
    p_install.add_argument("--service", action="store_true", help="Install autostart background service")
    p_install.add_argument("--all", action="store_true", help="Install both statusline and service")
    p_install.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    p_install.add_argument("--force", action="store_true", help="Overwrite existing statusline configuration")

    # uninstall
    p_un = sub.add_parser("uninstall", help="Uninstall autostart service and statusline")
    p_un.add_argument("--apply", action="store_true", help="Apply removal")

    args = parser.parse_args(argv)
    paths = get_paths(args.home)

    if args.command == "serve":
        if args.supervise:
            supervise(paths, host=args.host, port=args.port)
            return 0
        service = LoopbackService(paths, host=args.host, port=args.port)
        try:
            print(f"Claude Burndown service listening on http://{args.host}:{args.port}/")
            service.start()
        except KeyboardInterrupt:
            service.stop()
        return 0

    elif args.command == "capacity":
        if args.watch:
            for item in client.watch_capacity(paths.home, args.url):
                sys.stdout.write(json.dumps(item) + "\n")
                sys.stdout.flush()
            return 0
        snap = client.read_capacity(paths.home, args.url)
        if args.json:
            print(json.dumps(snap, indent=2))
        else:
            state = snap.get("service_state", "unknown")
            print(f"Claude Capacity: {state}")
            for pool in snap.get("pools", []):
                for w in pool.get("windows", []):
                    used = w.get("used_pct")
                    used_s = f"{used:.1f}%" if used is not None else "N/A"
                    print(f"  {w.get('window')}: used {used_s}, status {w.get('allowance_state')}, resets {w.get('resets_at')}")
        return 0

    elif args.command == "statusline":
        stdin_text = sys.stdin.read() if not sys.stdin.isatty() else ""
        store = Store(paths)
        line = statusline.run(stdin_text, store, color=not args.no_color)
        print(line)
        return 0

    elif args.command == "usage":
        conn = ledger.connect(paths.usage_db)
        try:
            if args.csv:
                rows = usage_report.export_rows(conn, days=args.days, since=args.since, until=args.until)
                usage_report.write_csv(args.csv, rows)
                print(f"Exported {len(rows)} events to {args.csv}")
                return 0
            if args.json:
                data = usage_report.payload(conn, days=args.days)
                print(json.dumps(data, indent=2))
                return 0
            dims = [d.strip() for d in args.by.split(",") if d.strip()]
            text = usage_report.render_text(conn, days=args.days, since=args.since, until=args.until, dimensions=dims, raw=args.raw)
            print(text)
        finally:
            conn.close()
        return 0

    elif args.command == "backfill":
        conn = ledger.connect(paths.usage_db)
        try:
            if args.rescan:
                dropped = ledger.forget_scans(conn)
                print(f"Reset scan cache ({dropped} files)")
            candidates = usage.claude.discover(since_days=args.since_days)
            print(f"Found {len(candidates)} Claude Code transcripts...")
            scanned = 0
            for path, size, mtime in candidates:
                if ledger.file_changed(conn, path, size, mtime):
                    events = usage.claude.parse_file(path)
                    ledger.upsert(conn, events)
                    ledger.mark_scanned(conn, path, size, mtime)
                    scanned += 1
            print(f"Scanned and indexed {scanned} new or updated transcript files.")
        finally:
            conn.close()
        return 0

    elif args.command == "collect":
        store = Store(paths)
        samples, _, _ = claude_desktop.collect(paths.claude_desktop_state)
        if samples:
            store.append(samples)
            print(f"Collected {len(samples)} samples from Claude Desktop.")
        else:
            print("No new readings in Claude Desktop history.")
        if args.render:
            html_text = render.render_page(store, now_utc())
            atomic_write_text(paths.html, html_text)
            print(f"Rendered dashboard to {paths.html}")
        return 0

    elif args.command == "render":
        store = Store(paths)
        html_text = render.render_page(store, now_utc())
        atomic_write_text(paths.html, html_text)
        print(f"Rendered dashboard to {paths.html}")
        return 0

    elif args.command == "install":
        if args.all or args.statusline:
            msg = install.install_statusline(apply=args.apply, force=args.force)
            print(msg)
        if args.all or args.service:
            msg = install.install_service(apply=args.apply, home=str(paths.home))
            print(msg)
        if not (args.all or args.statusline or args.service):
            print("Specify --statusline, --service, or --all (add --apply to execute).")
        return 0

    elif args.command == "uninstall":
        lines = install.uninstall(apply=args.apply)
        for line in lines:
            print(line)
        return 0

    return 0
