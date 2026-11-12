"""Strategy lab: test several gold strategies on the same MT5 history.

Usage:  python lab.py [SYMBOL] [--bars 60000]

All strategies use fixed, textbook parameters (no optimisation). Conservative fills:
stop entries fill at the level, if SL and TP are in the same bar the SL wins,
the bar spread is charged on every trade. Times are server time (Exness = UTC).
A strategy PASSES only if profit factor >= 1.10 in BOTH the first 60% and last 40%.
"""
import argparse
import sys

import numpy as np
import pandas as pd

import config as C
import mt5_client as M


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ---------------------------------------------------------------- simulator
def simulate(df, signals, point, half_at_1r=False):
    """signals: list of dicts {i, side, kind('stop'|'market'), entry, sl, tp, exit_i}.
    Trade starts at bar i. Returns DataFrame of trades with result in R."""
    H, L, O, CL, SP = (df[c].to_numpy() for c in ("high", "low", "open", "close", "spread"))
    T = df["time"].to_numpy()
    out, busy_until = [], -1
    for s in signals:
        i = s["i"]
        if i <= busy_until or i >= len(df):
            continue
        d = 1 if s["side"] == "BUY" else -1
        entry = O[i] if s["kind"] == "market" else s["entry"]
        sl, tp = s["sl"], s["tp"]
        R = abs(entry - sl)
        if R <= 0:
            continue
        cost = SP[i] * point / R
        banked, left, be = 0.0, 1.0, False
        res, j = None, i
        for j in range(i, min(s["exit_i"], len(df) - 1) + 1):
            adverse = L[j] if d == 1 else H[j]
            favour = H[j] if d == 1 else L[j]
            if (adverse - sl) * d <= 0:
                res = banked + left * (sl - entry) * d / R
                break
            if j > i and (favour - tp) * d >= 0:
                res = banked + left * (tp - entry) * d / R
                break
            if half_at_1r and not be and j > i and (favour - (entry + d * R)) * d >= 0:
                be, banked, left, sl = True, 0.5, 0.5, entry
            if s.get("trail") and j > i:
                sl = s["trail"](j, sl, d)
        if res is None:  # time exit at close
            res = banked + left * (CL[j] - entry) * d / R
        out.append({"open": pd.Timestamp(T[i]), "side": s["side"], "R": res - cost})
        busy_until = j
    return pd.DataFrame(out)


# ---------------------------------------------------------------- strategies
def _days(df):
    return df.groupby(df["time"].dt.date).indices


def london_breakout(df, sl_mode="mid", rr=2.0, min_pct=0.3, max_pct=1.6):
    """Asian range 00:00-06:59; stop orders on both sides 07:00-11:59; exit 20:00."""
    sig, hr = [], df["time"].dt.hour.to_numpy()
    H, L = df["high"].to_numpy(), df["low"].to_numpy()
    for _, idx in _days(df).items():
        asia = [k for k in idx if hr[k] < 7]
        trade = [k for k in idx if 7 <= hr[k] < 12]
        close = [k for k in idx if hr[k] < 20]
        if len(asia) < 20 or not trade:
            continue
        hi, lo = H[asia].max(), L[asia].min()
        width = (hi - lo) / lo * 100
        if not (min_pct <= width <= max_pct):
            continue
        for k in trade:
            side = "BUY" if H[k] >= hi else "SELL" if L[k] <= lo else None
            if side is None:
                continue
            if H[k] >= hi and L[k] <= lo:
                break  # both sides in one bar: ambiguous, skip day
            entry = hi if side == "BUY" else lo
            if sl_mode == "mid":
                sl = (hi + lo) / 2
            else:
                sl = lo if side == "BUY" else hi
            R = abs(entry - sl)
            tp = entry + R * rr if side == "BUY" else entry - R * rr
            sig.append({"i": k, "side": side, "kind": "stop", "entry": entry, "sl": sl, "tp": tp,
                        "exit_i": close[-1]})
            break
    return sig


