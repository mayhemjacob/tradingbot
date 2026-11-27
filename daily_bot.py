"""One stock a day, one strategy a day, on the Alpaca paper account.

Each trading day the next strategy in the rotation (see daily_config.py) ranks
the universe, picks one stock, and buys it shortly after the open with a
bracket order (stop-loss + take-profit, good until canceled). A trade that
hits neither is sold just before the close of its MAX_HOLD_DAYS-th trading
day, so up to that many positions can be open at once. Every trade gets a
report in reports/ and a line in the journal.

    python daily_bot.py enter              # pick today's stock and buy it
    python daily_bot.py exit               # sell trades that are due, protect the rest overnight
    python daily_bot.py enter --dry-run    # pick and write the report, no orders
    python daily_bot.py scheduled          # used by launchd: enter at the open, exit at the close
    python daily_bot.py summary            # results per strategy so far
    python daily_bot.py backtest --days 120

Everything is logged to logs/daily_bot.log. Open reports/dashboard.html for the daily view.
"""

import argparse
import json
import logging
import math
import os
import sys
import time as clock
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

import daily_config as cfg
import dashboard
from strategies import STRATEGIES, choose

PROJECT_DIR = Path(__file__).resolve().parent
LOG_FILE = PROJECT_DIR / "logs" / "daily_bot.log"
JOURNAL_FILE = PROJECT_DIR / "data" / "daily_journal.json"
REPORTS_DIR = PROJECT_DIR / "reports"
ET = ZoneInfo("America/New_York")

# launchd fires at two local times per phase to cover daylight-saving
# mismatches; a scheduled run only acts inside these New York time windows.
ENTRY_WINDOW = (time(9, 30), time(10, 15))
EXIT_WINDOW = (time(15, 45), time(15, 58))

log = logging.getLogger("daily_bot")


def setup_logging():
    LOG_FILE.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.addHandler(file_handler)
    log.addHandler(console)


# ---------------------------------------------------------------- data


def fetch_bars(symbols, completed_only=True):
    """Daily OHLCV per symbol. With completed_only, today's forming bar is dropped."""
    raw = yf.download(symbols, period=cfg.HISTORY_PERIOD, interval="1d", auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)
    now = datetime.now(ET)
    bars = {}
    for symbol in symbols:
        df = raw[symbol].dropna() if len(symbols) > 1 else raw.dropna()
        if df.empty:
            log.warning("No price data for %s, skipping it", symbol)
            continue
        df.index = pd.DatetimeIndex(df.index).tz_localize(None)
        if completed_only and df.index[-1].date() == now.date() and now.time() < time(16, 0):
            df = df.iloc[:-1]
        bars[symbol] = df
    return bars


# ---------------------------------------------------------------- journal


def load_journal():
    if JOURNAL_FILE.exists():
        return json.loads(JOURNAL_FILE.read_text())
    return []


def save_journal(journal):
    JOURNAL_FILE.parent.mkdir(exist_ok=True)
    JOURNAL_FILE.write_text(json.dumps(journal, indent=2))
    # A flat copy for spreadsheets.
    REPORTS_DIR.mkdir(exist_ok=True)
    cols = ["date", "strategy", "symbol", "setup", "qty", "entry_price", "exit_price",
            "exit_reason", "pnl", "pnl_pct", "r_multiple", "status"]
    pd.DataFrame(journal, columns=cols).to_csv(REPORTS_DIR / "trades.csv", index=False)
    dashboard.build(journal)


def next_strategy(journal):
    taken = [t for t in journal if t["status"] != "not_filled"]
    return cfg.STRATEGY_ROTATION[len(taken) % len(cfg.STRATEGY_ROTATION)]


# ---------------------------------------------------------------- reports


def money(x):
    return "n/a" if x is None else f"${x:,.2f}"


