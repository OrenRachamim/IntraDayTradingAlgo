@echo off
rem Weekly automated maintenance - scheduled Sundays
cd /d C:\Algo\IntraDayTradingAlgo
if not exist state\logs mkdir state\logs
.venv\Scripts\python.exe -m maintenance.run_maintenance >> state\logs\task_maint.log 2>&1
