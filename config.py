"""Settings loaded from the .env file (bot_v2/.env first, then the parent folder's .env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if not load_dotenv(ROOT / ".env"):
    load_dotenv(ROOT.parent / ".env")


def _s(key, default=""):
    return os.getenv(key, default).strip()


def _f(key, default):
    v = _s(key)
    return float(v) if v else float(default)


def _i(key, default):
    v = _s(key)
    return int(float(v)) if v else int(default)


def _b(key, default):
    v = _s(key).lower()
    return v in ("1", "true", "yes", "on") if v else default


# MT5 account
MT5_LOGIN = _i("MT5_LOGIN", 0)
MT5_PASSWORD = _s("MT5_PASSWORD")
MT5_SERVER = _s("MT5_SERVER")
MT5_PATH = _s("MT5_PATH")
MT5_TIMEOUT_MS = _i("MT5_TIMEOUT_MS", 60000)
MT5_RECONNECT_ATTEMPTS = _i("MT5_RECONNECT_ATTEMPTS", 5)
MT5_RECONNECT_DELAY_SEC = _i("MT5_RECONNECT_DELAY_SEC", 10)

SYMBOLS = [s.strip() for s in _s("SYMBOLS", "XAUUSD").split(",") if s.strip()]
TREND_TIMEFRAME = _s("TREND_TIMEFRAME", "H1")
ENTRY_TIMEFRAME = _s("ENTRY_TIMEFRAME", "M15")

# Strategy
EMA_FAST = _i("EMA_FAST", 50)
EMA_SLOW = _i("EMA_SLOW", 200)
RSI_PERIOD = _i("RSI_PERIOD", 14)
RSI_BUY_MIN = _f("RSI_BUY_MIN", 45)
RSI_BUY_MAX = _f("RSI_BUY_MAX", 60)
RSI_SELL_MIN = _f("RSI_SELL_MIN", 40)
RSI_SELL_MAX = _f("RSI_SELL_MAX", 55)
ATR_PERIOD = _i("ATR_PERIOD", 14)
ATR_MIN_MULTIPLIER = _f("ATR_MIN_MULTIPLIER", 0.5)
MACD_FAST = _i("MACD_FAST", 12)
MACD_SLOW = _i("MACD_SLOW", 26)
MACD_SIGNAL = _i("MACD_SIGNAL", 9)
EMA_RETRACE_TOLERANCE_PCT = _f("EMA_RETRACE_TOLERANCE_PCT", 0.15)
STRATEGY_NAME = _s("STRATEGY_NAME", "EMA_RSI_MACD")
LONDON_BREAKOUT_ENABLED = _b("LONDON_BREAKOUT_ENABLED", True)  # second strategy, see london.py
MAGIC_NUMBER = _i("MAGIC_NUMBER", 20260717)
SL_ATR_MULTIPLIER = _f("SL_ATR_MULTIPLIER", 1.5)
TP_RR_RATIO = _f("TP_RR_RATIO", 2.0)

# Risk
RISK_PER_TRADE_PCT = _f("RISK_PER_TRADE_PCT", 0.5)
MAX_OPEN_TRADES = _i("MAX_OPEN_TRADES", 2)
MAX_DAILY_LOSS_PCT = _f("MAX_DAILY_LOSS_PCT", 2.0)
MAX_WEEKLY_LOSS_PCT = _f("MAX_WEEKLY_LOSS_PCT", 5.0)
MAX_ACCOUNT_DRAWDOWN_PCT = _f("MAX_ACCOUNT_DRAWDOWN_PCT", 10.0)
MAX_CONSECUTIVE_LOSSES = _i("MAX_CONSECUTIVE_LOSSES", 3)
MIN_MARGIN_LEVEL_PCT = _f("MIN_MARGIN_LEVEL_PCT", 200.0)
MAX_EXPOSURE_PCT = _f("MAX_EXPOSURE_PCT", 5.0)
BREAK_EVEN_ENABLED = _b("BREAK_EVEN_ENABLED", True)
BREAK_EVEN_TRIGGER_RR = _f("BREAK_EVEN_TRIGGER_RR", 1.0)
TRAILING_STOP_ENABLED = _b("TRAILING_STOP_ENABLED", True)
TRAILING_STOP_ATR_MULTIPLIER = _f("TRAILING_STOP_ATR_MULTIPLIER", 1.0)
PARTIAL_CLOSE_ENABLED = _b("PARTIAL_CLOSE_ENABLED", True)
PARTIAL_CLOSE_PCT = _f("PARTIAL_CLOSE_PCT", 50.0)

# Filters
# Spread must be at most this % of the stop-loss distance (works for every asset class).
MAX_SPREAD_PCT_OF_SL = _f("MAX_SPREAD_PCT_OF_SL", 10)
# A symbol whose last price is older than this is treated as closed (stocks/indices off-hours).
MAX_TICK_AGE_SEC = _i("MAX_TICK_AGE_SEC", 300)
FRIDAY_CUTOFF_HOUR_UTC = _i("FRIDAY_CUTOFF_HOUR_UTC", 20)
NEWS_FILTER_ENABLED = _b("NEWS_FILTER_ENABLED", True)
NEWS_BUFFER_MINUTES = _i("NEWS_BUFFER_MINUTES", 30)
NEWS_IMPACT_LEVELS = [s.strip() for s in _s("NEWS_IMPACT_LEVELS", "High").split(",") if s.strip()]
FOREX_FACTORY_URL = _s("FOREX_FACTORY_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")

# Telegram
TELEGRAM_ENABLED = _b("TELEGRAM_ENABLED", False)
TELEGRAM_BOT_TOKEN = _s("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _s("TELEGRAM_CHAT_ID")

# Bot
LOOP_INTERVAL_SEC = _i("LOOP_INTERVAL_SEC", 30)
BARS_TREND = _i("BARS_TREND", 300)
BARS_ENTRY = _i("BARS_ENTRY", 300)
DRY_RUN = _b("DRY_RUN", True)
LOG_LEVEL = _s("LOG_LEVEL", "INFO")
STATE_FILE = ROOT / "state.json"
LOG_FILE = ROOT / "logs" / "bot.log"
