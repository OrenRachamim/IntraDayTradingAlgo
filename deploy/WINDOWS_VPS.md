# Running unattended on a Windows VPS (IB Gateway + engine survive RDP disconnect)

IB Gateway is a GUI app: it lives inside your Windows *logon session*. It dies
when that session ends — which happens when you **Sign out**, when a session
time-limit policy kills disconnected sessions, or when the VPS reboots.
Fix all three, in this order.

## 1. Disconnect the right way (immediate fix)

- ✅ Close RDP with the **X button** on the blue connection bar (or just close
  the RDP window). This *disconnects* but leaves your session logged in —
  Gateway and Python keep running.
- ❌ Never Start → **Sign out** — that terminates the session and everything in it.

## 2. Stop Windows from killing disconnected sessions (one-time)

Run `gpedit.msc` →
`Computer Configuration → Administrative Templates → Windows Components →
Remote Desktop Services → Remote Desktop Session Host → Session Time Limits`:

- **Set time limit for disconnected sessions** → Enabled → **Never**
- **Set time limit for active but idle sessions** → Disabled (or Never)

(Home editions without gpedit: set the registry keys under
`HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services`:
`MaxDisconnectionTime=0`, `MaxIdleTime=0`.)

Also in Power Options: set the machine to **never sleep** (High performance,
sleep = Never) — some VPS images ship with sleep enabled.

## 2b. Gateway dies INSTANTLY on disconnect (Java display teardown)

If Gateway closes the moment you disconnect — even with the session-limit
policies set to Never — you are hitting a different, well-known failure: on RDP
disconnect Windows tears down the session's virtual display, and the Gateway's
JVM crashes with it. Policies cannot fix this. Two-part fix:

**a) Never disconnect normally — use the tscon redirect instead.**
Run `deploy\windows\disconnect_keepalive.bat` **as administrator** from inside
the RDP session when you want to "close" it. It runs
`tscon <your-session-id> /dest:console`, which hands the session to the VPS's
physical console: the desktop keeps a real display, your RDP window drops on
its own, and Gateway keeps running. (Manual equivalent from an elevated cmd:
`query user` to find your session id, then `tscon <id> /dest:console`.)
**tscon failed?** The script now tries three routes in order: by session id,
by session name, and as SYSTEM through a one-shot scheduled task (on many
hosts tscon is denied even to admins but allowed to SYSTEM). If all three
fail, your VPS almost certainly has **no console session at all** — common on
cloud virtualization — and tscon can never work there. Two supported
fallbacks:

1. **Install your own VNC server** — the chosen route here; see section 2c.
2. **Provider console**: open your VPS provider's web/VNC console (control
   panel → "Console"/"noVNC"), log in there, and start IB Gateway from that
   session. It *is* the console session, so closing the browser tab is
   completely safe — no display teardown ever happens to it. RDP can still be
   used for everything except launching/keeping Gateway.
3. **Watchdog-only mode**: accept that Gateway dies on each RDP disconnect
   and let the watchdog (below) + IBC bring it back logged-in within ~5
   minutes. Bracket orders rest on IBKR's servers, so open positions stay
   protected during the gap; the engine reconnects by itself. In this mode
   the watchdog task is mandatory, not optional.

## 2c. The VNC route (for console-less VPSes)

A VNC *server* owns the desktop's virtual display itself: viewers merely look
at it. Closing the VNC viewer therefore never tears the display down, and the
Gateway JVM never sees the display change that kills it on RDP disconnect.

**Install (one command, elevated cmd):**

```bat
cd C:\Algo\IntraDayTradingAlgo\deploy\windows
setup_vnc.bat <vnc-password-max-8-chars> <your-home-ip>
```

The script downloads TightVNC, installs the **server only** as a Windows
service with password auth, and opens firewall port 5900 **restricted to your
home IP** — it refuses to run without both arguments, so VNC is never exposed
to the whole internet. If your home IP is dynamic, refresh the rule when it
changes:

```bat
netsh advfirewall firewall set rule name="TightVNC-restricted" new remoteip=<new-ip>
```

**Operating discipline from now on:**

- Connect with any VNC viewer (TightVNC Viewer, RealVNC, Remmina) to
  `<vps-ip>:5900`. **Launch IB Gateway / IBC from this VNC desktop.**
