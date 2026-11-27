"""A one-page daily view of the bot: today's trade, the running P&L, results per
strategy, and the latest trades. Writes a self-contained reports/dashboard.html.

    python dashboard.py        # rebuild it from the journal (daily_bot.py also does this)
"""

import html
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import daily_config as cfg
from strategies import STRATEGIES

PROJECT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = PROJECT_DIR / "reports"
JOURNAL_FILE = PROJECT_DIR / "data" / "daily_journal.json"

CSS = """
:root { --bg:#f7f7f5; --card:#fff; --text:#1d1d1b; --muted:#6b6b66; --line:#e4e4df;
        --up:#1a7f4b; --down:#c0392b; --accent:#2f5bd3; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151514; --card:#1f1f1d; --text:#ecece8; --muted:#9a9a93; --line:#33332f;
          --up:#4cc38a; --down:#ef6b5b; --accent:#7b9cff; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width:960px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:16px; margin:0 0 12px; }
.sub { color:var(--muted); margin:0 0 20px; font-size:13px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; margin-bottom:16px; }
.kpi { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.kpi .label { color:var(--muted); font-size:12px; } .kpi .value { font-size:22px; font-weight:600; }
.kpi .value.text { font-size:15px; line-height:1.3; padding-top:4px; }
.up { color:var(--up); } .down { color:var(--down); } .muted { color:var(--muted); }
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
th { color:var(--muted); font-weight:500; } td.num, th.num { text-align:right; }
.today { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:8px 16px; }
.today div span { display:block; color:var(--muted); font-size:12px; }
.pill { display:inline-block; padding:1px 8px; border-radius:99px; font-size:12px; border:1px solid var(--line); }
a { color:var(--accent); } svg { width:100%; height:auto; display:block; }
"""


def esc(x):
    return html.escape(str(x))


def money(x, sign=False):
    if x is None or pd.isna(x):
        return "–"
    return f"{'+' if sign and x > 0 else ''}{'-' if x < 0 else ''}${abs(x):,.2f}"


def pct(x):
    return "–" if x is None or pd.isna(x) else f"{x:+.2%}"


def tone(x):
    if x is None or pd.isna(x) or x == 0:
        return ""
    return "up" if x > 0 else "down"


def equity_svg(dates, pnl):
    """Cumulative P&L line as inline SVG."""
    cum = pnl.cumsum().tolist()
    if len(cum) < 2:
        return '<p class="muted">The chart appears after the second closed trade.</p>'
    w, h, pad, left = 900, 220, 36, 80
    lo, hi = min(0, min(cum)), max(0, max(cum))
    span = (hi - lo) or 1
    x = lambda i: left + i * (w - left - pad) / (len(cum) - 1)
    y = lambda v: h - pad - (v - lo) * (h - 2 * pad) / span
    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(cum))
    color = "var(--up)" if cum[-1] >= 0 else "var(--down)"
    return f"""<svg viewBox="0 0 {w} {h}" role="img" aria-label="Cumulative P&L">
  <line x1="{left}" x2="{w - pad}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="var(--line)" stroke-dasharray="4 4"/>
  <polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>
  <text x="{left}" y="{h - 10}" fill="var(--muted)" font-size="12">{esc(dates[0])}</text>
  <text x="{w - pad}" y="{h - 10}" fill="var(--muted)" font-size="12" text-anchor="end">{esc(dates[-1])}</text>
  <text x="{left - 8}" y="{y(hi) + 4:.1f}" fill="var(--muted)" font-size="12" text-anchor="end">{money(hi)}</text>
  <text x="{left - 8}" y="{y(0) + 4:.1f}" fill="var(--muted)" font-size="12" text-anchor="end">$0</text>
  <text x="{left - 8}" y="{y(lo) + 4:.1f}" fill="var(--muted)" font-size="12" text-anchor="end">{money(lo)}</text>
  <text x="{w - pad}" y="{y(cum[-1]) - 8:.1f}" fill="{color}" font-size="13" font-weight="600" text-anchor="end">{money(cum[-1], True)}</text>
</svg>"""


def strategy_rows(df):
    rows = []
    for name in cfg.STRATEGY_ROTATION:
        s = df[df["strategy"] == name] if not df.empty else df
        title = esc(STRATEGIES[name]["title"])
        if s.empty:
            rows.append(f'<tr><td>{title}</td><td class="num">0</td>' + '<td class="num muted">–</td>' * 4 + "</tr>")
            continue
        total = s["pnl"].sum()
        rows.append(
            f'<tr><td>{title}</td><td class="num">{len(s)}</td>'
            f'<td class="num">{(s["pnl"] > 0).mean():.0%}</td>'
            f'<td class="num {tone(total)}">{money(total, True)}</td>'
            f'<td class="num {tone(s["pnl_pct"].mean())}">{pct(s["pnl_pct"].mean())}</td>'
            f'<td class="num">{s["r_multiple"].mean():+.2f}R</td></tr>')
    return "\n".join(rows)


