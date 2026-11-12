"""Backtest the strategy on many symbols and pick the ones that worked.

Usage:  python scan.py [--bars 60000] [SYMBOL ...]

Each symbol's trades are split by time: the first 60% ("in-sample") and the
last 40% ("out-of-sample"). A symbol PASSES only if it was profitable in BOTH
parts with enough trades - this filters out symbols that only look good by luck.
Results are saved to scan_results.csv.
"""
import argparse
import sys
import time

import pandas as pd

import backtest as B
import config as C
import mt5_client as M

CANDIDATES = [
    # Forex majors + main crosses
    "EURUSDm", "GBPUSDm", "USDJPYm", "USDCHFm", "USDCADm", "AUDUSDm", "NZDUSDm",
    "EURJPYm", "GBPJPYm", "EURGBPm", "AUDJPYm", "EURAUDm", "GBPAUDm", "EURCHFm",
    "CADJPYm", "CHFJPYm", "AUDCADm", "AUDNZDm", "EURCADm", "GBPCADm", "NZDJPYm",
    # Metals / energy
    "XAUUSDm", "XAGUSDm", "USOILm", "UKOILm",
    # Indices
    "US30m", "US500m", "USTECm", "DE30m", "UK100m", "JP225m",
    # Crypto
    "BTCUSDm", "ETHUSDm", "SOLUSDm", "XRPUSDm", "BNBUSDm", "LTCUSDm", "DOGEUSDm", "ADAUSDm",
    # Stocks
    "AAPLm", "MSFTm", "NVDAm", "AMZNm", "GOOGLm", "METAm", "TSLAm", "AMDm", "NFLXm", "JPMm",
]

MIN_TRADES = 40        # total trades needed to judge a symbol
MIN_PF = 1.10          # profit factor needed in BOTH halves


def stats(t):
    if t.empty:
        return 0, 0.0, 0.0
    w, l = t[t["R"] > 0]["R"].sum(), -t[t["R"] <= 0]["R"].sum()
    return len(t), (w / l if l > 0 else 99.0), t["R"].sum()


def max_dd(t):
    eq = peak = 1.0
    mdd = 0.0
    for r in t["R"]:
        eq *= 1 + C.RISK_PER_TRADE_PCT / 100 * r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return mdd * 100


def scan_one(symbol, bars):
    info = M.mt5.symbol_info(symbol)
    if info is None or not M.mt5.symbol_select(symbol, True):
        return None
    df = None
    for _ in range(3):  # first request may only trigger the history download
        try:
            df = B.load(symbol, bars)
        except SystemExit:
            df = None
        if df is not None and len(df) > 5000:
            break
        time.sleep(3)
    if df is None or len(df) < 2000:
        return None
    t = B.run(df, info.point)
    if t.empty:
        return None
    cut = t["open"].iloc[int(len(t) * 0.6)] if len(t) > 5 else t["open"].iloc[-1]
    n, pf, r = stats(t)
    n1, pf1, r1 = stats(t[t["open"] < cut])
    n2, pf2, r2 = stats(t[t["open"] >= cut])
    ok = n >= MIN_TRADES and pf1 >= MIN_PF and pf2 >= MIN_PF
    return {"symbol": symbol, "from": f"{t['open'].iloc[0]:%Y-%m}", "trades": n,
            "PF": round(pf, 2), "R": round(r, 1), "maxDD%": round(max_dd(t), 1),
            "PF_in": round(pf1, 2), "PF_out": round(pf2, 2), "R_out": round(r2, 1),
            "PASS": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--bars", type=int, default=60000)
    a = ap.parse_args()
    if not M.connect():
        sys.exit("Could not connect to MT5")
    rows = []
    for s in a.symbols or CANDIDATES:
        try:
            res = scan_one(s, a.bars)
        except Exception as e:
            print(f"{s:10s} error: {e}", flush=True)
            continue
        if res is None:
            print(f"{s:10s} no data / no trades", flush=True)
            continue
        rows.append(res)
        print(f"{s:10s} trades {res['trades']:4d} | PF {res['PF']:.2f} | in {res['PF_in']:.2f} "
              f"out {res['PF_out']:.2f} | DD {res['maxDD%']:.1f}% | {'PASS' if res['PASS'] else '-'}",
              flush=True)
    M.mt5.shutdown()
    if not rows:
        return
    out = pd.DataFrame(rows).sort_values(["PASS", "PF_out"], ascending=False)
    out.to_csv(C.ROOT / "scan_results.csv", index=False)
    print("\n" + out.to_string(index=False))
    passed = out[out["PASS"]]["symbol"].tolist()
    print("\nPASSED:", ",".join(passed) if passed else "none")


if __name__ == "__main__":
    main()
