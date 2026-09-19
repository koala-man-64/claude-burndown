"""Self-contained HTML dashboard for Claude Enterprise rate limits and token usage."""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, charts, ledger, request_text, usage_report
from .charts import ChartData
from .model import Burndown, canonical_samples, current
from .store import Sample, Store
from .util import fmt_local, fmt_minutes, iso, now_utc, to_local

CHART_W, CHART_H = 800, 320
PAD_L, PAD_R, PAD_T, PAD_B = 48, 56, 30, 30
TOKEN_SLOTS = 7


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _x_of(data: ChartData, ts: datetime) -> float:
    total = (data.span_end - data.span_start).total_seconds() or 1.0
    frac = (ts - data.span_start).total_seconds() / total
    return PAD_L + (CHART_W - PAD_L - PAD_R) * min(max(frac, 0.0), 1.0)


def _y_of(used: float) -> float:
    return PAD_T + (CHART_H - PAD_T - PAD_B) * (1 - min(max(used, 0.0), 100.0) / 100)


def _token_y(data: ChartData, tokens: int) -> float:
    max_t = data.token_max or 1
    frac = min(max(tokens / max_t, 0.0), 1.0)
    plot_h = CHART_H - PAD_T - PAD_B
    return (PAD_T + plot_h) - (plot_h * 0.45 * frac)


def _n(value: int) -> str:
    return f"{value:,}"


def _clip(data: ChartData, line: charts.Line) -> tuple[float, float, float, float] | None:
    (t1, y1), (t2, y2) = line.start, line.end
    if t2 < data.span_start or t1 > data.span_end:
        return None
    x1, x2 = _x_of(data, t1), _x_of(data, t2)
    y1_p, y2_p = _y_of(y1), _y_of(y2)
    return x1, y1_p, x2, y2_p


def chart_svg(data: ChartData, title: str, chart_id: str) -> str:
    plot_bottom = CHART_H - PAD_B
    parts = []
    # Grid lines & ticks
    for tick in data.x_ticks:
        x = _x_of(data, tick.ts)
        parts.append(f'<line class="grid-v" x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{plot_bottom:.1f}"/>')
        parts.append(f'<text class="axis-x" x="{x:.1f}" y="{plot_bottom + 18:.1f}" text-anchor="middle">{esc(tick.label)}</text>')

    for y_pct in (0, 25, 50, 75, 100):
        y = _y_of(y_pct)
        parts.append(f'<line class="grid-h" x1="{PAD_L}" y1="{y:.1f}" x2="{CHART_W - PAD_R:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis-y" x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end">{y_pct}%</text>')

    # Token bars
    if data.token_bars:
        for bar in data.token_bars:
            if bar.end <= data.span_start or bar.start >= data.span_end or bar.tokens <= 0:
                continue
            x0, x1 = _x_of(data, bar.start), _x_of(data, bar.end)
            w = max(x1 - x0 - 1.0, 1.0)
            y_top = _token_y(data, bar.tokens)
            parts.append(
                f'<rect class="tok-bar" x="{x0 + 0.5:.1f}" y="{y_top:.1f}" width="{w:.1f}" height="{plot_bottom - y_top:.1f}">'
                f'<title>{_n(bar.tokens)} tokens ({fmt_local(bar.start)} - {fmt_local(bar.end)})</title></rect>'
            )

    # Historical usage segments
    for segment in data.segments:
        coords = [(_x_of(data, p.ts), _y_of(p.used)) for p in segment.points]
        if len(coords) == 1:
            coords.append(coords[0])
        line = " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)
        parts.append(f'<path class="area" d="M {coords[0][0]:.1f} {plot_bottom:.1f} L {line} L {coords[-1][0]:.1f} {plot_bottom:.1f} Z"/>')
        parts.append(f'<path class="used" d="M {line}"/>')

    # Pace & projection lines
    if data.pace is not None:
        clipped = _clip(data, data.pace)
        if clipped:
            parts.append('<line class="pace" x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}"/>'.format(*clipped))
    if data.projection is not None:
        clipped = _clip(data, data.projection)
        if clipped:
            parts.append('<line class="proj" x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}"/>'.format(*clipped))

    # Resets
    for mark in data.resets:
        x = _x_of(data, mark.ts)
        parts.append(f'<line class="reset-tick" x1="{x:.1f}" y1="{PAD_T - 10}" x2="{x:.1f}" y2="{PAD_T}"/>')
        if mark.current:
            anchor = "end" if x > CHART_W * 0.7 else "start"
            dx = -6 if anchor == "end" else 6
            parts.append(f'<text class="reset-lbl" x="{x + dx:.1f}" y="{PAD_T - 14}" text-anchor="{anchor}">resets {esc(fmt_local(mark.ts))}</text>')

    # Current time line
    x_now = _x_of(data, data.now)
    if PAD_L <= x_now <= CHART_W - PAD_R:
        parts.append(f'<line class="now" x1="{x_now:.1f}" y1="{PAD_T}" x2="{x_now:.1f}" y2="{plot_bottom:.1f}"/>')

    label = f"{title} rate-limit burndown chart ({data.span_label})"
    return (
        f'<svg class="chart" viewBox="0 0 {CHART_W} {CHART_H}" role="img" aria-label="{esc(label)}" '
        f'data-chart="{esc(chart_id)}">' + "".join(parts) + "</svg>"
    )