def ny_orb(df, rr=2.0):
    """Range 13:00-15:59 UTC; breakout 16:00-19:59 in direction of EMA200(M15) ~ EMA50(H1); exit 21:00."""
    sig, hr = [], df["time"].dt.hour.to_numpy()
    H, L, CL = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    ema = df["close"].ewm(span=200, adjust=False).mean().to_numpy()
    for _, idx in _days(df).items():
        rng = [k for k in idx if 13 <= hr[k] < 16]
        trade = [k for k in idx if 16 <= hr[k] < 20]
        close = [k for k in idx if hr[k] < 21]
        if len(rng) < 10 or not trade:
            continue
        hi, lo = H[rng].max(), L[rng].min()
        for k in trade:
            up, dn = H[k] >= hi, L[k] <= lo
            if up and dn:
                break
            if up and CL[k - 1] > ema[k - 1]:
                side, entry, sl = "BUY", hi, lo
            elif dn and CL[k - 1] < ema[k - 1]:
                side, entry, sl = "SELL", lo, hi
            elif up or dn:
                break  # broke against the trend filter: no trade today
            else:
                continue
            R = abs(entry - sl)
            tp = entry + R * rr if side == "BUY" else entry - R * rr
            sig.append({"i": k, "side": side, "kind": "stop", "entry": entry, "sl": sl, "tp": tp,
                        "exit_i": close[-1]})
            break
    return sig


def asia_fade(df):
    """London fakes out of the Asian range (closes back inside) -> fade it, target the other side."""
    sig, hr = [], df["time"].dt.hour.to_numpy()
    H, L, CL = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    for _, idx in _days(df).items():
        asia = [k for k in idx if hr[k] < 7]
        trade = [k for k in idx if 7 <= hr[k] < 12]
        close = [k for k in idx if hr[k] < 20]
        if len(asia) < 20 or not trade:
            continue
        hi, lo = H[asia].max(), L[asia].min()
        ext_hi, ext_lo = hi, lo
        for k in trade[:-1]:
            ext_hi, ext_lo = max(ext_hi, H[k]), min(ext_lo, L[k])
            if ext_hi > hi and lo < CL[k] < hi:
                sig.append({"i": k + 1, "side": "SELL", "kind": "market", "entry": 0, "sl": ext_hi,
                            "tp": lo, "exit_i": close[-1]})
                break
            if ext_lo < lo and lo < CL[k] < hi:
                sig.append({"i": k + 1, "side": "BUY", "kind": "market", "entry": 0, "sl": ext_lo,
                            "tp": hi, "exit_i": close[-1]})
                break
    return sig


def donchian_h1(df):
    """H1 close above 20-bar high -> buy (below low -> sell). SL 2 ATR, exit on 10-bar opposite channel."""
    sig = []
    hi20, lo20 = df["high"].rolling(20).max().shift(), df["low"].rolling(20).min().shift()
    lo10, hi10 = df["low"].rolling(10).min(), df["high"].rolling(10).max()
    a = atr(df).to_numpy()
    CL, n = df["close"].to_numpy(), len(df)

    def trail_fn(j, sl, d):
        lvl = lo10.iloc[j - 1] if d == 1 else hi10.iloc[j - 1]
        return max(sl, lvl) if d == 1 else min(sl, lvl)

    for k in range(200, n - 1):
        if CL[k] > hi20.iloc[k]:
            side, sl, tp = "BUY", CL[k] - 2 * a[k], CL[k] + 100 * a[k]
        elif CL[k] < lo20.iloc[k]:
            side, sl, tp = "SELL", CL[k] + 2 * a[k], CL[k] - 100 * a[k]
        else:
            continue
        sig.append({"i": k + 1, "side": side, "kind": "market", "entry": 0, "sl": sl, "tp": tp,
                    "exit_i": n - 1, "trail": trail_fn})
    return sig


