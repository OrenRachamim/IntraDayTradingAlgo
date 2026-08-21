@echo off
rem Safe "close RDP" for VPSes where IB Gateway dies instantly on disconnect
rem (Java display-teardown crash). Redirects this session to the physical
rem console so the desktop keeps a live display; the RDP viewer closes as a
rem side effect and Gateway keeps running.
rem
rem RUN THIS INSTEAD OF CLOSING THE RDP WINDOW. Must run elevated (as admin).

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo This script must run as Administrator.
    echo Right-click the file - "Run as administrator", or start an elevated cmd.
    pause
    exit /b 1
)

for /f "skip=1 tokens=3" %%s in ('query user %USERNAME%') do set SESSIONID=%%s
echo Redirecting session %SESSIONNAME% (id %SESSIONID%) to console...
tscon %SESSIONID% /dest:console
rem If tscon succeeds the RDP connection drops immediately and this line
rem is never reached from the RDP side.
echo tscon failed - check the session id above and run: tscon ^<id^> /dest:console
pause
