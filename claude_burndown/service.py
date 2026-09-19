"""Loopback capacity service for Claude: isolated collectors, cached reads, and SSE."""
from __future__ import annotations

import copy
import http.client
import ipaddress
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import ledger, render, request_text, usage, usage_report
from .capacity import CapacityState, Observation
from .config import Paths
from .model import from_latest
from .providers import claude_desktop
from .store import Sample, Store
from .util import WriterLease, atomic_write_text, now_utc, read_json

SERVICE_INFO_NAME = "capacity-service.json"
SERVICE_LOG_NAME = "service.log"
SERVICE_LOG_MAX_BYTES = 1_000_000


def _log_service_event(paths: Paths, message: str, detail: str | None = None) -> None:
    try:
        path = paths.home / SERVICE_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size >= SERVICE_LOG_MAX_BYTES:
            os.replace(path, path.with_name(SERVICE_LOG_NAME + ".1"))
        timestamp = now_utc().isoformat().replace("+00:00", "Z")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
            if detail:
                handle.write(detail.rstrip() + "\n")
    except OSError:
        pass


def _remove_owned_service_info(paths: Paths, pid: int) -> None:
    path = paths.home / SERVICE_INFO_NAME
    if read_json(path, {}).get("pid") != pid:
        return
    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        pass


