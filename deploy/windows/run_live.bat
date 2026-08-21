@echo off
rem Live trading session - scheduled weekdays before the open (engine waits for 10:00 ET itself)
cd /d C:\Algo\IntraDayTradingAlgo
if not exist state\logs mkdir state\logs
.venv\Scripts\python.exe -m live.run_live >> state\logs\task_live.log 2>&1