def write_report(trade, folder=REPORTS_DIR):
    spec = STRATEGIES[trade["strategy"]]
    m = trade["metrics"]
    lines = [
        f"# {trade['date']} · {trade['symbol']} · {spec['title']}",
        "",
        f"**Status:** {trade['status']}" + ("  (dry run, no orders placed)" if trade.get("dry_run") else ""),
        "",
        "## The strategy",
        spec["idea"],
        "",
        f"- Filter: {spec['filter']}",
        f"- Ranking: {spec['rank']}",
        f"- Stop-loss: {spec['stop_atr']} x ATR below entry · Take-profit: {spec['target_atr']} x ATR above",
        "",
        "## Why this stock",
        f"Setup quality: **{trade['setup']}**",
        "",
        "| close | 3d return | 20d return | 10d SMA | 50d SMA | 200d SMA | RSI(14) | 20d high "
        "| volume ratio | range / ATR | ATR(14) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        f"| {m['close']:.2f} | {m['ret_3d']:+.1%} | {m['ret_20d']:+.1%} | {m['sma_10']:.2f} "
        f"| {m['sma_50']:.2f} | {m['sma_200']:.2f} | {m['rsi_14']:.1f} | {m['high_20d']:.2f} "
        f"| {m['vol_ratio']:.2f}x | {m['range_ratio']:.2f} | {m['atr']:.2f} |",
        "",
        "Top candidates (the ranking the strategy saw):",
        "",
        "| # | symbol | passes filter | close | 3d return | 20d return | RSI(14) | volume ratio | range / ATR |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(trade["candidates"], 1):
        lines.append(f"| {i} | {c['symbol']} | {'yes' if c['passes'] else 'no'} | {c['close']:.2f} "
                     f"| {c['ret_3d']:+.1%} | {c['ret_20d']:+.1%} | {c['rsi_14']:.1f} "
                     f"| {c['vol_ratio']:.2f}x | {c['range_ratio']:.2f} |")
    risk = (trade["ref_price"] - trade["stop_price"]) * trade["qty"]
    reward = (trade["target_price"] - trade["ref_price"]) * trade["qty"]
    lines += [
        "",
        "## The plan",
        f"- Buy {trade['qty']} shares near {money(trade['ref_price'])} "
        f"(about {money(trade['qty'] * trade['ref_price'])})",
        f"- Stop-loss {money(trade['stop_price'])} · risk {money(risk)}",
        f"- Take-profit {money(trade['target_price'])} · reward {money(reward)}",
        f"- If neither hits, sold at about 15:50 New York time on trading day {cfg.MAX_HOLD_DAYS}"
        + (" (same day)" if cfg.MAX_HOLD_DAYS == 1 else ", held overnight until then"),
        "",
        "## The result",
    ]
    if trade["status"] == "closed":
        lines += [
            f"- Entry fill: {money(trade['entry_price'])} · Exit fill: {money(trade['exit_price'])}",
            f"- Exit reason: {trade['exit_reason']} on {trade.get('closed_on', trade['date'])}",
            f"- P&L: **{money(trade['pnl'])} ({trade['pnl_pct']:+.2%})** · R multiple: {trade['r_multiple']:+.2f}R",
        ]
    elif trade["status"] == "not_filled":
        lines.append("- The entry order never filled, so there was no trade.")
    else:
        lines.append("- Still open." + (f" Bought at {money(trade['entry_price'])}." if trade["entry_price"] else ""))

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{trade['date']}_{trade['symbol']}.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def stats_table(df):
    """Per-strategy results from a DataFrame with strategy, pnl, pnl_pct, r_multiple."""
    g = df.groupby("strategy")
    out = pd.DataFrame({
        "trades": g.size(),
        "win_rate": g["pnl"].apply(lambda s: (s > 0).mean()),
        "total_pnl": g["pnl"].sum(),
        "avg_pct": g["pnl_pct"].mean(),
        "avg_R": g["r_multiple"].mean(),
        "best": g["pnl"].max(),
        "worst": g["pnl"].min(),
    })
    total = pd.DataFrame({
        "trades": [len(df)], "win_rate": [(df["pnl"] > 0).mean()], "total_pnl": [df["pnl"].sum()],
        "avg_pct": [df["pnl_pct"].mean()], "avg_R": [df["r_multiple"].mean()],
        "best": [df["pnl"].max()], "worst": [df["pnl"].min()],
    }, index=["ALL"])
    return pd.concat([out, total])


def format_stats(table):
    return table.to_string(formatters={
        "win_rate": "{:.0%}".format, "total_pnl": "${:,.2f}".format, "avg_pct": "{:+.2%}".format,
        "avg_R": "{:+.2f}".format, "best": "${:,.2f}".format, "worst": "${:,.2f}".format,
    })


# ---------------------------------------------------------------- broker


def get_clients():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient

    load_dotenv(PROJECT_DIR / ".env")
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY. Copy .env.example to .env and fill them in.")
    # paper=True always routes orders to the paper trading endpoint.
    return TradingClient(api_key, secret_key, paper=True), StockHistoricalDataClient(api_key, secret_key)


def latest_price(data_client, symbol, fallback):
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockLatestTradeRequest

    try:
        trade = data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX))
        return float(trade[symbol].price)
    except Exception as exc:  # free data feed hiccups shouldn't stop the day
        log.warning("Latest price for %s unavailable (%s); using last close %.2f", symbol, exc, fallback)
        return fallback


