# Orbix on an Android phone — the full guide

This runs the **real app** on your phone: FastAPI, DuckDB, the scheduler, the paper ledger. Not a snapshot, not a remote website. Your data stays on the device and the server listens only on `127.0.0.1`, so nothing is reachable from your Wi-Fi or the internet.

There is **no `.apk`**. Android cannot run Python directly, and DuckDB/NumPy only publish glibc ARM64 wheels, so plain Termux cannot install them. The app therefore runs inside a small Debian container managed by Termux, and you start it from a home-screen button instead of an app icon.

---

## Quick version (if you already know Termux)

```bash
pkg update
curl -fsSL https://raw.githubusercontent.com/Aizennn7/orbix/main/scripts/termux-setup.sh -o termux-setup.sh
bash termux-setup.sh
~/orbix-start.sh
```

If the script is not on `main` yet, use the branch URL:

```bash
curl -fsSL https://raw.githubusercontent.com/Aizennn7/orbix/arena/8f21adda-orbix/scripts/termux-setup.sh -o termux-setup.sh
```

Everything below explains what those lines do, how to make it survive daily use, and what to do when something goes wrong.

---

## Before you start

| Requirement | Why |
|---|---|
| **ARM64 phone**, 4 GB RAM or more | DuckDB and NumPy need the ARM64 builds |
| **~1.5 GB free storage** | Debian container + Python packages + your data |
| **Android 9 or newer** | Termux and proot-distro expectations |
| **Wi-Fi or mobile data for setup** | About 250 MB of downloads |

Check your architecture in Termux any time with `uname -m`. You want `aarch64`.

