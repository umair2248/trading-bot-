"""EMA trend + pullback entry, confirmed by RSI and MACD momentum.

Rules (BUY; SELL is the mirror image):
  1. Trend timeframe (H1): close > EMA200 and EMA50 > EMA200.
  2. Entry timeframe (M15) pullback: bar low touched EMA50 (within tolerance) and closed back above it.
  3. RSI between RSI_BUY_MIN and RSI_BUY_MAX (not overbought).
  4. MACD histogram rising (momentum turning back in trend direction).
  5. ATR at least ATR_MIN_MULTIPLIER x its 50-bar average (market is not dead).
  SL = 1.5 x ATR beyond entry, TP = 2R.
"""
import numpy as np
import pandas as pd

import config as C


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df, n):
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_trend_indicators(df):
    df = df.copy()
    df["ema_fast"] = ema(df["close"], C.EMA_FAST)
    df["ema_slow"] = ema(df["close"], C.EMA_SLOW)
    df["trend"] = 0
    df.loc[(df["close"] > df["ema_slow"]) & (df["ema_fast"] > df["ema_slow"]), "trend"] = 1
    df.loc[(df["close"] < df["ema_slow"]) & (df["ema_fast"] < df["ema_slow"]), "trend"] = -1
    return df


def add_entry_indicators(df):
    df = df.copy()
    df["ema_fast"] = ema(df["close"], C.EMA_FAST)
    df["rsi"] = rsi(df["close"], C.RSI_PERIOD)
    macd = ema(df["close"], C.MACD_FAST) - ema(df["close"], C.MACD_SLOW)
    df["hist"] = macd - ema(macd, C.MACD_SIGNAL)
    df["hist_prev"] = df["hist"].shift()
    df["atr"] = atr(df, C.ATR_PERIOD)
    df["atr_avg"] = df["atr"].rolling(50).mean()
    return df


def entry_signal(trend, row):
    """trend: +1/-1/0 from the last CLOSED trend bar. row: last CLOSED entry bar (with indicators).
    Returns 'BUY', 'SELL' or None."""
    if pd.isna(row["atr_avg"]) or row["atr"] < C.ATR_MIN_MULTIPLIER * row["atr_avg"]:
        return None
    tol = row["ema_fast"] * C.EMA_RETRACE_TOLERANCE_PCT / 100
    if trend == 1:
        if (row["low"] <= row["ema_fast"] + tol and row["close"] > row["ema_fast"]
                and C.RSI_BUY_MIN <= row["rsi"] <= C.RSI_BUY_MAX
                and row["hist"] > row["hist_prev"]):
            return "BUY"
    elif trend == -1:
        if (row["high"] >= row["ema_fast"] - tol and row["close"] < row["ema_fast"]
                and C.RSI_SELL_MIN <= row["rsi"] <= C.RSI_SELL_MAX
                and row["hist"] < row["hist_prev"]):
            return "SELL"
    return None


def sl_tp(side, entry, atr_value):
    dist = C.SL_ATR_MULTIPLIER * atr_value
    if side == "BUY":
        return entry - dist, entry + dist * C.TP_RR_RATIO
    return entry + dist, entry - dist * C.TP_RR_RATIO
