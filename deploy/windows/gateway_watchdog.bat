@echo off
rem IB Gateway watchdog - schedule every 5 minutes ("Run only when user is
rem logged on"). If no Gateway java process is running, relaunch it via IBC
rem (IBC then handles the login automatically). Requires IBC installed - see
rem deploy\WINDOWS_VPS.md section 3.

set IBC_START=C:\IBC\StartGateway.bat

tasklist /FI "IMAGENAME eq java.exe" 2>nul | find /I "java.exe" >nul
if %errorlevel% equ 0 goto :alive
tasklist /FI "IMAGENAME eq javaw.exe" 2>nul | find /I "javaw.exe" >nul
if %errorlevel% equ 0 goto :alive

echo [%date% %time%] Gateway not running - restarting via IBC >> "%~dp0watchdog.log"
start "" "%IBC_START%"
exit /b 0

:alive
exit /b 0