You do **not** need root, a developer account, or a computer — except optionally for the Android 12+ fix in [Part 4](#part-4--stop-android-from-killing-it).

---

## Part 1 — Install Termux

1. Open **F-Droid**: <https://f-droid.org/packages/com.termux/>
2. Install it.

**Use F-Droid, not the Play Store.** The Play Store build is deprecated and cannot install packages properly, which will make this guide fail at the first command.

Open Termux and run:

```bash
pkg update
```

Answer `y` when it asks. This refreshes the package index and typically takes one to three minutes. If it warns about a mirror, just run it again.

---

## Part 2 — Install Orbix

Still in Termux:

```bash
curl -fsSL https://raw.githubusercontent.com/Aizennn7/orbix/main/scripts/termux-setup.sh -o termux-setup.sh
bash termux-setup.sh
```

If `main` does not have the script yet, use
`https://raw.githubusercontent.com/Aizennn7/orbix/arena/8f21adda-orbix/scripts/termux-setup.sh`.

### What the installer does

1. Installs `proot-distro` and creates a Debian container (~200 MB).
2. Inside Debian, installs `git`, `python3`, `python3-venv`, and `curl`.
3. Clones Orbix and installs its Python dependencies — DuckDB, NumPy, FastAPI, uvicorn.
4. **Runs a real DuckDB query** to prove the native build works on your phone.
5. Writes `/root/orbix-serve.sh`, the server launcher inside the container.
6. Writes `~/orbix-start.sh`, `~/orbix-stop.sh`, and two `~/.shortcuts/` entries for Termux:Widget.

Ten minutes is normal on a phone. Progress is printed as it goes.

### If it stops early

Just run `bash termux-setup.sh` again. Each step is idempotent: the container is reused, the repo is updated rather than re-cloned, and the virtualenv is reused. Your data directory is never touched.

---

## Part 3 — Start it

```bash
~/orbix-start.sh
```

The script:

- takes a wake lock so Android does not freeze the server,
- starts the container,
- waits until `http://127.0.0.1:8000/healthz` answers,
- opens Chrome at the dashboard.

First start takes 15–30 seconds. Later starts are usually under 5.

To stop:

```bash
~/orbix-stop.sh
```

To check whether it is running:

```bash
curl -s http://127.0.0.1:8000/healthz
```

`{"status":"ok"}` means it is up. Nothing at all means it is down.

### Run it in the background at boot?

Not automatically. Android blocks most reliable auto-start, and the wake lock needs Termux alive. In practice you tap the widget after a reboot — that is one tap and it is reliable.

---

## Part 4 — Stop Android from killing it

Android is aggressive about background apps. Three things matter, in order:

### 4.1 Battery optimization (do this, it is free)

Settings → Apps → Termux → **Battery** → **Unrestricted**.

The exact path varies: on Samsung it is *App battery usage*; on Xiaomi it is *Battery saver → No restrictions*; on stock Android it is *Unrestricted*. Find the option that means "never restrict this app".

### 4.2 Wake lock (already automatic)

`orbix-start.sh` runs `termux-wake-lock`, which shows a permanent notification and stops Android from freezing Termux. `orbix-stop.sh` releases it.

### 4.3 The phantom process killer (Android 12+)

Android 12 and newer kill child processes once an app passes roughly 32 of them. A Linux container spawns more than that, so the container can vanish mid-run — often as `Process completed (signal 9)` in Termux.

This needs a computer with ADB **once**:

1. On the phone: Settings → About phone → tap *Build number* seven times → Developer options appear.
2. Enable **USB debugging**.
3. On a computer, install Android platform-tools, connect the phone, and run `adb devices` (accept the prompt on the phone).
4. Then:

```bash
# Android 13 and newer (also 12L)
adb shell settings put global settings_enable_monitor_phantom_procs false

# Android 12 exactly — run in this order
adb shell "/system/bin/device_config set_sync_disabled_for_tests persistent"
adb shell "/system/bin/device_config put activity_manager max_phantom_processes 2147483647"
```

On Android 14 and newer, some builds also expose a Developer options toggle named **Disable child process restrictions** — check there first, it avoids ADB entirely.

The setting survives reboots. A factory reset or a major OS update can clear it.

**No computer?** Skip this and rely on 4.1 and 4.2. Many phones work for days without trouble; if the dashboard disappears while you are not looking, this is the cause.

---

## Part 5 — Make it feel like an app

### Home-screen launcher

1. Install **Termux:Widget** from F-Droid.
2. Long-press an empty spot on your home screen → **Widgets**.
3. Find **Termux:Widget** and drag it out.
4. Tap it and choose `orbix-start`.

You now have a one-tap button. `orbix-stop` is in the same list.

### Add the dashboard itself

With Orbix running, open `http://127.0.0.1:8000` in Chrome → **⋮** → **Add to Home screen**.

That icon opens the dashboard full-screen, with no browser address bar. It only works while the server is running, so start the widget first if it does not load.

### A note on Chrome and localhost

`127.0.0.1` is treated as a secure origin, so nothing breaks here. Chrome will not ask for a certificate or complain about HTTP.

---

## Part 6 — What actually works on the phone

Everything the app does on a laptop works here. A short tour:

| Area | What to expect |
|---|---|
| **Dashboard** | Same UI. Without a provider configured it shows **SYNTHETIC DEMO** data and says so in a banner. |
| **Market data** | Add a Twelve Data key under *Connect providers*, or import a CSV. |
| **CSV import** | Chrome's file picker works. Pick a file from Downloads; a year of M1 data is ~20 MB. |
| **Signal engine** | Runs locally. Produces BUY / SELL / NO SIGNAL on closed candles. |
| **Walk-forward test** | Works, but a long history takes minutes on a phone CPU. Keep the screen on. |
| **Scheduler** | Runs while the server runs. If the phone sleeps or Termux is killed, signals pause until you start it again. |
| **Telegram** | Works with a bot token and chat id. Sends only while the server is up. |
| **Pocket Option** | Still a **manual** copy-ticket handoff. No broker connection exists. |

Nothing here changes the app's nature: it is signal-only, it never places orders, and it makes no accuracy promises.

---

## Part 7 — Everyday commands

```bash
~/orbix-start.sh          # start (safe to run twice)
~/orbix-stop.sh           # stop
curl -s localhost:8000/healthz   # is it up?
tail -f ~/orbix.log       # live server log
```

Look inside the container:

```bash
proot-distro login debian
du -sh /root/orbix /root/orbix-data
exit
```

Restart cleanly:

```bash
~/orbix-stop.sh && ~/orbix-start.sh
```

---

## Part 8 — Updating

```bash
~/orbix-stop.sh
bash termux-setup.sh
~/orbix-start.sh
```

The installer runs `git fetch` + `checkout` inside the container and reinstalls dependencies if they changed. Your data in `/root/orbix-data` is untouched.

Updates are not automatic. Nothing pulls behind your back.

---

## Part 9 — Backup and restore

Your entire state is one folder: `/root/orbix-data` (DuckDB file, ledger, provider keys, models).

From Termux:

```bash
termux-setup-storage                 # one-time: grant storage access
cp -r $PREFIX/var/lib/proot-distro/installed-rootfs/debian/root/orbix-data \
      ~/storage/shared/orbix-data-backup
```

That puts a copy in your phone's normal storage, where you can move it to a computer or cloud drive.

Restore:

```bash
~/orbix-stop.sh
cp -r ~/storage/shared/orbix-data-backup \
      $PREFIX/var/lib/proot-distro/installed-rootfs/debian/root/orbix-data
~/orbix-start.sh
```

If the path ever looks wrong, confirm it from inside the container:

```bash
proot-distro login debian -- ls -la /root/orbix-data
```

---

## Part 10 — Optional: password and LAN access

By default the server binds to `127.0.0.1`, so only the phone can reach it. That is the safest setting and needs no password.

If you want to open the dashboard from a laptop on the same Wi-Fi, **set a password first** — otherwise anyone on that network can read it.

```bash
proot-distro login debian
apt-get install -y nano
nano /root/orbix-serve.sh
```

Change the last line to bind all interfaces, and add the credentials above it:

```bash
export ORBIX_DASHBOARD_USER=orbix
export ORBIX_DASHBOARD_PASSWORD='choose-something-long'

exec /root/orbix/.venv/bin/python -m uvicorn orbix.api:app \
  --host 0.0.0.0 --port 8000 --workers 1 --no-server-header
```

Then restart and browse to `http://<phone-ip>:8000`. Find the IP in Settings → Wi-Fi.

Only do this on a network you trust. Revert to `127.0.0.1` when you are done.

---

## Part 11 — Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `This script must run inside Termux` | You ran it inside Debian or on a PC | `exit` back to Termux, run it there |
| `pkg: command not found`, updates fail | Play Store Termux | Uninstall, install from F-Droid |
| `pkg install` errors mid-way | Stale mirror | `pkg update`, then retry |
| Installer stops at pip | Network dropped, or no space | Check `df -h`, retry — it resumes |
| `Orbix did not answer within 90 seconds` | Container died or port blocked | `tail -30 ~/orbix.log` |
| `signal 9` / `Process completed` | Phantom process killer | [Part 4.3](#43-the-phantom-process-killer-android-12) |
| Dashboard loads, then dies after minutes | Battery optimization | [Part 4.1](#41-battery-optimization-do-this-it-is-free) |
| `Address already in use` | Old server still running | `~/orbix-stop.sh`, retry |
| Dashboard shows **SYNTHETIC DEMO** | No feed configured — expected | Add a Twelve Data key or import a CSV |
| Page loads but nothing updates | Server not running | Run `~/orbix-start.sh` |
| Chrome says "site can't be reached" | Server stopped, or wrong port | `curl -s localhost:8000/healthz` |

The log is your best friend: `tail -50 ~/orbix.log`.

---

## Part 12 — Uninstall

```bash
~/orbix-stop.sh
proot-distro remove debian
rm -f ~/orbix-start.sh ~/orbix-stop.sh ~/orbix.log
rm -rf ~/.shortcuts/orbix-start ~/.shortcuts/orbix-stop
```

That deletes the container and **all data inside it**. Copy `/root/orbix-data` out first if you want to keep it (Part 9).

---

## FAQ

**Why not an APK?**
Packaging CPython plus a C++ analytical database for Android means cross-compiling DuckDB with the NDK and shipping a 300 MB+ app. It is weeks of fragile work versus a working setup today, and it would break every time DuckDB changes.

**Does it trade for me?**
No. It never places broker orders. Pocket Option remains a manual copy-ticket handoff, and `order_execution` is off by design.

**Does it need internet?**
Only for market data, news, and Telegram. With no keys it runs entirely offline on synthetic demo data or an imported CSV.

**Will it drain my battery?**
It is a Python server doing light work between M1 candles, but the wake lock keeps Termux from being frozen, so expect some impact. Stopping it with `~/orbix-stop.sh` when you are done is the real fix.

**Can I run it on an iPhone?**
No. This depends on Termux and a Linux container, neither of which exists on iOS.

**Is my data sent anywhere?**
No, unless you configure a provider or Telegram yourself. The server listens on loopback only.

**What happens if I lose the phone?**
The data goes with it. Back it up (Part 9) if it matters.

**Is this the same app as on GitHub Pages?**
No. Pages hosts a frozen read-only snapshot. This is the live application with a real database and scheduler.

---

## Limits, stated plainly

- The phone must be awake and Termux alive for the scheduler to fire. It is not a 24/7 server.
- One worker only: DuckDB and SQLite are single-process stores.
- Long backtests are slower here than on a laptop.
- Nothing about this makes the signal engine more accurate. It is a research tool on different hardware.
