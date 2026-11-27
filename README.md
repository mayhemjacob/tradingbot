# tradingbot

A personal experiment in automated trading, built with Python and Claude Code.
It trades US stocks on an **Alpaca paper (simulated) account**: no real money is involved.

> **Disclaimer.** This is a personal learning project, not financial advice and not a
> recommendation to buy or sell anything. The results in `reports/` come from a simulated
> account and from backtests, and past results say nothing about future ones. If you run
> this code, you do so at your own risk.

## What's inside

### Daily bot: one stock a day, one strategy a day (`daily_bot.py`)

Each trading day the bot takes the next strategy in a rotation of five
(momentum, mean reversion, breakout, pullback, squeeze), ranks a fixed universe of large
US stocks with it and buys one shortly after the open. Every trade goes in with a bracket
order (stop-loss and take-profit sized from the ATR), and anything that hits neither is
sold before the close of its third trading day.

Each trade gets a written report in [`reports/`](reports/), a line in the journal
([`reports/trades.csv`](reports/trades.csv)) and a refresh of the one-page dashboard
(`reports/dashboard.html`). Results per strategy so far: [`reports/SUMMARY.md`](reports/SUMMARY.md).

```bash
python daily_bot.py enter --dry-run    # pick today's stock and write the report, no orders
python daily_bot.py enter              # pick today's stock and buy it
python daily_bot.py exit               # sell trades that are due
python daily_bot.py summary            # results per strategy
python daily_bot.py backtest --days 120
```

Strategies live in [`strategies.py`](strategies.py) and settings in
[`daily_config.py`](daily_config.py).

### Moving average crossover bot (`bot.py`)

The first, simpler bot: a 20/50-day moving average crossover on a single stock.

### Gold backtest (`backtest_gold.py`)

A backtest of a time-of-day gold (XAUUSD) strategy on one-minute data, with spread,
gaps and compounding risk per trade. The data isn't in the repo; see the file's header.

### Claude Code skills (`.claude/skills/`)

A set of skills that turn Claude Code into a small trading desk. Every order still needs
an explicit yes from a human before it reaches the broker.

| Skill | What it does |
| --- | --- |
| `trader-screener` | Checks companies against your own rules (growth, margins, P/E…) |
| `trader-analyst` | Reads reported numbers straight from SEC EDGAR filings |
| `trader-news` | Watches headlines and 8-K filings for the stocks you hold |
| `trader-copycat` | Rebuilds a famous investor's portfolio from their public 13F filings |
| `trader-pelosi` | Tracks stock trades members of the US Congress must disclose |
| `trader-budget` | Sizes positions to a budget, with a cash buffer and per-stock caps |
| `trader-risk` | Blocks any order that breaks your risk limits |
| `trader-rebalance` | Brings a portfolio back to its target weights |
| `trader-tax` | Tracks cost-basis lots and unrealized gains and losses |
| `trader-alerts` | Price alerts and the NYSE opening bell |
| `trader-broker` | Places approved orders, one at a time |

## Setup

Requires Python 3 and a free [Alpaca](https://alpaca.markets) paper trading account.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then add your Alpaca PAPER keys
python daily_bot.py enter --dry-run
```

The two `.plist` files run the bots on a schedule with macOS `launchd`. Change the paths
inside them to your own before loading them.

Keys live only in `.env`, which is git-ignored. Never commit it.

## Author

[Jacob Miralles](https://jacobomiralles.com)