def rsi2_h1(df):
    """Connors RSI(2) pullback in the EMA200 trend on H1. Exit at 1R target or SL 1.5 ATR or 10 bars."""
    sig = []
    r2, ema = rsi(df["close"], 2).to_numpy(), df["close"].ewm(span=200, adjust=False).mean().to_numpy()
    a, CL, n = atr(df).to_numpy(), df["close"].to_numpy(), len(df)
    for k in range(200, n - 1):
        if CL[k] > ema[k] and r2[k] < 10:
            side, sl, tp = "BUY", CL[k] - 1.5 * a[k], CL[k] + 1.5 * a[k]
        elif CL[k] < ema[k] and r2[k] > 90:
            side, sl, tp = "SELL", CL[k] + 1.5 * a[k], CL[k] - 1.5 * a[k]
        else:
            continue
        sig.append({"i": k + 1, "side": side, "kind": "market", "entry": 0, "sl": sl, "tp": tp,
                    "exit_i": min(k + 11, n - 1)})
    return sig


# ---------------------------------------------------------------- report
def pf(t):
    w, l = t[t["R"] > 0]["R"].sum(), -t[t["R"] <= 0]["R"].sum()
    return w / l if l > 0 else 99.0


def summary(name, t, risk=1.0):
    if len(t) < 30:
        return {"strategy": name, "trades": len(t), "PASS": False}
    cut = t["open"].iloc[int(len(t) * 0.6)]
    a, b = t[t["open"] < cut], t[t["open"] >= cut]
    eq = peak = 1.0
    mdd = 0.0
    for r in t["R"]:
        eq *= 1 + risk / 100 * r
        peak, mdd = max(peak, eq), max(mdd, 1 - eq / peak)
    months = max((t["open"].iloc[-1] - t["open"].iloc[0]).days / 30.4, 1)
    return {"strategy": name, "trades": len(t), "per_month": round(len(t) / months, 1),
            "win%": round((t["R"] > 0).mean() * 100), "PF": round(pf(t), 2),
            "PF_in": round(pf(a), 2), "PF_out": round(pf(b), 2), "R": round(t["R"].sum(), 1),
            "R/month": round(t["R"].sum() / months, 2), f"ret%@{risk:g}%": round((eq - 1) * 100, 1),
            "maxDD%": round(mdd * 100, 1), "PASS": pf(a) >= 1.1 and pf(b) >= 1.1}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default=C.SYMBOLS[0])
    ap.add_argument("--bars", type=int, default=60000)
    a = ap.parse_args()
    if not M.connect():
        sys.exit("Could not connect to MT5")
    point = M.mt5.symbol_info(a.symbol).point
    m15 = M.rates(a.symbol, "M15", a.bars)
    h1 = None
    for n in (a.bars, 30000, 20000, 15000, 10000):
        h1 = M.rates(a.symbol, "H1", n)
        if h1 is not None:
            break
    M.mt5.shutdown()
    print(f"M15 from {m15['time'].iloc[0]:%Y-%m-%d}, H1 from {h1['time'].iloc[0]:%Y-%m-%d}\n")

    tests = {
        "London breakout (SL mid, 2R)": (m15, london_breakout(m15, "mid", 2.0)),
        "London breakout (SL range, 1R)": (m15, london_breakout(m15, "range", 1.0)),
        "NY opening-range breakout + EMA": (m15, ny_orb(m15, 2.0)),
        "Asian range fake-out fade": (m15, asia_fade(m15)),
        "Donchian 20 breakout (H1)": (h1, donchian_h1(h1)),
        "RSI(2) pullback (H1)": (h1, rsi2_h1(h1)),
    }
    rows = []
    for name, (df, sig) in tests.items():
        for half in (False, True):
            label = name + (" +half@1R" if half else "")
            rows.append(summary(label, simulate(df, sig, point, half_at_1r=half)))
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(out.to_string(index=False))
    out.to_csv(C.ROOT / "lab_results.csv", index=False)


if __name__ == "__main__":
    main()