- Do everything Gateway-related through VNC. Closing the viewer is always safe.
- **RDP becomes emergency-only.** Logging in over RDP with the same user takes
  over the desktop session; the next RDP disconnect can kill Gateway once —
  the watchdog (2b) revives it within ~5 minutes, but don't make RDP a habit.
- Keep the watchdog task scheduled regardless: it also covers crashes, reboots
  and IBKR's own nightly restarts.

Extra hardening (optional): move VNC to a non-default port in TightVNC's
server config, and prefer your provider's cloud-firewall on top of Windows'.

**b) Watchdog — self-heal even if Gateway does die.**
Schedule `deploy\windows\gateway_watchdog.bat` every 5 minutes (edit the IBC
path inside it first; requires IBC from section 3 so the relaunch logs itself
in):

```bat
schtasks /Create /TN "GatewayWatchdog" /SC MINUTE /MO 5 ^
  /TR "C:\Algo\IntraDayTradingAlgo\deploy\windows\gateway_watchdog.bat"
```

With both in place, a dead Gateway is back and logged in within ~5 minutes,
and the engine's connection-retry loop (12 attempts, 10s apart) plus
`ensure_connected()` re-attach automatically. The bracket orders protecting
open positions live on IBKR's servers, so they stay active even while the
Gateway is down.

## 3. Survive reboots + the nightly/weekly Gateway restart (the real fix: IBC)

IB Gateway restarts itself nightly and logs out weekly (Sunday) by design —
without automation you must re-login by hand. **IBC** does it for you:

1. Download IBC: https://github.com/IbcAlpha/IBC/releases (zip for Windows).
2. Unzip to `C:\IBC`, edit `C:\IBC\config.ini`:
   ```ini
   IbLoginId=your_paper_username
   IbPassword=your_password
   TradingMode=paper          ; change to live only when you go live
   AcceptNonBrokerageAccountWarning=yes
   ```
   and set the Gateway path/version in `StartGateway.bat` per IBC's README.
3. Enable Windows **auto-logon** for your user (`netplwiz` → uncheck "Users
   must enter a user name and password"), so a session always exists after
   reboot. Use a strong password + restrict RDP by firewall/VPN — auto-logon
   trades some security for uptime.
4. Task Scheduler → Create Task:
   - Trigger: **At log on** (your user)
   - Action: `C:\IBC\StartGateway.bat`
   - "Run only when user is logged on" (GUI apps need the interactive session)

Result: VPS reboots → auto-logon → IBC starts Gateway and logs it in → your
scheduled engine connects. Nightly restarts are also re-logged-in by IBC.

## 4. Schedule the engine (Task Scheduler)

The engine waits for 10:00 ET by itself, so start it early; times below are in
the **VPS's local clock** — adjust to your VPS timezone (e.g. Central = ET-1).

```bat
schtasks /Create /TN "AlgoLive" /SC WEEKLY /D MON,TUE,WED,THU,FRI ^
  /ST 08:20 /TR "C:\Algo\IntraDayTradingAlgo\deploy\windows\run_live.bat"
schtasks /Create /TN "AlgoMaint" /SC WEEKLY /D SUN ^
  /ST 11:00 /TR "C:\Algo\IntraDayTradingAlgo\deploy\windows\run_maintenance.bat"
```

Edit paths inside the two .bat files first. For both tasks choose
"Run only when user is logged on" (the session always exists thanks to step 3).

## 5. Verify the setup

1. Start Gateway (or let IBC do it), run
   `python -m live.run_live --status` — should print equity.
2. Leave RDP via `disconnect_keepalive.bat` (as admin). Wait 10 minutes,
   reconnect — Gateway must still be running and logged in.
3. Kill Gateway manually (Task Manager), wait ~6 minutes — the watchdog must
   have relaunched it, logged in via IBC.
4. Reboot the VPS, don't touch anything for 5 minutes, reconnect —
   Gateway should be up and logged in via auto-logon + IBC.

## Alternative: no GUI at all

`gnzsnz/ib-gateway` (Docker) wraps Gateway+IBC headless. On a Windows VPS this
needs Docker Desktop/WSL2 — usually more moving parts than IBC, but the right
choice if you ever migrate to a Linux VPS (then: docker compose + our
`deploy/crontab.txt` and everything runs with no desktop session at all).
