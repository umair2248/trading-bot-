"""Position sizing and account-protection rules. Any rule failing => no new trades."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import requests

import config as C
import mt5_client as M

log = logging.getLogger("risk")
_news_cache = {"at": None, "events": []}


def money_per_price_unit(symbol, volume=1.0):
    info = mt5.symbol_info(symbol)
    return info.trade_tick_value / info.trade_tick_size * volume


def lot_size(symbol, sl_distance):
    acc = mt5.account_info()
    risk_money = acc.balance * C.RISK_PER_TRADE_PCT / 100
    per_lot = sl_distance * money_per_price_unit(symbol)
    return M.round_volume(symbol, risk_money / per_lot) if per_lot > 0 else 0.0


def _closed_deals(since):
    deals = mt5.history_deals_get(since, datetime.now() + timedelta(days=1)) or []
    return [d for d in deals if d.magic == C.MAGIC_NUMBER
            and d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]


def _pnl(deals):
    return sum(d.profit + d.commission + d.swap for d in deals)


def _load_state():
    try:
        return json.loads(C.STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state):
    try:
        C.STATE_FILE.write_text(json.dumps(state))
    except Exception as e:
        log.warning("Could not save state: %s", e)


def account_ok():
    """Returns (ok, reason)."""
    acc = mt5.account_info()
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())

    day_deals = _closed_deals(today)
    day_pnl = _pnl(day_deals)
    if day_pnl < 0 and -day_pnl >= (acc.balance - day_pnl) * C.MAX_DAILY_LOSS_PCT / 100:
        return False, f"daily loss limit hit ({day_pnl:.2f})"

    week_pnl = _pnl(_closed_deals(week))
    if week_pnl < 0 and -week_pnl >= (acc.balance - week_pnl) * C.MAX_WEEKLY_LOSS_PCT / 100:
        return False, f"weekly loss limit hit ({week_pnl:.2f})"

    state = _load_state()
    peak = max(state.get("peak_equity", 0), acc.equity)
    if peak != state.get("peak_equity"):
        state["peak_equity"] = peak
        _save_state(state)
    dd = (1 - acc.equity / peak) * 100 if peak else 0
    if dd >= C.MAX_ACCOUNT_DRAWDOWN_PCT:
        return False, f"max drawdown {dd:.1f}% hit - bot stopped, review strategy (delete state.json to reset)"

    # Consecutive losses today -> stop for the rest of the day.
    streak = 0
    for d in sorted(day_deals, key=lambda d: d.time, reverse=True):
        if d.profit + d.commission + d.swap < 0:
            streak += 1
        else:
            break
    if streak >= C.MAX_CONSECUTIVE_LOSSES:
        return False, f"{streak} losses in a row today"

    if acc.margin > 0 and acc.margin_level < C.MIN_MARGIN_LEVEL_PCT:
        return False, f"margin level {acc.margin_level:.0f}% too low"

    positions = M.my_positions()
    if len(positions) >= C.MAX_OPEN_TRADES:
        return False, f"{len(positions)} trades already open"

    exposure = 0.0
    for p in positions:
        at_risk = (p.price_open - p.sl) if p.type == mt5.POSITION_TYPE_BUY else (p.sl - p.price_open)
        if p.sl and at_risk > 0:
            exposure += at_risk * money_per_price_unit(p.symbol, p.volume)
    if exposure / acc.balance * 100 + C.RISK_PER_TRADE_PCT > C.MAX_EXPOSURE_PCT:
        return False, "max exposure reached"
    return True, ""


CRYPTO = {"BTC", "ETH", "SOL", "XRP", "BNB", "LTC", "DOGE", "ADA", "BCH", "DOT", "LINK",
          "MATIC", "AVAX", "UNI", "AAVE", "XTZ", "FIL", "TRX", "TON"}


def is_crypto(symbol):
    return mt5.symbol_info(symbol).currency_base.upper() in CRYPTO


def market_ok(symbol, sl_distance):
    """sl_distance is in price units; the spread must be a small fraction of it."""
    now = datetime.now(timezone.utc)
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
        return False, "trading disabled for this symbol"
    if tick is None or time.time() - tick.time > C.MAX_TICK_AGE_SEC:
        return False, "market closed (no fresh price)"
    if not is_crypto(symbol):  # crypto trades 24/7 with no weekend gap
        if now.weekday() == 4 and now.hour >= C.FRIDAY_CUTOFF_HOUR_UTC:
            return False, "Friday cutoff (weekend gap risk)"
        if now.weekday() >= 5:
            return False, "weekend"
    spread = tick.ask - tick.bid
    if sl_distance <= 0 or spread / sl_distance * 100 > C.MAX_SPREAD_PCT_OF_SL:
        return False, f"spread {spread:.{info.digits}f} > {C.MAX_SPREAD_PCT_OF_SL:.0f}% of SL distance"
    if C.NEWS_FILTER_ENABLED:
        ev = upcoming_news(symbol)
        if ev:
            return False, f"news: {ev}"
    return True, ""


def _currencies(symbol):
    info = mt5.symbol_info(symbol)
    cur = {info.currency_base, info.currency_profit}
    if symbol.upper().startswith(("XAU", "XAG")):
        cur.add("USD")
    return cur


def upcoming_news(symbol):
    now = datetime.now(timezone.utc)
    if _news_cache["at"] is None or now - _news_cache["at"] > timedelta(hours=1):
        try:
            _news_cache["events"] = requests.get(C.FOREX_FACTORY_URL, timeout=15).json()
            _news_cache["at"] = now
        except Exception as e:
            log.warning("News calendar unavailable (%s) - news filter skipped this cycle", e)
            _news_cache["at"] = now - timedelta(minutes=50)  # retry in ~10 min
    buf = timedelta(minutes=C.NEWS_BUFFER_MINUTES)
    cur = _currencies(symbol)
    for e in _news_cache["events"]:
        if e.get("impact") not in C.NEWS_IMPACT_LEVELS or e.get("country") not in cur:
            continue
        try:
            t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except Exception:
            continue
        if t - buf <= now <= t + buf:
            return f"{e.get('country')} {e.get('title')} at {t:%H:%M} UTC"
    return None
