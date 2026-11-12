# Trading Bot v2 (MT5 + Python)

Strategy: H1 EMA50/200 trend -> M15 pullback to EMA50, RSI + MACD confirmation,
SL = 1.5 x ATR, TP = 2R, break-even + 50% partial at 1R, ATR trailing stop.
Settings are read from `../.env` (or a `.env` in this folder).

1. Log in to your account in the MT5 terminal (Exness). Turn on **Algo Trading**.
2. Put the same login/password/server in `.env`.
3. Double-click `backtest.bat`  -> tests the strategy on ~2 years of history.
4. Double-click `start_bot.bat`  -> runs live. With `DRY_RUN=true` it only logs signals.
5. Set `DRY_RUN=false` only on a DEMO account first. Real accounts are blocked unless
   `ALLOW_REAL_ACCOUNT=true`.

Safety: 0.5% risk/trade, daily loss 2%, weekly 5%, max drawdown 10% (bot stops),
3 losses in a row -> stops for the day, spread / news / Friday filters.
Logs: `logs/bot.log`.  No strategy guarantees profit.
