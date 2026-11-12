"""London breakout of the Asian range (runs next to the EMA strategy, own magic number).

Times are MT5 server time (Exness = UTC). Pakistan time = UTC + 5.
  00:00-06:59 UTC  Asian range is measured            (05:00-11:59 PKT)
  07:00 UTC        BUY STOP at range high + SELL STOP at range low  (12:00 PKT)
  first fill       the other order is cancelled (one trade per day)
  12:00 UTC        unfilled orders are cancelled       (17:00 PKT)
  20:00 UTC        any open trade is closed            (01:00 PKT)
SL = middle of the range, TP = 2R. At 1R: half is booked and SL moves to entry
(done by bot.manage). Skips days when the range is < 0.3% or > 1.6% of price.
Backtest (lab.py, M15 2024-05 -> 2026-11): PF 1.16, max DD 10.7% at 1% risk.
"""
import logging
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

import config as C
import mt5_client as M
import risk

log = logging.getLogger("london")
MAGIC = C.MAGIC_NUMBER + 1
NAME = "LONDON_BO"
RANGE_END, ENTRY_END, EXIT_HOUR = 7, 12, 20
MIN_PCT, MAX_PCT, RR = 0.3, 1.6, 2.0
_done = {}  # symbol -> date already handled


def _now():
    return datetime.now(timezone.utc)


def _traded_today(symbol, today):
    start = datetime(today.year, today.month, today.day)
    deals = mt5.history_deals_get(start, datetime.now() + timedelta(days=1)) or []
    return any(d.magic == MAGIC and d.symbol == symbol and d.entry == mt5.DEAL_ENTRY_IN for d in deals)


def _place(symbol, today):
    bars = M.rates(symbol, "M15", 200)
    if bars is None:
        return
    asia = bars[(bars["time"].dt.date == today) & (bars["time"].dt.hour < RANGE_END)]
    if len(asia) < 20:
        log.info("%s: Asian range incomplete (%d bars) - no London trade today", symbol, len(asia))
        return
    hi, lo = asia["high"].max(), asia["low"].min()
    width = (hi - lo) / lo * 100
    if not MIN_PCT <= width <= MAX_PCT:
        log.info("%s: Asian range %.2f%% outside %.1f-%.1f%% - skip today", symbol, width, MIN_PCT, MAX_PCT)
        return
    tick = mt5.symbol_info_tick(symbol)
    if not (lo < tick.bid and tick.ask < hi):
        log.info("%s: price already outside the Asian range (%.2f-%.2f) - skip today", symbol, lo, hi)
        return
    mid = (hi + lo) / 2
    R = (hi - lo) / 2
    for check in (risk.account_ok, lambda: risk.market_ok(symbol, R)):
        ok, why = check()
        if not ok:
            log.info("%s London breakout blocked: %s", symbol, why)
            return
    vol = risk.lot_size(symbol, R)
    if vol <= 0:
        log.info("%s: lot below minimum for %.1f%% risk - skip", symbol, C.RISK_PER_TRADE_PCT)
        return
    d = mt5.symbol_info(symbol).digits
    msg = (f"London breakout {symbol}: range {lo:.{d}f}-{hi:.{d}f} | BUY STOP {hi:.{d}f} / "
           f"SELL STOP {lo:.{d}f} | {vol} lots | SL {mid:.{d}f} | TP 2R")
    if C.DRY_RUN:
        return "[DRY RUN] " + msg
    b = M.place_stop(symbol, "BUY", vol, hi, mid, hi + RR * R, MAGIC, NAME)
    s = M.place_stop(symbol, "SELL", vol, lo, mid, lo - RR * R, MAGIC, NAME)
    if not (b and s):
        for o in M.my_orders(symbol, MAGIC):
            M.cancel_order(o)
        return None
    return "PLACED " + msg


def _deadline(ts, hour):
    day = datetime.fromtimestamp(ts, timezone.utc)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def step(symbol, notify):
    """Call every loop."""
    now = _now()
    today = now.date()
    orders = M.my_orders(symbol, MAGIC)
    positions = M.my_positions(symbol, MAGIC)

    # One side filled -> cancel the other (OCO).
    if positions and orders:
        for o in orders:
            M.cancel_order(o)
        notify(f"London breakout {symbol}: {'BUY' if positions[0].type == 0 else 'SELL'} filled, "
               f"other order cancelled")
    # Entry window over -> cancel unfilled orders (also old ones if the bot was off).
    for o in ([] if positions else orders):
        if now >= _deadline(o.time_setup, ENTRY_END) and M.cancel_order(o):
            log.info("%s: no breakout by %02d:00 UTC - pending order cancelled", symbol, ENTRY_END)
    # Time exit at 20:00 UTC of the day the trade opened.
    for p in positions:
        if now >= _deadline(p.time, EXIT_HOUR) and M.close_position(p):
            notify(f"London breakout {symbol}: closed at {EXIT_HOUR}:00 UTC, P/L {p.profit:.2f}")

    # Place orders once per day inside the entry window.
    if _done.get(symbol) == today or not RANGE_END <= now.hour < ENTRY_END or now.weekday() >= 5:
        return
    _done[symbol] = today
    if orders or positions or _traded_today(symbol, today):
        return  # already set up (e.g. bot restarted)
    msg = _place(symbol, today)
    if msg:
        notify(msg)
