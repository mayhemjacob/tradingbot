"""Backtest of the 02:35 New York gold buy strategy on Dukascopy XAUUSD M1 data.

    python backtest_gold.py                 # all variants, default 0.30 $ spread
    python backtest_gold.py --spread 0.50   # different round-trip cost
    python backtest_gold.py --detail        # also print yearly/monthly tables per variant

Data: data/xauusd_m1_*.csv from dukascopy-node (bid prices, UTC timestamps in ms).

Rules simulated:
- Entry at the open of the 02:35 New York bar (close of the 02:34 signal bar), buy only.
- Entry price = bid open + spread (we buy at the ask); exits are on bid prices.
- SL 6.80 $ below entry, TP 5.10 $ above. No time exit: the trade is held until one is hit.
- If SL and TP are both inside one bar, the trade counts as a loss.
- If a bar opens beyond the SL or TP (gap), it fills at that bar's open.
- One trade at a time: a day is skipped if the previous trade is still open.
- R = P&L / SL distance. Equity risks 1 % per trade, compounded.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
ET = ZoneInfo("America/New_York")

SL_USD = 6.80
TP_USD = 5.10
RISK_PCT = 0.01
# Gold price used to turn the fixed-dollar SL/TP into percentages (early 2024 level,
# when 6.80 $ was about 0.33 % of the price).
PCT_REFERENCE_PRICE = 2050.0


@dataclass
class Variant:
    name: str
    clock: str          # "ny" -> 02:35 New York, "utc" -> 06:35 UTC
    stops: str          # "usd" or "pct"
    weekdays: tuple     # Monday = 0


def load_data():
    files = sorted(DATA_DIR.glob("xauusd_m1_*.csv"))
    if not files:
        raise SystemExit("No data in data/. Download it with dukascopy-node first.")
    df = pd.concat(pd.read_csv(f) for f in files)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return df


def entry_indices(df, variant):
    """Bar index of each trade's entry bar for the variant's clock and weekdays."""
    if variant.clock == "ny":
        local = df["time"].dt.tz_convert(ET)
    else:
        local = df["time"]
    hit = (local.dt.hour == (2 if variant.clock == "ny" else 6)) & (local.dt.minute == 35)
    hit &= local.dt.weekday.isin(variant.weekdays)
    # Dukascopy fills closed-market periods (holidays) with flat bars; skip those days.
    moving = (df["high"] != df["low"]).rolling(10).sum().shift(-9).fillna(0) > 0
    return np.flatnonzero((hit & moving).to_numpy())


def simulate(df, variant, spread):
    o, h, l = (df[c].to_numpy() for c in ("open", "high", "low"))
    times = df["time"]
    trades = []
    busy_until = -1
    skipped = 0
    for i in entry_indices(df, variant):
        if i <= busy_until:
            skipped += 1
            continue
        entry = o[i] + spread
        if variant.stops == "usd":
            sl_dist, tp_dist = SL_USD, TP_USD
        else:
            sl_dist = entry * SL_USD / PCT_REFERENCE_PRICE
            tp_dist = entry * TP_USD / PCT_REFERENCE_PRICE
        sl, tp = entry - sl_dist, entry + tp_dist

        exit_price = reason = None
        for j in range(i, len(o)):
            if j > i and o[j] <= sl:
                exit_price, reason = o[j], "SL gap"
            elif j > i and o[j] >= tp:
                exit_price, reason = o[j], "TP gap"
            elif l[j] <= sl:
                exit_price, reason = sl, "SL"   # also covers SL+TP in one bar
            elif h[j] >= tp:
                exit_price, reason = tp, "TP"
            if reason:
                break
        if reason is None:
            break  # still open at the end of the data
        busy_until = j
        pnl = exit_price - entry
        trades.append({
            "date": times[i].tz_convert(ET).date(),
            "entry_time": times[i],
            "exit_time": times[j],
            "entry": entry,
            "exit": exit_price,
            "reason": reason,
            "same_bar_both": l[j] <= sl and h[j] >= tp,
            "pnl": pnl,
            "r": pnl / sl_dist,
        })
    t = pd.DataFrame(trades)
    t["date"] = pd.to_datetime(t["date"])
    return t, skipped


def stats(t):
    r = t["r"]
    wins, losses = r[r > 0], r[r <= 0]
    equity = np.cumprod(1 + RISK_PCT * r.to_numpy())
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    max_dd = ((peak - equity) / peak).max()
    streak = longest = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        longest = max(longest, streak)
    hold = (t["exit_time"] - t["entry_time"]).dt.total_seconds() / 60
    return {
        "trades": len(t),
        "win%": 100 * len(wins) / len(t),
        "PF": wins.sum() / -losses.sum() if len(losses) else np.inf,
        "exp_R": r.mean(),
        "total_R": r.sum(),
        "return%": 100 * (equity[-1] - 1),
        "maxDD%": 100 * max_dd,
        "max_loss_streak": longest,
        "same_bar%": 100 * t["same_bar_both"].mean(),
        "median_hold_min": hold.median(),
    }


def period_table(t, freq):
    g = t.groupby(t["date"].dt.to_period(freq))
    return pd.DataFrame({
        "trades": g.size(),
        "win%": g["r"].apply(lambda r: 100 * (r > 0).mean()),
        "total_R": g["r"].sum(),
        "exp_R": g["r"].mean(),
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spread", type=float, default=0.30, help="round-trip cost in $ (default 0.30)")
    parser.add_argument("--detail", action="store_true", help="print yearly and monthly tables")
    args = parser.parse_args()

    df = load_data()
    print(f"Data: {df['time'].iloc[0]} -> {df['time'].iloc[-1]}  ({len(df):,} M1 bars)")
    print(f"Spread/cost: {args.spread:.2f} $   SL {SL_USD} $   TP {TP_USD} $   "
          f"(% variant: SL {100*SL_USD/PCT_REFERENCE_PRICE:.3f} %, TP {100*TP_USD/PCT_REFERENCE_PRICE:.3f} %)")
    print(f"Breakeven win rate at 1:0.75 = {100/1.75:.1f} %\n")

    mon_thu, mon_fri = (0, 1, 2, 3), (0, 1, 2, 3, 4)
    variants = [
        Variant("BASE  NY 02:35, $ stops, Mon-Thu", "ny", "usd", mon_thu),
        Variant("UTC 06:35, $ stops, Mon-Thu", "utc", "usd", mon_thu),
        Variant("NY 02:35, % stops, Mon-Thu", "ny", "pct", mon_thu),
        Variant("NY 02:35, $ stops, Mon-Fri", "ny", "usd", mon_fri),
        Variant("NY 02:35, % stops, Mon-Fri", "ny", "pct", mon_fri),
    ]
    rows, results = {}, {}
    for v in variants:
        t, skipped = simulate(df, v, args.spread)
        s = stats(t)
        s["skipped_busy"] = skipped
        rows[v.name] = s
        results[v.name] = t
    summary = pd.DataFrame(rows).T
    with pd.option_context("display.width", 200, "display.float_format", "{:.2f}".format):
        print(summary.to_string())

        for name, t in results.items():
            if not args.detail and not name.startswith("BASE"):
                continue
            print(f"\n=== {name}: by year ===")
            print(period_table(t, "Y").to_string())
            print(f"\n=== {name}: by month ===")
            print(period_table(t, "M").to_string())

    out = PROJECT_DIR / "data" / "backtest_base_trades.csv"
    results[variants[0].name].to_csv(out, index=False)
    print(f"\nBase trades saved to {out.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
