"""Settings for the one-stock-a-day bot (daily_bot.py)."""

# Stocks the bot is allowed to pick from. Liquid large caps keep fills clean.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "JPM",
    "V", "MA", "UNH", "XOM", "LLY", "COST", "NFLX", "CRM", "ORCL", "WMT",
]

# Strategies take turns, one per trading day, in this order.
# Put a single name here (e.g. ["momentum"]) to run just one strategy.
STRATEGY_ROTATION = ["momentum", "mean_reversion", "breakout", "pullback", "squeeze"]

# Dollars put into each day's trade (rounded down to whole shares).
POSITION_DOLLARS = 1000

# Longest a trade is held, in trading days, if neither the stop nor the target
# hits first. 1 = sold at the close the same day (no overnight risk).
# Backtest (Nov 2025 - Oct 2026): 1 day +$32, 3 days +$588, 5 days +$562.
MAX_HOLD_DAYS = 3

# Stop-loss and take-profit distances, in multiples of the 14-day ATR
# (average daily range). Each strategy has its own, see strategies.py.
ATR_WINDOW = 14

# How many trading days of history to pull for the indicators.
HISTORY_PERIOD = "2y"