def status_of(order):
    return getattr(order.status, "value", str(order.status))


def get_order(client, order_id):
    from alpaca.trading.requests import GetOrderByIdRequest
    return client.get_order_by_id(order_id, filter=GetOrderByIdRequest(nested=True))


FINAL = ("filled", "canceled", "expired", "rejected", "replaced")


def submit_bracket(client, symbol, qty, stop_price, target_price):
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

    # GTC so the stop and target stay live overnight while the trade is held.
    return client.submit_order(MarketOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=target_price),
        stop_loss=StopLossRequest(stop_price=stop_price),
    ))


def submit_oco(client, symbol, qty, stop_price, target_price):
    """Stop + target for shares already held (one cancels the other)."""
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest

    return client.submit_order(LimitOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.GTC,
        order_class=OrderClass.OCO, limit_price=target_price,
        take_profit=TakeProfitRequest(limit_price=target_price),
        stop_loss=StopLossRequest(stop_price=stop_price),
    ))


def submit_sell(client, symbol, qty):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    return client.submit_order(MarketOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY))


def wait_filled(client, order_id, seconds=60):
    order = get_order(client, order_id)
    deadline = clock.time() + seconds
    while status_of(order) != "filled" and clock.time() < deadline:
        clock.sleep(2)
        order = get_order(client, order_id)
    return order


def sessions_held(client, trade):
    """Trading sessions from the entry day through today, both included."""
    from alpaca.trading.requests import GetCalendarRequest

    start = date.fromisoformat(trade["date"])
    return len(client.get_calendar(GetCalendarRequest(start=start, end=datetime.now(ET).date())))


def record_result(trade, entry_price, exit_price, reason):
    qty = trade["qty"]
    risk_per_share = trade["ref_price"] - trade["stop_price"]
    trade.update({
        "status": "closed",
        "closed_on": datetime.now(ET).date().isoformat(),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_reason": reason,
        "pnl": round((exit_price - entry_price) * qty, 2),
        "pnl_pct": exit_price / entry_price - 1,
        "r_multiple": (exit_price - entry_price) / risk_per_share if risk_per_share > 0 else 0.0,
    })
    log.info("%s %s closed: %s -> %s (%s), P&L %s (%+.2f%%)", trade["date"], trade["symbol"],
             money(entry_price), money(exit_price), reason, money(trade["pnl"]), trade["pnl_pct"] * 100)


def level_orders(client, trade, entry):
    """The orders that hold the stop and target: the bracket's legs, or the OCO
    that replaced them."""
    if trade.get("oco_order_id"):
        oco = get_order(client, trade["oco_order_id"])
        return [oco] + list(oco.legs or [])
    return list(entry.legs or [])


def check(client, trade):
    """Update an open trade from the broker. Returns the entry order while the
    position is still open, or None once the trade is finished."""
    if trade["status"] == "closing":
        exit_order = wait_filled(client, trade["exit_order_id"])
        if status_of(exit_order) == "filled":
            entry = get_order(client, trade["entry_order_id"])
            record_result(trade, float(entry.filled_avg_price), float(exit_order.filled_avg_price),
                          trade["exit_reason"])
        return None

    entry = get_order(client, trade["entry_order_id"])
    filled_qty = float(entry.filled_qty or 0)
    if filled_qty == 0:
        if status_of(entry) not in FINAL:
            client.cancel_order_by_id(entry.id)
        trade["status"] = "not_filled"
        log.info("%s %s entry never filled", trade["date"], trade["symbol"])
        return None
    trade["qty"] = filled_qty
    trade["entry_price"] = float(entry.filled_avg_price)

    for order in level_orders(client, trade, entry):
        if status_of(order) == "filled":
            reason = "stop_loss" if order.stop_price is not None else "take_profit"
            record_result(trade, trade["entry_price"], float(order.filled_avg_price), reason)
            return None
    return entry


