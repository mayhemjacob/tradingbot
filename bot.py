"""Moving average crossover bot for an Alpaca paper trading account.

    python bot.py              # checks for a crossover and places an order if found
    python bot.py --dry-run    # same check, but never places an order
    python bot.py --scheduled  # used by launchd: only runs on trading days at market open

Everything is logged to logs/bot.log.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import GetCalendarRequest, MarketOrderRequest
from dotenv import load_dotenv

import config

PROJECT_DIR = Path(__file__).resolve().parent
LOG_FILE = PROJECT_DIR / "logs" / "bot.log"
ET = ZoneInfo("America/New_York")

# launchd fires at two local times to cover daylight-saving mismatches;
# a scheduled run only proceeds inside this New York time window.
SCHEDULED_WINDOW = (time(9, 25), time(10, 0))

log = logging.getLogger("tradingbot")


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


def fetch_prices(symbol, days):
    """Return the last `days` completed trading days of daily data for `symbol`."""
    # Ask for more calendar time than needed, then keep the last `days` rows.
    data = yf.Ticker(symbol).history(period="1y", interval="1d")
    if data.empty:
        raise RuntimeError(f"No price data returned for {symbol}")

    # Today's bar is still forming until the 4pm ET close; signals use closed days only.
    now = datetime.now(ET)
    if data.index[-1].date() == now.date() and now.time() < time(16, 0):
        data = data.iloc[:-1]
    return data.tail(days)


def add_moving_averages(data, short_window, long_window):
    data = data.copy()
    data["SMA_short"] = data["Close"].rolling(window=short_window).mean()
    data["SMA_long"] = data["Close"].rolling(window=long_window).mean()
    return data.dropna(subset=["SMA_short", "SMA_long"])


def detect_crossover(data):
    """Compare the last two days. Returns "buy", "sell" or None.

    buy:  short MA was at/below long MA yesterday and is above it today
    sell: short MA was at/above long MA yesterday and is below it today
    """
    if len(data) < 2:
        raise RuntimeError("Not enough data to compare two consecutive days")

    prev, curr = data.iloc[-2], data.iloc[-1]
    if prev["SMA_short"] <= prev["SMA_long"] and curr["SMA_short"] > curr["SMA_long"]:
        return "buy"
    if prev["SMA_short"] >= prev["SMA_long"] and curr["SMA_short"] < curr["SMA_long"]:
        return "sell"
    return None


def get_trading_client():
    load_dotenv(PROJECT_DIR / ".env")
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY. Copy .env.example to .env and fill them in.")
    # paper=True always routes orders to the paper trading endpoint.
    return TradingClient(api_key, secret_key, paper=True)


def is_trading_day(client, day):
    calendar = client.get_calendar(GetCalendarRequest(start=day, end=day))
    return len(calendar) > 0


def held_qty(client, symbol):
    try:
        return float(client.get_open_position(symbol).qty)
    except APIError:
        return 0.0  # no open position


def place_order(client, symbol, qty, side):
    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    return client.submit_order(order)


def run(args):
    now = datetime.now(ET)
    if args.scheduled and not (SCHEDULED_WINDOW[0] <= now.time() <= SCHEDULED_WINDOW[1]):
        # The other launchd slot of the day; nothing to do.
        return

    log.info("=== Run started (%s, New York time %s) ===",
             "scheduled" if args.scheduled else "dry run" if args.dry_run else "manual",
             now.strftime("%Y-%m-%d %H:%M"))

    client = get_trading_client()
    if args.scheduled and not is_trading_day(client, now.date()):
        log.info("Market closed today (holiday). Nothing to do.")
        return

    data = fetch_prices(config.SYMBOL, config.LOOKBACK_DAYS)
    data = add_moving_averages(data, config.SHORT_WINDOW, config.LONG_WINDOW)
    signal = detect_crossover(data)

    prev, curr = data.iloc[-2], data.iloc[-1]
    log.info("%s  %dd vs %dd SMA", config.SYMBOL, config.SHORT_WINDOW, config.LONG_WINDOW)
    for label, row in (("prev", prev), ("curr", curr)):
        log.info("  %s %s  close=%.2f  short=%.2f  long=%.2f",
                 label, row.name.date(), row["Close"], row["SMA_short"], row["SMA_long"])
    log.info("Signal: %s", signal or "none")

    shares = held_qty(client, config.SYMBOL)
    log.info("Currently holding %g share(s) of %s", shares, config.SYMBOL)

    if signal is None:
        return
    if args.dry_run:
        log.info("Dry run: would %s %d %s", signal, config.TRADE_QTY, config.SYMBOL)
        return
    if signal == "sell" and shares < config.TRADE_QTY:
        # Avoid opening a short position when we don't own enough shares.
        log.info("Skipping sell: not holding %d share(s) of %s", config.TRADE_QTY, config.SYMBOL)
        return

    order = place_order(client, config.SYMBOL, config.TRADE_QTY, signal)
    log.info("Submitted %s order for %d %s: id=%s status=%s",
             signal, config.TRADE_QTY, config.SYMBOL, order.id, order.status)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="check for a signal but don't place orders")
    parser.add_argument("--scheduled", action="store_true", help="only run on trading days at 9:30 ET")
    args = parser.parse_args()

    setup_logging()
    try:
        run(args)
    except Exception:
        log.exception("Run failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