def _capacity_available(host: str, port: int) -> bool:
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        connection.request("GET", "/v1/capacity")
        response = connection.getresponse()
        response.read()
        return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def supervise(paths: Paths, host="127.0.0.1", port=8787, interval=60, stop_event=None):
    stop_event = stop_event or threading.Event()
    launcher = Path(__file__).resolve().parent.parent / "claude-burndown.py"
    command = [sys.executable, "-B", str(launcher), "--home", str(paths.home), "serve", "--host", host, "--port", str(port)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    child = None
    with WriterLease(paths.home, filename=".claude-service-supervisor.lock"):
        _log_service_event(paths, f"supervisor starting pid={os.getpid()} interval={interval}s")
        try:
            while not stop_event.is_set():
                if not _capacity_available(host, port) and (child is None or child.poll() is not None):
                    child = subprocess.Popen(command, creationflags=flags)
                    _log_service_event(paths, f"supervisor launched service pid={child.pid}")
                stop_event.wait(interval)
        finally:
            _log_service_event(paths, f"supervisor stopped pid={os.getpid()}")


class LoopbackService:
    def __init__(self, paths: Paths, host: str = "127.0.0.1", port: int = 8787):
        self.paths = paths
        self.host = host
        self.port = port
        self.store = Store(paths)
        self.capacity_state = CapacityState(paths.home)
        self.stop_event = threading.Event()
        self._page_cache: bytes | None = None
        self._recent_requests_map: dict[str, sqlite3.Row] = {}
        self._text_slots = threading.Semaphore(4)
        self._server = None

    def start(self):
        self.capacity_state.set_health("claude", "running")
        # Start background collector thread
        collector_thread = threading.Thread(target=self._collector_loop, daemon=True)
        collector_thread.start()

        # Start usage transcript ingest thread
        usage_thread = threading.Thread(target=self._usage_loop, daemon=True)
        usage_thread.start()

        # Initialize and write capacity-service.json
        service_info = {
            "pid": os.getpid(),
            "url": f"http://{self.host}:{self.port}/",
            "host": self.host,
            "port": self.port,
            "started_at": now_utc().isoformat(),
        }
        atomic_write_text(self.paths.home / SERVICE_INFO_NAME, json.dumps(service_info, indent=2))

        handler_cls = self._create_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler_cls)
        try:
            self._server.serve_forever()
        finally:
            self.stop_event.set()
            _remove_owned_service_info(self.paths, os.getpid())

    def stop(self):
        self.stop_event.set()
        if self._server:
            self._server.shutdown()

    def _collector_loop(self):
        while not self.stop_event.is_set():
            try:
                # 1. Drain statusline handoff
                from .handoff import drain

                handoff_items = drain(self.paths.home)
                new_observations: list[Observation] = []
                samples_to_store: list[Sample] = []
                for item in handoff_items:
                    quota = item.get("quota") or {}
                    scope = item.get("account_scope") or "enterprise"
                    obs_ts = item.get("observed_at")
                    for name, q in quota.items():
                        used = q.get("used_percentage")
                        resets = q.get("resets_at")
                        minutes = q.get("window_minutes", 300 if name == "five_hour" else 10080)
                        win_label = "5h" if name == "five_hour" else "7d"
                        from .util import parse_iso
                        dt_obs = parse_iso(obs_ts) or now_utc()
                        dt_reset = parse_iso(resets) if resets else None
                        obs = Observation(
                            provider="claude",
                            account_scope=scope,
                            limit_id="claude",
                            window=win_label,
                            window_min=minutes,
                            used_pct=used,
                            resets_at=dt_reset,
                            observed_at=dt_obs,
                            received_at=now_utc(),
                            source="statusline",
                        )
                        new_observations.append(obs)
                        samples_to_store.append(
                            Sample(
                                ts=dt_obs,
                                provider="claude",
                                window=win_label,
                                used=used,
                                resets_at=dt_reset,
                                window_min=minutes,
                                source="statusline",
                            )
                        )

                # 2. Check desktop history
                desktop_samples, _, _ = claude_desktop.collect(self.paths.claude_desktop_state)
                for s in desktop_samples:
                    obs = Observation(
                        provider="claude",
                        account_scope="desktop",
                        limit_id="claude",
                        window=s.window,
                        window_min=s.window_min,
                        used_pct=s.used,
                        resets_at=s.resets_at,
                        observed_at=s.ts,
                        received_at=now_utc(),
                        source="desktop",
                    )
                    new_observations.append(obs)
                    samples_to_store.append(s)

                if new_observations:
                    self.capacity_state.ingest(new_observations)
                    self.capacity_state.publish(persist=True)
                if samples_to_store:
                    self.store.append(samples_to_store)

                self._invalidate_page_cache()
            except Exception as exc:
                _log_service_event(self.paths, f"collector error: {exc}", traceback.format_exc())
            self.stop_event.wait(5)

    def _usage_loop(self):
        while not self.stop_event.is_set():
            try:
                conn = ledger.connect(self.paths.usage_db)
                try:
                    candidates = usage.claude.discover()
                    for path, size, mtime in candidates:
                        if ledger.file_changed(conn, path, size, mtime):
                            events = usage.claude.parse_file(path)
                            ledger.upsert(conn, events)
                            ledger.mark_scanned(conn, path, size, mtime)
                finally:
                    conn.close()
                self._invalidate_page_cache()
            except Exception as exc:
                _log_service_event(self.paths, f"usage loop error: {exc}", traceback.format_exc())
            self.stop_event.wait(15)

    def _invalidate_page_cache(self):
        self._page_cache = None

    def _get_page(self) -> bytes:
        if self._page_cache is None:
            snapshot = self.capacity_state.read()
            html_text = render.render_page(self.store, now_utc(), snapshot, self.capacity_state.reserve_pct)
            self._page_cache = html_text.encode("utf-8")
            # Update recent requests lookup
            conn = ledger.connect(self.paths.usage_db)
            try:
                reqs = ledger.recent_requests(conn, limit=50)
                self._recent_requests_map = {request_text.row_id(r): r for r in reqs}
            finally:
                conn.close()
        return self._page_cache

    def _create_handler(service_self):
        class ServiceHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _trusted(self) -> bool:
                host_val = self.headers.get("Host", "").split(":")[0]
                if host_val not in ("127.0.0.1", "localhost", "::1"):
                    return False
                origin = self.headers.get("Origin")
                if origin:
                    parts = urlsplit(origin)
                    if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
                        return False
                return True

            def _send(self, code: int, ctype: str, body: bytes, headers: dict | None = None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                if headers:
                    for k, v in headers.items():
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._trusted():
                    self._send(403, "text/plain", b"Access denied: loopback host required")
                    return
                url = urlsplit(self.path)
                path = url.path
                query = parse_qs(url.query)

                if path in ("/", "/index.html", "/burndown.html"):
                    page = service_self._get_page()
                    self._send(200, "text/html; charset=utf-8", page)
                elif path == "/v1/capacity":
                    snap = service_self.capacity_state.read()
                    self._send(200, "application/json; charset=utf-8", json.dumps(snap).encode())
                elif path == "/v1/capacity/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    cur_rev = -1
                    try:
                        while not service_self.stop_event.is_set():
                            snap = service_self.capacity_state.wait(cur_rev, timeout=10)
                            if snap.get("revision") != cur_rev:
                                cur_rev = snap.get("revision")
                                data = json.dumps(snap)
                                self.wfile.write(f"data: {data}\n\n".encode())
                                self.wfile.flush()
                            else:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                elif path == "/v1/usage":
                    conn = ledger.connect(service_self.paths.usage_db)
                    try:
                        data = usage_report.payload(conn)
                    finally:
                        conn.close()
                    self._send(200, "application/json; charset=utf-8", json.dumps(data).encode())
                elif path.startswith("/v1/recent-text/download"):
                    conn = ledger.connect(service_self.paths.usage_db)
                    try:
                        reqs = ledger.recent_requests(conn, limit=100)
                    finally:
                        conn.close()
                    fmt = query.get("format", ["json"])[0]
                    items = []
                    for r in reqs:
                        rid = request_text.row_id(r)
                        text_data = request_text.read_request(r)
                        items.append({"id": rid, "model": r["model"], "ts": r["ts"], **text_data})
                    if fmt == "jsonl":
                        body = "\n".join(json.dumps(i) for i in items).encode()
                        self._send(
                            200,
                            "application/x-ndjson",
                            body,
                            {"Content-Disposition": 'attachment; filename="recent-requests.jsonl"'},
                        )
                    else:
                        body = json.dumps(items, indent=2).encode()
                        self._send(
                            200,
                            "application/json",
                            body,
                            {"Content-Disposition": 'attachment; filename="recent-requests.json"'},
                        )
                elif path.startswith("/v1/recent-text/"):
                    row_id = path.removeprefix("/v1/recent-text/")
                    row = service_self._recent_requests_map.get(row_id)
                    if not row:
                        self._send(404, "application/json", b'{"status":"unavailable","note":"Request not found"}')
                        return
                    result = request_text.read_request(row)
                    fmt = query.get("format", ["json"])[0]
                    if fmt == "txt":
                        txt = f"PROMPT:\n{result.get('request', '')}\n\nRESPONSE:\n{result.get('response', '')}\n"
                        self._send(
                            200,
                            "text/plain; charset=utf-8",
                            txt.encode(),
                            {"Content-Disposition": f'attachment; filename="request-{row_id[:8]}.txt"'},
                        )
                    else:
                        self._send(200, "application/json; charset=utf-8", json.dumps(result).encode())
                elif path == "/latest.json":
                    latest = service_self.store.latest()
                    body = json.dumps({k: v.to_dict() for k, v in latest.items()}, indent=2).encode()
                    self._send(200, "application/json", body)
                elif path == "/status.json":
                    snap = service_self.capacity_state.read()
                    self._send(200, "application/json", json.dumps(snap).encode())
                else:
                    self._send(404, "text/plain", b"Not found")

            def do_POST(self):
                if not self._trusted():
                    self._send(403, "text/plain", b"Access denied")
                    return
                url = urlsplit(self.path)
                if url.path == "/v1/policy":
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        payload = json.loads(self.rfile.read(length))
                        reserve = int(payload.get("reserve_pct", 10))
                        service_self.capacity_state.set_policy(reserve)
                        service_self.capacity_state.publish(persist=True)
                        service_self._invalidate_page_cache()
                        self._send(200, "application/json", b'{"status":"ok"}')
                    except Exception as exc:
                        self._send(400, "application/json", json.dumps({"error": str(exc)}).encode())
                else:
                    self._send(404, "text/plain", b"Not found")

        return ServiceHandler