def cancel_levels(client, trade, entry):
    for order in level_orders(client, trade, entry):
        if status_of(order) not in FINAL:
            try:
                client.cancel_order_by_id(order.id)
            except Exception as exc:
                log.warning("Could not cancel order %s: %s", order.id, exc)
    clock.sleep(2)


def protect(client, trade, entry):
    """Make sure a held position has a live GTC stop and target overnight.
    (Orders placed as DAY, like the first trade, expire at the close.)"""
    live = [o for o in level_orders(client, trade, entry) if status_of(o) not in FINAL]
    if live and all(getattr(o.time_in_force, "value", o.time_in_force) == "gtc" for o in live):
        return
    cancel_levels(client, trade, entry)
    try:
        oco = submit_oco(client, trade["symbol"], trade["qty"], trade["stop_price"], trade["target_price"])
    except Exception as exc:
        # Never leave a held position without a stop: fall back to a plain GTC stop.
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        log.warning("OCO rejected for %s (%s); placing a stop-loss only", trade["symbol"], exc)
        oco = client.submit_order(StopOrderRequest(
            symbol=trade["symbol"], qty=trade["qty"], side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC, stop_price=trade["stop_price"]))
    trade["oco_order_id"] = str(oco.id)
    log.info("%s: placed GTC stop %.2f / target %.2f to hold overnight (id=%s)",
             trade["symbol"], trade["stop_price"], trade["target_price"], oco.id)


def close_at_market(client, trade, entry, reason):
    """Cancel the stop and target (they reserve the shares) and sell at market."""
    cancel_levels(client, trade, entry)
    exit_order = submit_sell(client, trade["symbol"], trade["qty"])
    trade.update({"status": "closing", "exit_order_id": str(exit_order.id), "exit_reason": reason})
    exit_order = wait_filled(client, exit_order.id)
    if status_of(exit_order) == "filled":
        record_result(trade, trade["entry_price"], float(exit_order.filled_avg_price), reason)
    else:
        log.warning("Exit order for %s not filled yet (%s); will settle on the next run",
                    trade["symbol"], status_of(exit_order))


# ---------------------------------------------------------------- phases


def enter(args):
    today = datetime.now(ET).date().isoformat()
    journal = load_journal()
    client = data_client = None
    if not args.dry_run:
        client, data_client = get_clients()
        # Trades from earlier days: record overnight stop/target fills, sell any that
        # should have been sold at an earlier close (missed run), protect the rest.
        for trade in journal:
            if trade["status"] not in ("open", "closing") or trade["date"] == today:
                continue
            entry = check(client, trade)
            if entry is not None:
                if sessions_held(client, trade) - 1 >= cfg.MAX_HOLD_DAYS:
                    log.info("Closing overdue trade from %s (%s)", trade["date"], trade["symbol"])
                    close_at_market(client, trade, entry, "missed_exit_closed_next_open")
                else:
                    protect(client, trade, entry)
            write_report(trade)
        save_journal(journal)
        if any(t["date"] == today for t in journal):
            log.info("Already traded today; nothing to do.")
            return

    # Skip stocks already held (e.g. by bot.py) so the two bots never share a position.
    held = {p.symbol for p in client.get_all_positions()} if client else set()
    universe = [s for s in cfg.UNIVERSE if s not in held]
    strategy = next_strategy(journal)
    log.info("=== Entry %s: strategy %s (%s) ===", today, strategy, STRATEGIES[strategy]["title"])

    pick = choose(strategy, fetch_bars(universe))
    symbol, m = pick["symbol"], pick["metrics"]
    ref = latest_price(data_client, symbol, m["close"]) if data_client else m["close"]
    qty = math.floor(cfg.POSITION_DOLLARS / ref)
    if qty < 1:
        raise RuntimeError(f"{symbol} at {ref:.2f} costs more than POSITION_DOLLARS={cfg.POSITION_DOLLARS}")

    trade = {
        "date": today, **pick, "qty": qty, "ref_price": round(ref, 2),
        "stop_price": round(ref - pick["stop_atr"] * m["atr"], 2),
        "target_price": round(ref + pick["target_atr"] * m["atr"], 2),
        "status": "planned", "entry_price": None, "exit_price": None, "exit_reason": None,
        "pnl": None, "pnl_pct": None, "r_multiple": None, "dry_run": args.dry_run,
    }
    log.info("Pick: %s (%s setup) %d sh @ ~%.2f, stop %.2f, target %.2f", symbol, pick["setup"],
             qty, ref, trade["stop_price"], trade["target_price"])

    if args.dry_run:
        path = write_report(trade, REPORTS_DIR / "dry-run")
        log.info("Dry run: no order placed. Report: %s", path.relative_to(PROJECT_DIR))
        return

    order = submit_bracket(client, symbol, qty, trade["stop_price"], trade["target_price"])
    trade.update({"status": "open", "entry_order_id": str(order.id)})
    log.info("Submitted bracket buy %s: id=%s status=%s", symbol, order.id, status_of(order))
    journal.append(trade)
    save_journal(journal)
    log.info("Report: %s", write_report(trade).relative_to(PROJECT_DIR))