def render_page(
    store: Store,
    now: datetime | None = None,
    capacity_snapshot: dict | None = None,
    reserve_pct: int = 10,
) -> str:
    now = now or now_utc()
    samples = store.read_samples(since=now - timedelta(days=15))
    latest = store.latest()
    burndowns = current(samples, latest, now)

    conn = ledger.connect(store.paths.usage_db)
    try:
        recent = ledger.recent_requests(conn, limit=25)
        usage_sum = usage_report.summary(conn, now)
        eff_models = usage_report.efficiency_rows(ledger.rows(conn, since=now - timedelta(days=7)), "model")
    finally:
        conn.close()

    # Cards for 5h and 7d
    bd_5h = next((b for b in burndowns if b.window == "5h"), None)
    bd_7d = next((b for b in burndowns if b.window == "7d"), None)

    conn_charts = ledger.connect(store.paths.usage_db)
    try:
        c_5h = charts.build(bd_5h, samples, now, conn_charts) if bd_5h else None
        c_7d = charts.build(bd_7d, samples, now, conn_charts) if bd_7d else None
    finally:
        conn_charts.close()

    # CSS styles
    css = """
    :root {
      --bg: #0d1117; --card-bg: #161b22; --border: #30363d; --text: #c9d1d9; --text-muted: #8b949e;
      --heading: #f0f6fc; --accent: #58a6ff; --green: #3fb950; --red: #f85149; --yellow: #d29922;
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: var(--font); padding: 24px 32px; line-height: 1.5; }
    header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; }
    h1 { font-size: 24px; color: var(--heading); font-weight: 600; display: flex; align-items: center; gap: 12px; }
    .badge { font-size: 12px; padding: 2px 8px; border-radius: 12px; background: #238636; color: #fff; font-weight: 500; }
    .meta-hdr { font-size: 13px; color: var(--text-muted); }
    .reserves { display: flex; align-items: center; gap: 8px; font-size: 13px; }
    .btn { background: var(--card-bg); border: 1px solid var(--border); color: var(--text); padding: 4px 10px; border-radius: 6px; cursor: pointer; }
    .btn.active { background: #1f6feb; border-color: #388bfd; color: #fff; }
    .grid-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px; margin-bottom: 24px; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }
    .card h2 { font-size: 16px; color: var(--heading); margin-bottom: 12px; display: flex; justify-content: space-between; }
    .stat-row { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 14px; }
    .stat-val { font-weight: 600; color: var(--heading); font-family: var(--mono); }
    .prog-bar { height: 8px; background: #21262d; border-radius: 4px; overflow: hidden; margin: 12px 0; }
    .prog-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
    .chart-box { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 24px; }
    .chart-box h3 { font-size: 15px; color: var(--heading); margin-bottom: 12px; }
    svg.chart { width: 100%; height: auto; display: block; }
    .grid-h, .grid-v { stroke: #21262d; stroke-width: 1; }
    .axis-x, .axis-y { fill: #8b949e; font-size: 10px; font-family: var(--mono); }
    .used { stroke: #58a6ff; stroke-width: 2.5; fill: none; stroke-linecap: round; }
    .area { fill: rgba(88, 166, 255, 0.08); }
    .pace { stroke: #8b949e; stroke-width: 1.5; stroke-dasharray: 4 4; fill: none; }
    .proj { stroke: #d29922; stroke-width: 2; stroke-dasharray: 5 3; fill: none; }
    .now { stroke: #f85149; stroke-width: 1.5; stroke-dasharray: 2 2; }
    .reset-tick { stroke: #3fb950; stroke-width: 2; }
    .reset-lbl { fill: #3fb950; font-size: 11px; font-weight: 500; }
    .tok-bar { fill: rgba(63, 185, 80, 0.4); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }
    th { background: #161b22; color: var(--text-muted); font-weight: 600; padding: 10px 12px; border-bottom: 1px solid var(--border); }
    td { padding: 8px 12px; border-bottom: 1px solid var(--border); font-family: var(--mono); }
    tr:hover { background: rgba(255,255,255,0.02); }
    .raw-viewer { margin-top: 8px; background: #090d13; border: 1px solid var(--border); border-radius: 6px; padding: 12px; font-size: 12px; white-space: pre-wrap; word-break: break-word; }
    """

    def status_color(used: float, pace: float) -> str:
        if used >= 100:
            return "var(--red)"
        if used > pace + 2:
            return "var(--yellow)"
        return "var(--green)"

    # Render cards
    cards_html = []
    for title, bd, chart in (("Current Session (5-Hour)", bd_5h, c_5h), ("All Models (7-Day Weekly)", bd_7d, c_7d)):
        if bd:
            color = status_color(bd.used, bd.pace)
            usable = max(0.0, 100 - reserve_pct - bd.used)
            resets = fmt_minutes(bd.remaining_min) if bd.remaining_min else "N/A"
            cards_html.append(f"""
            <div class="card">
              <h2>{esc(title)} <span style="color: {color}">{bd.used:.1f}%</span></h2>
              <div class="prog-bar"><div class="prog-fill" style="width: {min(bd.used, 100):.1f}%; background: {color};"></div></div>
              <div class="stat-row"><span>Remaining / Usable:</span><span class="stat-val">{bd.remaining_pct:.1f}% / {usable:.1f}%</span></div>
              <div class="stat-row"><span>Burn Pace / Target:</span><span class="stat-val">{bd.delta:+.1f}% vs p{bd.pace:.0f}</span></div>
              <div class="stat-row"><span>Resets In:</span><span class="stat-val">{resets}</span></div>
              <div class="stat-row"><span>Burn Rate:</span><span class="stat-val">{bd.rate_per_hour:.1f} %/h</span></div>
            </div>
            """)
        else:
            cards_html.append(f"""
            <div class="card">
              <h2>{esc(title)}</h2>
              <p style="color: var(--text-muted); padding: 24px 0;">No active rate-limit readings sampled yet.</p>
            </div>
            """)

    # Render charts
    charts_html = []
    if c_5h:
        charts_html.append(f'<div class="chart-box"><h3>5-Hour Session Burndown</h3>{chart_svg(c_5h, "5h", "c5h")}</div>')
    if c_7d:
        charts_html.append(f'<div class="chart-box"><h3>7-Day Weekly Burndown</h3>{chart_svg(c_7d, "7d", "c7d")}</div>')

    # Models table
    model_rows = []
    for m in eff_models:
        model_rows.append(
            f"<tr><td>{esc(m['key'])}</td><td>{m['requests']:,}</td><td>{m['input']:,}</td>"
            f"<td>{m['cache_read']:,} ({m['cache_hit_rate_pct']}%)</td><td>{m['output']:,}</td>"
            f"<td>{m['reasoning']:,}</td><td><strong>{m['total']:,}</strong></td></tr>"
        )

    # Recent requests
    req_rows = []
    for r in recent:
        rid = request_text.row_id(r)
        req_rows.append(
            f"<tr><td>{esc(r['ts'][:19].replace('T', ' '))}</td><td>{esc(r['model'] or 'Unknown')}</td>"
            f"<td>{r['input_tokens'] or 0:,}</td><td>{r['cache_read_tokens'] or 0:,}</td><td>{r['output_tokens'] or 0:,}</td>"
            f"<td>{r['reasoning_tokens'] or 0:,}</td><td><strong>{r['total_tokens'] or 0:,}</strong></td>"
            f"<td><button class=\"btn\" onclick=\"toggleText('{rid}')\">View Text</button> "
            f"<a class=\"btn\" href=\"/v1/recent-text/{rid}?format=txt\" download=\"request-{rid[:8]}.txt\">Download</a></td></tr>"
            f"<tr id=\"text-row-{rid}\" style=\"display:none;\"><td colspan=\"8\"><div class=\"raw-viewer\" id=\"text-box-{rid}\">Loading...</div></td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Claude Burndown · Enterprise Edition</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <div>
      <h1>Claude Burndown <span class="badge">Enterprise Edition</span></h1>
      <div class="meta-hdr">Generated {esc(iso(now))} · Auto-sync active</div>
    </div>
    <div class="reserves">
      <span>Reserve Buffer:</span>
      <button class="btn {'active' if reserve_pct == 5 else ''}" onclick="setReserve(5)">5%</button>
      <button class="btn {'active' if reserve_pct == 10 else ''}" onclick="setReserve(10)">10%</button>
      <button class="btn {'active' if reserve_pct == 20 else ''}" onclick="setReserve(20)">20%</button>
    </div>
  </header>

  <div class="grid-cards">
    {''.join(cards_html)}
  </div>

  {''.join(charts_html)}

  <div class="chart-box">
    <h3>Token Usage by Model (Past 7 Days)</h3>
    <table>
      <thead>
        <tr><th>Model @ Effort</th><th>Requests</th><th>Input Tokens</th><th>Cache Hits</th><th>Output Tokens</th><th>Thinking Tokens</th><th>Total Tokens</th></tr>
      </thead>
      <tbody>
        {''.join(model_rows) if model_rows else '<tr><td colspan="7" style="color: var(--text-muted);">No request logs ingested yet.</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="chart-box">
    <h3>Recent Claude Code Requests</h3>
    <table>
      <thead>
        <tr><th>Time (UTC)</th><th>Model</th><th>Input</th><th>Cache Read</th><th>Output</th><th>Thinking</th><th>Total</th><th>Actions</th></tr>
      </thead>
      <tbody>
        {''.join(req_rows) if req_rows else '<tr><td colspan="8" style="color: var(--text-muted);">No requests recorded yet.</td></tr>'}
      </tbody>
    </table>
  </div>

  <script>
    function setReserve(pct) {{
      fetch('/v1/policy', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ reserve_pct: pct }}) }})
        .then(() => setTimeout(() => location.reload(), 200));
    }}
    function toggleText(id) {{
      const row = document.getElementById('text-row-' + id);
      const box = document.getElementById('text-box-' + id);
      if (row.style.display === 'none') {{
        row.style.display = 'table-row';
        if (box.textContent === 'Loading...') {{
          fetch('/v1/recent-text/' + id)
            .then(r => r.json())
            .then(data => {{
              if (data.status === 'available' || data.status === 'partial') {{
                box.textContent = 'PROMPT:\\n' + data.request + '\\n\\nRESPONSE:\\n' + data.response;
              }} else {{
                box.textContent = data.note || 'Text unavailable';
              }}
            }})
            .catch(() => {{ box.textContent = 'Error loading prompt/response text.'; }});
        }}
      }} else {{
        row.style.display = 'none';
      }}
    }}
    // SSE stream for real-time updates
    if (window.EventSource) {{
      const ev = new EventSource('/v1/capacity/events');
      ev.onmessage = function(e) {{
        console.log('Capacity updated, refreshing state...');
      }};
    }}
  </script>
</body>
</html>"""
