"""Live trading loop.  Run:  python bot.py
DRY_RUN=true in .env -> only logs signals, never sends orders.
Real-money accounts are refused unless ALLOW_REAL_ACCOUNT=true is set in .env.
"""
import logging
import os
import sys
import time

import MetaTrader5 as mt5
import requests

import config as C
import mt5_client as M
import risk
import strategy as S

C.LOG_FILE.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, C.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[logging.FileHandler(C.LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bot")


def notify(text):
    log.info(text)
    if C.TELEGRAM_ENABLED and C.TELEGRAM_BOT_TOKEN and C.TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id": C.TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        except Exception as e:
            log.warning("Telegram failed: %s", e)


def closed_bars(symbol):
    h = M.rates(symbol, C.TREND_TIMEFRAME, C.BARS_TREND)
    m = M.rates(symbol, C.ENTRY_TIMEFRAME, C.BARS_ENTRY)
    if h is None or m is None:
        return None, None
    # Drop the still-forming last bar of each timeframe.
    return S.add_trend_indicators(h.iloc[:-1]), S.add_entry_indicators(m.iloc[:-1])


def look_for_entry(symbol, last_bar_seen):
    h, m = closed_bars(symbol)
    if m is None:
        log.warning("%s: no data", symbol)
        return last_bar_seen
    row = m.iloc[-1]
    if last_bar_seen.get(symbol) == row["time"]:
        return last_bar_seen
    last_bar_seen[symbol] = row["time"]

    trend = int(h["trend"].iloc[-1])
    side = S.entry_signal(trend, row)
    log.info("%s bar %s | trend %s | RSI %.1f | signal %s", symbol, row["time"],
             {1: "UP", -1: "DOWN", 0: "NONE"}[trend], row["rsi"], side or "-")
    if not side:
        return last_bar_seen
    if M.my_positions(symbol):
        log.info("%s: already have a position, skip", symbol)
        return last_bar_seen
    for check in (risk.account_ok, lambda: risk.market_ok(symbol)):
        ok, why = check()
        if not ok:
            log.info("%s %s blocked: %s", symbol, side, why)
            return last_bar_seen

    tick = mt5.symbol_info_tick(symbol)
    entry = tick.ask if side == "BUY" else tick.bid
    sl, tp = S.sl_tp(side, entry, row["atr"])
    info = mt5.symbol_info(symbol)
    if abs(entry - sl) < info.trade_stops_level * info.point:
        log.info("%s: SL closer than broker stops level, skip", symbol)
        return last_bar_seen
    vol = risk.lot_size(symbol, abs(entry - sl))
    if vol <= 0:
        log.info("%s: lot size below broker minimum for %.2f%% risk, skip", symbol, C.RISK_PER_TRADE_PCT)
        return last_bar_seen

    msg = f"{side} {symbol} {vol} lots @ {entry:.{info.digits}f} SL {sl:.{info.digits}f} TP {tp:.{info.digits}f}"
    if C.DRY_RUN:
        notify("[DRY RUN] would open " + msg)
    elif M.open_market(symbol, side, vol, sl, tp, C.STRATEGY_NAME):
        notify("OPENED " + msg)
    return last_bar_seen


def manage(position, atr_value):
    """Break-even + partial close at BREAK_EVEN_TRIGGER_RR, then ATR trailing stop."""
    is_buy = position.type == mt5.POSITION_TYPE_BUY
    d = 1 if is_buy else -1
    R = abs(position.tp - position.price_open) / C.TP_RR_RATIO if position.tp else 0
    if R <= 0:
        return
    tick = mt5.symbol_info_tick(position.symbol)
    price = tick.bid if is_buy else tick.ask
    info = mt5.symbol_info(position.symbol)
    secured = position.sl and (position.sl - position.price_open) * d >= 0

    if not secured and (price - position.price_open) * d >= C.BREAK_EVEN_TRIGGER_RR * R:
        if C.PARTIAL_CLOSE_ENABLED:
            vol = M.round_volume(position.symbol, position.volume * C.PARTIAL_CLOSE_PCT / 100)
            if vol > 0 and position.volume - vol >= info.volume_min and not C.DRY_RUN:
                if M.close_partial(position, vol):
                    notify(f"Partial close {vol} lots of #{position.ticket} {position.symbol}")
        if C.BREAK_EVEN_ENABLED and not C.DRY_RUN:
            fresh = mt5.positions_get(ticket=position.ticket)
            if fresh and M.modify_sl(fresh[0], fresh[0].price_open):
                notify(f"#{position.ticket} {position.symbol} SL moved to break-even")
        return

    if secured and C.TRAILING_STOP_ENABLED and not C.DRY_RUN:
        new_sl = price - d * C.TRAILING_STOP_ATR_MULTIPLIER * atr_value
        min_gap = max(info.trade_stops_level, 1) * info.point
        if (new_sl - position.sl) * d > 10 * info.point and abs(price - new_sl) >= min_gap:
            if M.modify_sl(position, new_sl):
                log.info("#%s trailing SL -> %.*f", position.ticket, info.digits, new_sl)


def main():
    if not M.connect():
        sys.exit("Could not connect to MT5. Open the MT5 terminal, log in, and check .env (MT5_LOGIN/PASSWORD/SERVER).")
    acc = mt5.account_info()
    if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL and not C.DRY_RUN \
            and os.getenv("ALLOW_REAL_ACCOUNT", "").lower() != "true":
        sys.exit("REAL account detected. Test on demo first. To allow real trading set ALLOW_REAL_ACCOUNT=true in .env.")
    if not C.DRY_RUN and not mt5.terminal_info().trade_allowed:
        sys.exit("Algo Trading is OFF in MT5. Click the 'Algo Trading' button in the terminal toolbar.")

    notify(f"Bot started | {C.STRATEGY_NAME} | {', '.join(C.SYMBOLS)} | "
           f"{'DRY RUN (no real orders)' if C.DRY_RUN else 'LIVE ORDERS'} | risk {C.RISK_PER_TRADE_PCT}%/trade")
    seen = {}
    try:
        while True:
            if not M.ensure_connected():
                time.sleep(C.MT5_RECONNECT_DELAY_SEC)
                continue
            for symbol in C.SYMBOLS:
                try:
                    seen = look_for_entry(symbol, seen)
                    positions = M.my_positions(symbol)
                    if positions:
                        _, m = closed_bars(symbol)
                        for p in positions:
                            manage(p, m["atr"].iloc[-1])
                except Exception:
                    log.exception("Error while processing %s", symbol)
            time.sleep(C.LOOP_INTERVAL_SEC)
    except KeyboardInterrupt:
        notify("Bot stopped by user")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
