@echo off
setlocal enabledelayedexpansion
rem Safe "close RDP" for VPSes where IB Gateway dies instantly on disconnect
rem (Java display-teardown crash). Redirects this session to the physical
rem console so the desktop keeps a live display; the RDP viewer drops on its
rem own and Gateway keeps running.
rem
rem RUN THIS INSTEAD OF CLOSING THE RDP WINDOW. Must run elevated (as admin).
rem If plain tscon is denied, a SYSTEM-level scheduled task retries it --
rem the standard workaround on hosts where even admins can't call tscon.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo This script must run as Administrator.
    echo Right-click the file - "Run as administrator", or start an elevated cmd.
    pause
    exit /b 1
)

for /f "skip=1 tokens=3" %%s in ('query user %USERNAME%') do set SESSIONID=%%s
echo Session: %SESSIONNAME% (id %SESSIONID%)
echo.
echo [1/3] Trying: tscon %SESSIONID% /dest:console
tscon %SESSIONID% /dest:console
rem success = the RDP connection drops and nothing below ever runs
echo     failed (error %errorlevel%).
echo.
echo [2/3] Trying by session name: tscon %SESSIONNAME% /dest:console
tscon %SESSIONNAME% /dest:console
echo     failed (error %errorlevel%).
echo.
echo [3/3] Trying as SYSTEM via a one-shot scheduled task...
schtasks /create /tn _tscon_redirect /tr "tscon %SESSIONID% /dest:console" ^
    /sc once /st 00:00 /ru SYSTEM /f >nul
schtasks /run /tn _tscon_redirect >nul
timeout /t 5 /nobreak >nul
schtasks /delete /tn _tscon_redirect /f >nul 2>&1
rem if the SYSTEM task worked, this window is already gone

echo.
echo ============================================================
echo All tscon attempts failed. This VPS most likely has NO console
echo session to redirect to (common on cloud virtualization).
echo.
echo Your options, in order of preference:
echo  1. Launch IB Gateway from your provider's web/VNC console
echo     (that session IS the console - closing the browser is safe).
echo  2. Rely on the watchdog: make sure gateway_watchdog.bat is
echo     scheduled every 5 minutes with IBC installed. Gateway will
echo     be relaunched and logged in within ~5 minutes after every
echo     disconnect. Bracket orders rest on IBKR's servers, so open
echo     positions stay protected while Gateway is down.
echo     Check with:  schtasks /query /tn GatewayWatchdog
echo ============================================================
pause
