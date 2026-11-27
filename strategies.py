"""The three daily strategies. Each one looks at every stock in the universe
using only completed daily bars, ranks them, and picks one to buy at the open.

All strategies are long-only: buy at the open, sell by the close the same day
(or earlier if the stop-loss or take-profit is hit).

    pick = choose("momentum", bars_by_symbol)
"""

import pandas as pd

import daily_config as cfg

STRATEGIES = {
    "momentum": {
        "title": "Trend Momentum",
        "idea": "Buy the stock that has risen the most over the last 20 days while "
                "trading above its 50-day average. Bet: strong stocks keep going.",
        "filter": "close above the 50-day SMA",
        "rank": "highest 20-day return",
        "stop_atr": 1.0,
        "target_atr": 2.0,
    },
    "mean_reversion": {
        "title": "Oversold Bounce",
        "idea": "Buy the most oversold stock (lowest RSI) that is still in a long-term "
                "uptrend. Bet: a sharp dip in a healthy stock tends to snap back.",
        "filter": "close above the 200-day SMA and RSI(14) below 40",
        "rank": "lowest RSI(14)",
        "stop_atr": 1.0,
        "target_atr": 1.0,
    },
    "breakout": {
        "title": "20-Day Breakout",
        "idea": "Buy the stock that just closed at or near its 20-day high on above-average "
                "volume. Bet: a breakout with volume behind it attracts more buyers.",
        "filter": "close within 1% of the 20-day high and volume above 1.2x its 20-day average",
        "rank": "highest volume ratio",
        "stop_atr": 1.0,
        "target_atr": 2.0,
    },
    "pullback": {
        "title": "Trend Pullback",
        "idea": "Buy a stock in a clean uptrend that has dipped for a few days below its "
                "10-day average. Bet: in a strong trend, short pullbacks get bought.",
        "filter": "close above the 50-day SMA, 50-day above the 200-day, and close below the 10-day SMA",
        "rank": "biggest 3-day drop",
        "stop_atr": 1.0,
        "target_atr": 1.5,
    },
    "squeeze": {
        "title": "Quiet Day Squeeze (NR7)",
        "idea": "Buy an uptrending stock whose last day was its narrowest range of the past 7 days. "
                "Bet: quiet, coiled days are often followed by a bigger move, and the trend picks the side.",
        "filter": "close above the 50-day SMA and the last day's range is the smallest of the last 7",
        "rank": "smallest last-day range compared to ATR",
        "stop_atr": 1.0,
        "target_atr": 2.0,
    },
}


def rsi(close, window=14):
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def atr(bars, window):
    prev_close = bars["Close"].shift()
    true_range = pd.concat([
        bars["High"] - bars["Low"],
        (bars["High"] - prev_close).abs(),
        (bars["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / window, adjust=False).mean()


def indicators(bars):
    """Metrics for one stock as of its last bar."""
    close, volume = bars["Close"], bars["Volume"]
    day_range = bars["High"] - bars["Low"]
    last = close.iloc[-1]
    atr_ = atr(bars, cfg.ATR_WINDOW).iloc[-1]
    return {
        "close": last,
        "ret_3d": last / close.iloc[-4] - 1,
        "ret_20d": last / close.iloc[-21] - 1,
        "sma_10": close.rolling(10).mean().iloc[-1],
        "sma_50": close.rolling(50).mean().iloc[-1],
        "sma_200": close.rolling(200).mean().iloc[-1],
        "rsi_14": rsi(close).iloc[-1],
        "high_20d": bars["High"].iloc[-20:].max(),
        "vol_ratio": volume.iloc[-1] / volume.iloc[-21:-1].mean(),
        "range_ratio": day_range.iloc[-1] / atr_,
        "nr7": bool(day_range.iloc[-1] <= day_range.iloc[-7:].min()),
        "atr": atr_,
    }


def _passes(name, m):
    if name == "momentum":
        return m["close"] > m["sma_50"]
    if name == "mean_reversion":
        return m["close"] > m["sma_200"] and m["rsi_14"] < 40
    if name == "breakout":
        return m["close"] >= 0.99 * m["high_20d"] and m["vol_ratio"] > 1.2
    if name == "pullback":
        return m["close"] > m["sma_50"] > m["sma_200"] and m["close"] < m["sma_10"]
    if name == "squeeze":
        return m["close"] > m["sma_50"] and m["nr7"]
    raise ValueError(f"Unknown strategy {name}")


def _score(name, m):
    """Higher is better."""
    if name == "momentum":
        return m["ret_20d"]
    if name == "mean_reversion":
        return -m["rsi_14"]
    if name == "breakout":
        return m["vol_ratio"]
    if name == "pullback":
        return -m["ret_3d"]
    if name == "squeeze":
        return -m["range_ratio"]
    raise ValueError(f"Unknown strategy {name}")


def choose(name, bars_by_symbol):
    """Pick today's stock for strategy `name`.

    `bars_by_symbol` maps symbol -> daily OHLCV DataFrame ending at the last
    completed session. If no stock passes the filter, the best-ranked stock is
    still picked (one trade a day) and the pick is flagged as a weak setup.
    """
    rows = []
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < 201:
            continue  # not enough history for the 200-day SMA
        if bars["Close"].iloc[-1] > cfg.POSITION_DOLLARS:
            continue  # one share costs more than the trade budget
        m = indicators(bars)
        m["symbol"] = symbol
        m["passes"] = bool(_passes(name, m))
        m["score"] = _score(name, m)
        rows.append(m)
    if not rows:
        raise RuntimeError("No stock has enough history to evaluate")

    table = pd.DataFrame(rows).sort_values(["passes", "score"], ascending=False)
    best = table.iloc[0]
    spec = STRATEGIES[name]
    return {
        "strategy": name,
        "symbol": best["symbol"],
        "setup": "strong" if best["passes"] else "weak (nothing passed the filter, picked the closest)",
        "metrics": {k: float(best[k]) for k in ("close", "ret_3d", "ret_20d", "sma_10", "sma_50", "sma_200",
                                                  "rsi_14", "high_20d", "vol_ratio", "range_ratio", "atr")},
        "stop_atr": spec["stop_atr"],
        "target_atr": spec["target_atr"],
        "candidates": table.head(5)[["symbol", "passes", "close", "ret_3d", "ret_20d", "rsi_14",
                                     "vol_ratio", "range_ratio"]].to_dict("records"),
    }
