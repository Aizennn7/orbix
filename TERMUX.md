# Running Orbix on an Android phone

This runs the **real app** — FastAPI, DuckDB, the scheduler, the paper ledger — on your phone, not a snapshot. Your data stays on the device. It is reached at `http://127.0.0.1:8000` from the phone's own browser, so nothing is exposed to the internet.

You will not get an `.apk` file. Android cannot run Python natively, so the app runs inside a small Debian container managed by Termux, and you use it through a home-screen shortcut instead of an app icon.

## What you need

- An **ARM64 Android phone** (almost every phone since ~2016) with **4 GB RAM** or more.
- About **1.5 GB** of free storage.
- **Termux from F-Droid** — <https://f-droid.org/packages/com.termux/>. Do not use the Play Store build; it is outdated and cannot install packages.
- Optionally **Termux:Widget** from F-Droid, for a home-screen button.

## Step 1 — install Termux

Install from F-Droid, open it, and run once:

```bash
pkg update
```

Accept the prompts. This usually takes a couple of minutes.

## Step 2 — run the installer

In Termux:

```bash
curl -fsSL https://raw.githubusercontent.com/Aizennn7/orbix/main/scripts/termux-setup.sh -o termux-setup.sh
bash termux-setup.sh
```

If the script is not on `main` yet, fetch it from the branch instead:

```bash
curl -fsSL https://raw.githubusercontent.com/Aizennn7/orbix/arena/8f21adda-orbix/scripts/termux-setup.sh -o termux-setup.sh
bash termux-setup.sh
```

The installer:

1. installs `proot-distro` and creates a Debian container (~200 MB download),
2. installs Python, NumPy and DuckDB there — these are the ARM64 glibc builds, which is why Debian is used instead of Termux directly,
3. clones Orbix and installs its dependencies,
4. verifies DuckDB actually runs a query,
5. writes `~/orbix-start.sh`, `~/orbix-stop.sh`, and two home-screen shortcuts.

Ten minutes on a phone is normal. If it stops with an error, re-run it — it picks up where it left off.

## Step 3 — start it

```bash
~/orbix-start.sh
```

It starts the server, waits for it to answer, and opens Chrome at the dashboard. First start takes 15–30 seconds; later starts are faster.

To stop:

```bash
~/orbix-stop.sh
```

## Step 4 — add a home-screen button

1. Install **Termux:Widget** from F-Droid.
2. Long-press an empty spot on your home screen → **Widgets** → **Termux:Widget**.
3. Pick `orbix-start`. One tap now starts Orbix and opens it.

The same widget list also has `orbix-stop`.

## Add it to your home screen as a web app

With Orbix running, open `http://127.0.0.1:8000` in Chrome → ⋮ menu → **Add to Home screen**. You get an icon that opens the dashboard full-screen, which looks like a normal app.

## Keep Android from killing it

Android aggressively shuts down background work. Two things help:

1. **Battery optimization** — Settings → Apps → Termux → Battery → **Unrestricted**. On some phones this is under "App battery usage" or "Background activity".
2. **Wake lock** — already handled: `orbix-start.sh` runs `termux-wake-lock`, which keeps a persistent notification and stops Android from freezing Termux.

**Android 12 and newer** add a second mechanism called the *phantom process killer*: it kills child processes beyond a limit, which can drop the container even with a wake lock. The fix needs a one-time ADB command from a computer:

```bash
adb shell settings put global settings_enable_monitor_phantom_procs false
```

If you do not have ADB set up, most phones still work fine with a wake lock — just expect occasional restarts on memory pressure.

## Updating

```bash
~/orbix-stop.sh
bash termux-setup.sh
```

The installer pulls the latest code and reinstalls dependencies. Your data in `/root/orbix-data` is not touched.

## Where things live

| Item | Path |
|---|---|
| App source | `/root/orbix` inside Debian |
| Your data (DuckDB, ledger, keys) | `/root/orbix-data` inside Debian |
| Server log | `~/orbix.log` in Termux |
| Launchers | `~/orbix-start.sh`, `~/orbix-stop.sh` |

To look inside the container:

```bash
proot-distro login debian
```

## Troubleshooting

**"This script must run inside Termux."** — You ran it inside Debian, or on a computer. Exit the container (`exit`) and run it from Termux.

**`pkg install` fails** — Run `pkg update` and retry. A stale mirror usually causes this.

**"Orbix did not answer within 90 seconds."** — Read `~/orbix.log`. The usual causes are a failed `pip install` (re-run the installer) or the container being killed by Android (see battery settings above).

**The dashboard loads but data never changes** — That is expected without a data provider. Configure Twelve Data keys in the app, or import a CSV. The screen shows a banner saying which data source is active.

**Storage** — The container plus Python dependencies is roughly 1–1.5 GB. Remove it with `proot-distro remove debian` and delete `~/orbix-*.sh` when you no longer want it.

## What this does not change

The app is still signal-only. It never places broker orders, and Pocket Option remains a manual copy-ticket handoff. Running it on a phone does not make it a trading robot, and the scheduler only produces signals when a configured feed is present.