def exit_(args):
    """Before the close: sell trades that reached MAX_HOLD_DAYS, keep the rest
    with a live stop and target overnight."""
    today = datetime.now(ET).date().isoformat()
    journal = load_journal()
    trades = [t for t in journal if t["status"] in ("open", "closing")]
    if not trades:
        log.info("No open trades; nothing to do.")
        return
    if args.dry_run:
        for trade in trades:
            log.info("Dry run: open %s from %s", trade["symbol"], trade["date"])
        return
    client, _ = get_clients()
    log.info("=== Close check %s: %s ===", today, ", ".join(t["symbol"] for t in trades))
    for trade in trades:
        entry = check(client, trade)
        if entry is not None:
            held = sessions_held(client, trade)
            if held >= cfg.MAX_HOLD_DAYS:
                close_at_market(client, trade, entry, "end_of_day" if cfg.MAX_HOLD_DAYS == 1 else "time_exit")
            else:
                protect(client, trade, entry)
                log.info("Holding %s overnight (day %d of %d)", trade["symbol"], held, cfg.MAX_HOLD_DAYS)
        log.info("Report: %s", write_report(trade).relative_to(PROJECT_DIR))
    save_journal(journal)
    summary(args)


def scheduled(args):
    from alpaca.trading.requests import GetCalendarRequest

    now = datetime.now(ET)
    in_entry = ENTRY_WINDOW[0] <= now.time() <= ENTRY_WINDOW[1]
    in_exit = EXIT_WINDOW[0] <= now.time() <= EXIT_WINDOW[1]
    if not (in_entry or in_exit):
        return  # the other launchd slot of the day
    client, _ = get_clients()
    if not client.get_calendar(GetCalendarRequest(start=now.date(), end=now.date())):
        log.info("Market closed today (holiday). Nothing to do.")
        return
    if in_entry:
        enter(args)
    else:
        exit_(args)


def summary(args):
    closed = [t for t in load_journal() if t["status"] == "closed"]
    if not closed:
        log.info("No closed trades yet.")
        return
    df = pd.DataFrame(closed)
    table = stats_table(df)
    log.info("Results so far:\n%s", format_stats(table))
    recent = df.tail(10)[["date", "strategy", "symbol", "exit_reason", "pnl", "pnl_pct"]]
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "SUMMARY.md").write_text(
        f"# Daily bot results (updated {date.today().isoformat()})\n\n"
        "## Per strategy\n\n```\n" + format_stats(table) + "\n```\n\n"
        "## Last 10 trades\n\n```\n" + recent.to_string(index=False) + "\n```\n")


def simulate(bars, day, stop, target, hold):
    """Walk a trade bought at `day`'s open forward up to `hold` sessions.
    Within a day the stop is assumed to hit first if both are touched; an
    overnight gap past a level fills at that day's open."""
    window = bars.loc[day:].iloc[:hold]
    for k, (when, bar) in enumerate(window.iterrows()):
        if k > 0 and bar["Open"] <= stop:
            return bar["Open"], "stop_loss_gap", k + 1
        if k > 0 and bar["Open"] >= target:
            return bar["Open"], "take_profit_gap", k + 1
        if bar["Low"] <= stop:
            return stop, "stop_loss", k + 1
        if bar["High"] >= target:
            return target, "take_profit", k + 1
    return window["Close"].iloc[-1], "end_of_day" if hold == 1 else "time_exit", len(window)


