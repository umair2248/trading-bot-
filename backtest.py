"""Backtest the strategy on MT5 history.

Usage:  python backtest.py [SYMBOL] [--bars 60000] [--plain]
  --plain  disables break-even / partial close / trailing stop (to compare).

Conservative assumptions: entry at next bar's open, if SL and TP are both inside
one bar the SL is assumed hit first, spread of each bar is charged as a cost.
"""
import argparse
import sys

import pandas as pd

import config as C
import mt5_client as M
import strategy as S


def load(symbol, bars):
    m = M.rates(symbol, C.ENTRY_TIMEFRAME, bars)
    h = M.rates(symbol, C.TREND_TIMEFRAME, max(bars // 4 + 500, 2000))
    if m is None or h is None:
        sys.exit(f"No history for {symbol}. Open its chart in MT5 once and scroll back, then retry.")
    tf_min = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
    m = S.add_entry_indicators(m)
    h = S.add_trend_indicators(h)
    m["close_time"] = m["time"] + pd.Timedelta(minutes=tf_min[C.ENTRY_TIMEFRAME])
    h["close_time"] = h["time"] + pd.Timedelta(minutes=tf_min[C.TREND_TIMEFRAME])
    # Only use trend bars that were already closed when the entry bar closed (no look-ahead).
    m = pd.merge_asof(m.sort_values("close_time"), h[["close_time", "trend"]].sort_values("close_time"),
                      on="close_time", direction="backward")
    return m.iloc[max(C.EMA_SLOW, 250):].reset_index(drop=True)


def run(df, point, manage=True):
    trades, pos = [], None
    part = C.PARTIAL_CLOSE_PCT / 100 if (manage and C.PARTIAL_CLOSE_ENABLED) else 0.0
    for i in range(len(df) - 1):
        bar = df.iloc[i]
        if pos:
            d = 1 if pos["side"] == "BUY" else -1
            hi, lo = bar["high"], bar["low"]
            adverse = lo if d == 1 else hi
            favour = hi if d == 1 else lo
            exit_px = None
            if (adverse - pos["sl"]) * d <= 0:
                exit_px = pos["sl"]
            elif (favour - pos["tp"]) * d >= 0:
                exit_px = pos["tp"]
            elif manage:
                trigger = pos["entry"] + d * pos["R"] * C.BREAK_EVEN_TRIGGER_RR
                if not pos["be"] and (favour - trigger) * d >= 0:
                    pos["be"] = True
                    if part:
                        pos["banked"] += part * C.BREAK_EVEN_TRIGGER_RR
                        pos["left"] -= part
                    if C.BREAK_EVEN_ENABLED:
                        pos["sl"] = pos["entry"]
                if pos["be"] and C.TRAILING_STOP_ENABLED:
                    trail = bar["close"] - d * C.TRAILING_STOP_ATR_MULTIPLIER * bar["atr"]
                    if (trail - pos["sl"]) * d > 0:
                        pos["sl"] = trail
            if exit_px is not None:
                r = pos["banked"] + pos["left"] * (exit_px - pos["entry"]) * d / pos["R"] - pos["cost"]
                trades.append({"open": pos["time"], "close": bar["time"], "side": pos["side"], "R": r})
                pos = None
        if pos is None:
            side = S.entry_signal(bar["trend"], bar)
            if side:
                nxt = df.iloc[i + 1]
                entry = nxt["open"]
                sl, tp = S.sl_tp(side, entry, bar["atr"])
                R = abs(entry - sl)
                pos = {"side": side, "entry": entry, "sl": sl, "tp": tp, "R": R, "be": False,
                       "banked": 0.0, "left": 1.0, "cost": nxt["spread"] * point / R, "time": nxt["time"]}
    return pd.DataFrame(trades)


def report(t, label):
    print(f"\n=== {label} ===")
    if t.empty:
        print("No trades.")
        return
    eq, peak, mdd, curve = 1.0, 1.0, 0.0, []
    for r in t["R"]:
        eq *= 1 + C.RISK_PER_TRADE_PCT / 100 * r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        curve.append(eq)
    wins, losses = t[t["R"] > 0]["R"], t[t["R"] <= 0]["R"]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    days = (t["close"].iloc[-1] - t["open"].iloc[0]).days or 1
    print(f"Period        : {t['open'].iloc[0]:%Y-%m-%d} -> {t['close'].iloc[-1]:%Y-%m-%d} ({days} days)")
    print(f"Trades        : {len(t)}  (~{len(t) / days * 30:.1f} per month)")
    print(f"Win rate      : {len(wins) / len(t) * 100:.1f}%")
    print(f"Avg trade     : {t['R'].mean():+.3f} R")
    print(f"Profit factor : {pf:.2f}")
    print(f"Total         : {t['R'].sum():+.1f} R")
    print(f"Return        : {(eq - 1) * 100:+.1f}% at {C.RISK_PER_TRADE_PCT}% risk per trade")
    print(f"Max drawdown  : {mdd * 100:.1f}%")
    t = t.assign(year=t["open"].dt.year)
    print("By year (R)   :", ", ".join(f"{y}: {r:+.1f}" for y, r in t.groupby("year")["R"].sum().items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default=C.SYMBOLS[0])
    ap.add_argument("--bars", type=int, default=60000)
    ap.add_argument("--plain", action="store_true")
    a = ap.parse_args()
    if not M.connect():
        sys.exit("Could not connect to MT5")
    point = M.mt5.symbol_info(a.symbol).point
    df = load(a.symbol, a.bars)
    t = run(df, point, manage=not a.plain)
    report(t, f"{a.symbol} {C.STRATEGY_NAME} {'(plain SL/TP)' if a.plain else '(with BE/partial/trailing)'}")
    if not t.empty:
        t.to_csv(C.ROOT / f"backtest_{a.symbol}.csv", index=False)
    M.mt5.shutdown()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
