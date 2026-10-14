# Windows app handoff: 2026-10-14

Branch: `feat/device-neutral-sdk`, repository `restarea92/hellgato`.
The current scope is SDK separation and a usable desktop app. Automated
publication is deferred; do not resume release-coordinator work.

## Desktop integration

The ordinary launcher now starts the product IPC supervisor. It discovers the
registered N4 through the worker, preserves the saved virtual identity, starts
Node under a Windows process job, and connects the existing GUI and native knob
preferences to the separated SDK session. Vendor USB code runs only in the
bundled `HellgatoMiraboxWorker.exe`.

The installer includes Node, the offline-installed SDK, the frozen worker, and
the existing native UI/startup components. There is no separate TEST console
step for ordinary app use. Device absence is recoverable until the user stops
the session. Saved settings and profile transfer retain their product paths.

## Display fix

Product streamed LCD frames now use explicit `stream-screensaver` mode. They
preserve normal key/background/touch caches; input, wake and host clear restore
the latest images. Normal full-LCD output for other SDK consumers is unchanged.
The reconnect journal preserves normal frames across streamed overlays.
Temporary knob brightness previews do not become saved preferences during
unrelated host brightness updates.

The installed `7559023` IPC build showed black keys after USB reconnection,
Stream Deck restart and Hellgato restart. Profile changes did not recover them;
sleep followed by wake restored icons in every reported case. Full LCD writes
now finish with a refresh after all key and touch surfaces, including the extra
clear sent when the SDK opens a session. Previously only initialization and
wake restoration refreshed that composite. Focused sequence tests pass; the
replacement installer's physical result is still pending.

The product keeps its CORA listener and sockets through USB worker generations,
releases held inputs, and reapplies pending display output after fresh readiness.
The desktop launcher also keeps the listener alive while Stream Deck restarts,
so the host can dial its saved dock during startup. It revalidates the new host
before loading native controls. Hellgato itself restarting while Stream Deck is
already open can still require manual pairing; no automatic host restart is used.

Display diagnostics contain fixed per-surface counters and brightness/last-writer
summaries, never image bytes, raw identities, profile content or credentials.

## Build and checks

Use the pinned local Node 24.19.0, pnpm 11.10.0, npm 11.9.0, Python 3.13.7,
uv 0.12.23 and Inno Setup 6.7.3. `scripts/build-windows.ps1` builds the ordinary
installer with its worker. `-UsePreparedNative` reuses previously built native
DLLs; it still requires both files to exist.

Focused display/control regression tests pass, including byte comparisons with
the legacy key/touch writer. Product IPC tests cover discovery, saved identity,
native control preferences, process supervision, and absent/disconnected USB.
The native UI and startup DLLs were already built and exercised locally.
These checks do not claim physical display visibility.

## GitHub Actions

The automatic release PR and release coordinator workflows are removed. Basic
Windows checks/builds remain. The manual publication job is disabled. No npm
publication, GitHub release, merge or push was performed for this continuation.

## Local work

The implementation checkout is `work/device-neutral-sdk` under the original
checkout. Preserve the original checkout's untracked user files.
Before the app switch, both apps were closed by the user and 66 settings/profile
files were backed up to ignored `work/before-ipc-app-backup`. Keep that backup
private. Do not replace saved identities or another PC's profiles.

Ask the user to perform physical/manual checks directly. Start with key icons,
then button/knob/touch actions and native knob brightness, then profile change,
screen saver wake and a USB reconnect. Fix observed failures before claiming
app completion; automated release work is outside this scope.