def backtest(args):
    """Replay the last N sessions on daily bars: buy at the open, exit at the stop,
    the target, or the close of the last day allowed by the holding period."""
    bars = fetch_bars(cfg.UNIVERSE, completed_only=True)
    dates = sorted(set().union(*(b.index for b in bars.values())))[-args.days:]
    rows = []
    for i, day in enumerate(dates):
        history = {s: b[b.index < day].tail(260) for s, b in bars.items() if day in b.index}
        for name in STRATEGIES:
            pick = choose(name, history)
            symbol_bars = bars[pick["symbol"]]
            entry, atr_ = symbol_bars.loc[day, "Open"], pick["metrics"]["atr"]
            stop, target = entry - pick["stop_atr"] * atr_, entry + pick["target_atr"] * atr_
            qty = math.floor(cfg.POSITION_DOLLARS / entry)
            for hold in args.hold:
                exit_price, reason, held = simulate(symbol_bars, day, stop, target, hold)
                rows.append({
                    "hold": hold, "date": day.date().isoformat(), "strategy": name, "symbol": pick["symbol"],
                    "setup": "strong" if pick["setup"] == "strong" else "weak",
                    "in_rotation": name == cfg.STRATEGY_ROTATION[i % len(cfg.STRATEGY_ROTATION)],
                    "entry": round(entry, 2), "exit": round(exit_price, 2), "exit_reason": reason,
                    "days_held": held, "pnl": round((exit_price - entry) * qty, 2),
                    "pnl_pct": exit_price / entry - 1, "r_multiple": (exit_price - entry) / (entry - stop),
                })
    df = pd.DataFrame(rows)
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"backtest_{dates[0].date()}_{dates[-1].date()}.csv"
    df.to_csv(out, index=False)

    print(f"\nBacktest {dates[0].date()} to {dates[-1].date()} ({len(dates)} sessions), "
          f"${cfg.POSITION_DOLLARS:,} per trade, no commissions or slippage\n")
    if len(args.hold) > 1:
        rot = df[df["in_rotation"]]
        compare = pd.DataFrame({
            "trades": rot.groupby("hold").size(),
            "win_rate": rot.groupby("hold")["pnl"].apply(lambda s: (s > 0).mean()),
            "total_pnl": rot.groupby("hold")["pnl"].sum(),
            "avg_R": rot.groupby("hold")["r_multiple"].mean(),
            "worst": rot.groupby("hold")["pnl"].min(),
            "avg_days": rot.groupby("hold")["days_held"].mean(),
        })
        print(f"The rotation, by max holding days:\n" + format_stats(compare) + "\n")
        print("Total P&L per strategy traded every day, by max holding days:\n"
              + df.pivot_table(index="strategy", columns="hold", values="pnl", aggfunc="sum")
                  .to_string(float_format="${:,.0f}".format) + "\n")
    for hold in args.hold if len(args.hold) == 1 else []:
        d = df[df["hold"] == hold]
        print("Each strategy traded every day:\n" + format_stats(stats_table(d)) + "\n")
        print("Strong setups only:\n" + format_stats(stats_table(d[d["setup"] == "strong"])) + "\n")
        print(f"The rotation {cfg.STRATEGY_ROTATION} (one trade a day):\n"
              + format_stats(stats_table(d[d["in_rotation"]])) + "\n")
    print(f"Every trade: {out.relative_to(PROJECT_DIR)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["enter", "exit", "scheduled", "summary", "backtest"])
    parser.add_argument("--dry-run", action="store_true", help="pick and report, but never place orders")
    parser.add_argument("--days", type=int, default=120, help="sessions to replay in backtest")
    parser.add_argument("--hold", type=int, nargs="+", default=[cfg.MAX_HOLD_DAYS],
                        help="max holding days to test in backtest, e.g. --hold 1 3 5")
    args = parser.parse_args()

    setup_logging()
    commands = {"enter": enter, "exit": exit_, "scheduled": scheduled, "summary": summary, "backtest": backtest}
    try:
        commands[args.command](args)
    except Exception:
        log.exception("Run failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
