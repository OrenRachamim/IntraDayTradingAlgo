@echo off
setlocal
rem One-shot TightVNC *server* install for a console-less Windows VPS where
rem IB Gateway dies on RDP disconnect and tscon cannot help.
rem A VNC server owns the desktop's virtual display, so closing the VNC viewer
rem never tears the display down -- Gateway keeps running.
rem
rem Usage (elevated cmd):
rem   setup_vnc.bat <vnc-password-max-8-chars> <your-home-ip>
rem
rem Security: port 5900 is opened ONLY for <your-home-ip>. Never expose VNC
rem to the whole internet. If your home IP changes (dynamic IP), update the
rem firewall rule:  netsh advfirewall firewall set rule name="TightVNC-restricted" new remoteip=<new-ip>

rem TightVNC pins its MSI per version -- if this 404s, get the current link
rem from https://www.tightvnc.com/download.php and update the two lines below.
set VNC_VER=2.8.85
set VNC_URL=https://www.tightvnc.com/download/%VNC_VER%/tightvnc-%VNC_VER%-gpl-setup-64bit.msi

if "%~2"=="" (
    echo Usage: %~nx0 ^<vnc-password-max-8-chars^> ^<your-home-ip^>
    echo   example: %~nx0 Str0ngPw 84.110.25.7
    echo Refusing to install without a password AND a source IP restriction.
    exit /b 1
)
set VNC_PASS=%~1
set HOME_IP=%~2

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo This script must run as Administrator.
    exit /b 1
)

echo [1/4] Downloading TightVNC %VNC_VER%...
powershell -Command "Invoke-WebRequest -Uri '%VNC_URL%' -OutFile '%TEMP%\tightvnc.msi'"
if not exist "%TEMP%\tightvnc.msi" (
    echo Download failed. Fetch the MSI manually from tightvnc.com/download.php,
    echo save as %TEMP%\tightvnc.msi and rerun.
    exit /b 1
)

echo [2/4] Installing server-only, service mode, password auth...
msiexec /i "%TEMP%\tightvnc.msi" /quiet /norestart ADDLOCAL=Server ^
    SERVER_REGISTER_AS_SERVICE=1 SERVER_ADD_FIREWALL_EXCEPTION=0 ^
    SET_USEVNCAUTHENTICATION=1 VALUE_OF_USEVNCAUTHENTICATION=1 ^
    SET_PASSWORD=1 VALUE_OF_PASSWORD=%VNC_PASS% ^
    SET_ALLOWLOOPBACK=1 VALUE_OF_ALLOWLOOPBACK=1
if %errorlevel% neq 0 (
    echo msiexec failed with error %errorlevel%.
    exit /b 1
)

echo [3/4] Firewall: allow TCP 5900 from %HOME_IP% only...
netsh advfirewall firewall delete rule name="TightVNC-restricted" >nul 2>&1
netsh advfirewall firewall add rule name="TightVNC-restricted" dir=in action=allow ^
    protocol=TCP localport=5900 remoteip=%HOME_IP%

echo [4/4] Verifying the tvnserver service...
sc query tvnserver | find "RUNNING" >nul
if %errorlevel% neq 0 (
    net start tvnserver >nul 2>&1
    sc query tvnserver | find "RUNNING" >nul || (
        echo Service is not running - reboot once and check: sc query tvnserver
        exit /b 1
    )
)

echo.
echo ============================================================
echo Done. Connect from home with any VNC viewer to:  ^<vps-ip^>:5900
echo From now on:
echo   - launch IB Gateway (or IBC) from the VNC desktop
echo   - closing the VNC viewer is always safe
echo   - treat RDP as emergency-only: logging in over RDP with the
echo     same user takes over the desktop session, and the next RDP
echo     disconnect can kill Gateway once - the watchdog revives it.
echo ============================================================
