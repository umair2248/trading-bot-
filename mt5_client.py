"""MetaTrader 5 connection, market data and order helpers."""
import logging
import time

import MetaTrader5 as mt5
import pandas as pd

import config as C

log = logging.getLogger("mt5")

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def connect():
    for attempt in range(1, C.MT5_RECONNECT_ATTEMPTS + 1):
        kwargs = {"timeout": C.MT5_TIMEOUT_MS}
        if C.MT5_PATH:
            kwargs["path"] = C.MT5_PATH
        if C.MT5_LOGIN:
            kwargs.update(login=C.MT5_LOGIN, password=C.MT5_PASSWORD, server=C.MT5_SERVER)
        if mt5.initialize(**kwargs):
            acc = mt5.account_info()
            mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(acc.trade_mode, "?")
            log.info("Connected: %s | %s account | balance %.2f %s | leverage 1:%d",
                     acc.server, mode, acc.balance, acc.currency, acc.leverage)
            for s in C.SYMBOLS:
                if not mt5.symbol_select(s, True):
                    log.error("Symbol %s not available on this broker", s)
            return True
        log.warning("MT5 connect attempt %d failed: %s", attempt, mt5.last_error())
        time.sleep(C.MT5_RECONNECT_DELAY_SEC)
    return False


def ensure_connected():
    if mt5.terminal_info() is not None and mt5.account_info() is not None:
        return True
    log.warning("MT5 connection lost, reconnecting...")
    mt5.shutdown()
    return connect()


def rates(symbol, timeframe, count):
    data = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[timeframe], 0, count)
    if data is None or len(data) == 0:
        return None
    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def filling_mode(symbol):
    info = mt5.symbol_info(symbol)
    if info.filling_mode & 1:
        return mt5.ORDER_FILLING_FOK
    if info.filling_mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def round_volume(symbol, volume):
    info = mt5.symbol_info(symbol)
    step = info.volume_step
    vol = int(volume / step) * step  # always round down
    vol = min(vol, info.volume_max)
    return round(vol, 8) if vol >= info.volume_min else 0.0


def my_positions(symbol=None):
    pos = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    return [p for p in (pos or []) if p.magic == C.MAGIC_NUMBER]


def open_market(symbol, side, volume, sl, tp, comment):
    tick = mt5.symbol_info_tick(symbol)
    digits = mt5.symbol_info(symbol).digits
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if side == "BUY" else tick.bid,
        "sl": round(sl, digits),
        "tp": round(tp, digits),
        "deviation": 20,
        "magic": C.MAGIC_NUMBER,
        "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode(symbol),
    }
    return _send(req)


def modify_sl(position, new_sl):
    digits = mt5.symbol_info(position.symbol).digits
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": position.symbol,
        "position": position.ticket,
        "sl": round(new_sl, digits),
        "tp": position.tp,
        "magic": C.MAGIC_NUMBER,
    }
    return _send(req)


def close_partial(position, volume):
    tick = mt5.symbol_info_tick(position.symbol)
    is_buy = position.type == mt5.POSITION_TYPE_BUY
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "position": position.ticket,
        "volume": volume,
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if is_buy else tick.ask,
        "deviation": 20,
        "magic": C.MAGIC_NUMBER,
        "comment": "partial",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode(position.symbol),
    }
    return _send(req)


def _send(req):
    res = mt5.order_send(req)
    if res is None:
        log.error("order_send returned None: %s", mt5.last_error())
        return None
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        log.error("Order rejected (%s): %s", res.retcode, res.comment)
        return None
    return res
