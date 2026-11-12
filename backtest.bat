@echo off
cd /d "%~dp0"
py -V:3.12 backtest.py %*
pause