def today_card(trade, next_name):
    if trade is None:
        spec = STRATEGIES[next_name]
        return (f'<div class="card"><h2>Next trade</h2><p>No trade today yet. Next up: '
                f'<strong>{esc(spec["title"])}</strong>, {esc(spec["idea"])}</p></div>')
    spec = STRATEGIES[trade["strategy"]]
    status = trade["status"]
    result = (f'<div><span>Result</span><strong class="{tone(trade["pnl"])}">{money(trade["pnl"], True)} '
              f'({pct(trade["pnl_pct"])})</strong></div>'
              f'<div><span>Exit</span>{esc(trade["exit_reason"] or "–")} @ {money(trade["exit_price"])}</div>'
              if status == "closed" else
              f'<div><span>Result</span><span class="pill">{esc(status)}</span></div>')
    report = f'{trade["date"]}_{trade["symbol"]}.md'
    return f"""<div class="card"><h2>Latest trade · {esc(trade['date'])}</h2>
<div class="today">
  <div><span>Stock</span><strong>{esc(trade['symbol'])}</strong> × {trade['qty']:g}</div>
  <div><span>Strategy</span>{esc(spec['title'])}</div>
  <div><span>Setup</span>{esc(trade['setup'].split(' ')[0])}</div>
  <div><span>Entry</span>{money(trade['entry_price'] or trade['ref_price'])}</div>
  <div><span>Stop / target</span>{money(trade['stop_price'])} / {money(trade['target_price'])}</div>
  {result}
</div>
<p class="muted" style="margin:12px 0 0">Why this stock: <a href="{esc(report)}">{esc(report)}</a></p></div>"""


def open_card(journal):
    rows = []
    today = datetime.now().date()
    for t in journal:
        if t["status"] not in ("open", "closing"):
            continue
        # Weekdays held so far, today included (holidays not excluded).
        day = int(np.busday_count(t["date"], today.isoformat())) + 1
        rows.append(
            f'<tr><td><strong>{esc(t["symbol"])}</strong> × {t["qty"]:g}</td>'
            f'<td>{esc(STRATEGIES[t["strategy"]]["title"])}</td><td>{esc(t["date"])}</td>'
            f'<td class="num">{money(t["entry_price"] or t["ref_price"])}</td>'
            f'<td class="num">{money(t["stop_price"])}</td><td class="num">{money(t["target_price"])}</td>'
            f'<td class="num">{min(day, cfg.MAX_HOLD_DAYS)} of {cfg.MAX_HOLD_DAYS}</td></tr>')
    if not rows:
        return ""
    return f"""<div class="card"><h2>Open positions ({len(rows)})</h2><div class="table-wrap"><table>
<tr><th>Stock</th><th>Strategy</th><th>Bought</th><th class="num">Entry</th><th class="num">Stop</th>
<th class="num">Target</th><th class="num">Day</th></tr>
{"".join(rows)}
</table></div></div>"""


def build(journal, out=REPORTS_DIR / "dashboard.html", title="Daily Bot"):
    df = pd.DataFrame([t for t in journal if t["status"] == "closed"])
    real = [t for t in journal if t["status"] != "not_filled"]
    next_name = cfg.STRATEGY_ROTATION[len(real) % len(cfg.STRATEGY_ROTATION)]
    latest = journal[-1] if journal else None

    if df.empty:
        kpis = '<div class="kpi"><div class="label">Closed trades</div><div class="value">0</div></div>'
        chart = '<p class="muted">No closed trades yet. The chart starts after the first day.</p>'
        recent = '<tr><td colspan="7" class="muted">Nothing yet.</td></tr>'
    else:
        total = df["pnl"].sum()
        last = df.iloc[-1]
        best = df.groupby("strategy")["pnl"].sum().idxmax()
        kpis = "".join(f'<div class="kpi"><div class="label">{label}</div><div class="value {cls}">{value}</div></div>'
                       for label, value, cls in [
                           ("Total P&L", money(total, True), tone(total)),
                           ("Last trade", money(last["pnl"], True), tone(last["pnl"])),
                           ("Win rate", f'{(df["pnl"] > 0).mean():.0%}', ""),
                           ("Trades", str(len(df)), ""),
                           ("Best strategy so far", esc(STRATEGIES[best]["title"]), "text"),
                       ])
        chart = equity_svg(df["date"].tolist(), df["pnl"])
        recent = "\n".join(
            f'<tr><td>{esc(t.date)}</td><td><strong>{esc(t.symbol)}</strong></td>'
            f'<td>{esc(STRATEGIES[t.strategy]["title"])}</td><td>{esc(t.setup.split(" ")[0])}</td>'
            f'<td>{esc(t.exit_reason)}</td><td class="num {tone(t.pnl)}">{money(t.pnl, True)}</td>'
            f'<td class="num {tone(t.pnl_pct)}">{pct(t.pnl_pct)}</td></tr>'
            for t in df.iloc[::-1].head(15).itertuples())

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body><main>
<h1>{esc(title)}</h1>
<p class="sub">Paper account · ${cfg.POSITION_DOLLARS:,} per trade · held up to {cfg.MAX_HOLD_DAYS} days · updated {datetime.now():%Y-%m-%d %H:%M}</p>
<div class="kpis">{kpis}</div>
{today_card(latest, next_name)}
{open_card(journal)}
<div class="card"><h2>Cumulative P&amp;L</h2>{chart}</div>
<div class="card"><h2>By strategy</h2><div class="table-wrap"><table>
<tr><th>Strategy</th><th class="num">Trades</th><th class="num">Win rate</th><th class="num">P&amp;L</th>
<th class="num">Avg %</th><th class="num">Avg R</th></tr>
{strategy_rows(df)}
</table></div></div>
<div class="card"><h2>Recent trades</h2><div class="table-wrap"><table>
<tr><th>Date</th><th>Stock</th><th>Strategy</th><th>Setup</th><th>Exit</th><th class="num">P&amp;L</th><th class="num">%</th></tr>
{recent}
</table></div></div>
</main></body></html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


if __name__ == "__main__":
    import json
    journal = json.loads(JOURNAL_FILE.read_text()) if JOURNAL_FILE.exists() else []
    print(build(journal))
