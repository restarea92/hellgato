# Hellgato Stream Dog guide

[Back to the README](../README.md)

## Installation and updates

Follow the [quickstart](../README.md#get-started) to install and pair the Windows client.

The installer is unsigned. It requires administrator permission to install into `%ProgramFiles%\Hellgato`. It displays an app-closing notice, closes running Stream Deck and Hellgato processes, and removes the verified previous installation before installing the new client. Settings and profiles are retained. Its finish page offers separate choices to launch Hellgato Stream Dog immediately and at Windows sign-in. Stream Deck is opened by the user after installation.

The installer bundles the runtimes and USB SDK; no source code, build tools or StreamDock app are required. Install any extra Stream Deck plugins separately. Check the [compatibility and limitations](#compatibility-and-limitations) before installing, including the conflict with genuine Stream Deck + hardware.

If startup support needs preparation after installation or a Stream Deck update, close Stream Deck yourself and choose **Prepare Stream Deck** in Hellgato Stream Dog. A changed executable hash requires a new plan; unsupported internal structures are rejected. See [Build and test](#build-and-test) for source setup and diagnostics.

### Stream Deck without StreamDock

Hellgato lets you use the official Stream Deck app with Mirabox hardware without keeping StreamDock installed. In the Windows setup shown below, StreamDock's reported installation size was 1.38 GB, compared with 711 MB for Stream Deck.

| Application | Version | Windows-reported installed size |
| --- | --- | --- |
| StreamDock | 3.10.202.0512 | 1.38 GB |
| Elgato Stream Deck | 7.6.0.23012 | 711 MB |

<p>
  <img src="images/streamdock-installed-size.png" alt="Windows installed-app entry for StreamDock 3.10.202.0512 showing 1.38 GB" width="430">
  <img src="images/stream-deck-installed-size.png" alt="Windows installed-app entry for Elgato Stream Deck 7.6.0.23012 showing 711 MB" width="430">
</p>

These are installation estimates for the shown versions. Hellgato and additional plugins require extra disk space; runtime CPU and RAM usage have not been benchmarked.

## Everyday use

Configure actions, profiles, display brightness and sleep timing in Stream Deck. Hellgato adds hardware connection, profile transfer, tray operation and eleven interface languages; knob brightness and screensaver behavior are described below.

- **Connection:** open Stream Deck yourself, connect one N4 Pro over USB and open Hellgato Stream Dog. Startup support prepares the ten-key model during every Stream Deck launch, including launches without USB hardware. Complete Stream Deck's pairing confirmation when prompted. Once connected, Hellgato keeps the network listener available during USB reconnection and Stream Deck restart. A new Stream Deck process must pass preparation checks before input and display forwarding resume. After the first manual pairing, restarting Hellgato while Stream Deck stays open automatically retries the network connection on verified Stream Deck 7.6.0.23012; other host builds keep manual pairing. The control changes between Connect, Cancel and Disconnect to match the current state.
- **File menu:** export this device's profiles into one `.hellgatoProfiles` bundle (`Ctrl+E`), or import a bundle or several `.streamDeckProfile` files (`Ctrl+I`). Close Stream Deck yourself before importing. Imports back up replaced profiles; open Stream Deck again yourself afterward to resume a previously active Hellgato connection.
- **Tray:** closing the window keeps Hellgato running. Choose Open or Exit from its tray menu.
- **Startup:** the installer's finish page offers separate checkboxes to launch Hellgato Stream Dog now and at Windows sign-in. Stream Deck keeps its own startup setting. During normal use, Hellgato Stream Dog waits for you to open Stream Deck and does not launch, close or restart it.
- **Prepare Stream Deck:** when startup support needs installation or an update, close Stream Deck from its tray menu, then choose this action in Hellgato Stream Dog. Windows may request administrator permission. Open Stream Deck yourself after preparation finishes.
- **Settings → Preferences:** choose the interface language. Changes apply immediately and persist across restarts. First launch uses the Windows display language, with English as fallback.

The main window lists remembered devices by name, assigned serial and connection status. The assigned `HGMOCK...` serial identifies the virtual Stream Deck device. Connection status follows live USB events; remembered devices stay listed as **Disconnected** when unavailable. Only one registered N4 Pro can be active at a time.

Profile tasks display a separate progress or result notification that can be dismissed when complete. Menus support keyboard navigation with `Alt+F`, `Alt+S` and `Alt+H`, arrow keys, Enter and Escape. The title bar uses dark Windows styling where supported.

Select the virtual N4 Pro in Stream Deck's device preferences to find **Knob brightness** below its standard brightness control. It uses the same control appearance and interaction as display brightness. Dragging updates the knobs immediately; the final independent value is saved per virtual device when the adjustment finishes. Sleep and wake still dim and restore the knobs with the display transition. The control appears only for the configured virtual N4 Pro and is disabled while its authenticated local connection or physical device is unavailable.

Stream Deck account files are not included, and the automatic app-switching field is reset. Action settings are retained and may contain personal paths or credentials; review a profile bundle before sharing it. Export asks for confirmation after you choose a save location; Cancel leaves the destination unchanged and creates no bundle. After transferring profiles, configure app switching and check action paths on the destination PC.

### Planned N4 Pro controls

These controls are not implemented:

- Knob RGB colors: all four together or individually, and default-effect reset.
- Vibration on/off.
- Existing boot video on/off.
- Upload and reset the device-stored boot/standby logo.

## Compatibility and limitations

Hellgato targets the specifically checked Stream Deck builds below on Windows x64. A higher version number alone does not establish compatibility. Compatibility checks inspect Stream Deck's internal structure before preparing startup support and its live device geometry before connecting. Passing these checks alone does not establish hardware usability.

| Stream Deck version | Release date | Test status |
| --- | --- | --- |
| 7.5.1 | [2026-07-28](https://help.elgato.com/hc/en-us/articles/49238316077713-Elgato-Stream-Deck-7-5-1-Release-Notes) | Earlier beta tested |
| 7.6.0.23012 | [2026-09-22](https://help.elgato.com/hc/en-us/articles/51156609278737-Elgato-Stream-Deck-7-6-Release-Notes) | Startup, USB reconnection and manual relaunch verified |

Hardware testing used the device and stock firmware below. The bridge targets one registered N4 Pro at a time; simultaneous use of multiple devices is unsupported.

| Device | Tested firmware | Controls | Platform | Status |
| --- | --- | --- | --- | --- |
| Mirabox N4 Pro E | `V4.N4 Pro E.02.010` | 10 LCD keys, 4 dials, touch strip | Windows x64 | Startup, reconnection and native knob brightness verified |

Other devices and firmware versions have not been validated. [Version 0.1.3](releases/0.1.3.md) permits other valid N4 Pro firmware strings to attempt a connection, with model, capability and firmware-consistency checks still enforced. This remains experimental and does not establish compatibility with those firmware versions.

Setup prepares `version.dll`, `HellgatoStartup.plan` and an ownership manifest, `HellgatoStartup.json`, beside `StreamDeck.exe`. The startup DLL loads during every Stream Deck launch, verifies the executable SHA-256 and expected code structure, then prepares the ten-key model and touch strip feedback before the model is published to devices. It runs whether N4 Pro is plugged in or not. The full executable remains unchanged on disk.

The plan is specific to the inspected Stream Deck executable. After an app update changes its SHA-256, close Stream Deck yourself and choose **Prepare Stream Deck** in Hellgato Stream Dog, or use the source command below. Rerunning the installer also refreshes startup support and closes the running apps as part of setup. A hash or structure mismatch fails closed instead of applying stale patches. The ten-key layout changes Stream Deck's shared Plus model; simultaneous use with a genuine Stream Deck + is unsupported, including when N4 Pro is unplugged but startup support remains installed.

Screen update requests enable 16 ms feedback checks for that composer; after two seconds without requests, it returns to Stream Deck's original idle wait.

N4 Pro does not report dial release events, so holds, release-triggered actions, press-duration detection, momentary actions, hold-to-repeat, press-and-turn and overlapping held dial presses cannot be reproduced accurately. This limit concerns dial buttons, not LCD keys or touch-strip holds.

Brightness, idle timeout, full LCD images, full-screen and individual-key color fills are forwarded to hardware. Configure screensavers in Stream Deck as usual: Hellgato reads the selected original from Stream Deck's settings and plays cached full-screen frames through the N4 SDK. Animated WebP/GIF frame timing is preserved without sending separate images for every key. Streamed screensaver frames preserve normal button and touch-strip images so input can restore them. The SDK streams from the PC; this does not store the animation in device firmware.

Changing the selected screensaver keeps the USB worker and Stream Deck network connection open. Replacement animations are prepared in the background before the active frames are swapped. An unreadable selection uses Stream Deck's full-screen image feedback.

Sleep without a screensaver uses N4 Pro's native screen-off command. With a screensaver, the image appears first and dims over 0.3 seconds to the LCD's minimum brightness, while knob lighting turns off immediately. Wake restores action images while dim/off, then fades back to the Stream Deck brightness and restores the selected knob brightness. Display animations do not reset the idle timer. Sleep timing is stored per virtual device and returned to Stream Deck after reconnection. Manual Sleep is recognized early when a recent key release is followed by timeout-disable and brightness-fade commands; when Stream Deck sends only brightness changes, screen-off waits for its zero-brightness command.

Hardware checks of the earlier bridge confirmed the 5×2 layout when Stream Deck starts without USB hardware, both rightmost keys, USB reconnection, Stream Deck relaunch and native knob brightness. Installed IPC build `bb51866` passed user checks for initial key images, USB reconnection with automatic connection and visible icons, return to the standalone device screen on Stream Deck exit, automatic reconnection on Stream Deck relaunch, button/knob/touch actions, profile/page image changes, display/knob brightness and sleep/wake. Hellgato leaves a manually closed Stream Deck closed. These results do not establish the complete hardware matrix. Earlier beta checks covered five-minute idle activation, screensaver dimming and wake behavior.

Automated registry tests cover persistence across restarts, physical-serial matching after USB port changes, USB-path fallback and protection against reusing an assigned identity for another device. Physical identity recording and LCD button images were also verified after the Program Files migration. The image worker uses the writable user-data directory for SDK temporary images.

Touch alignment across profiles and plugins, extended animation playback and Windows reboot recovery need broader hardware testing. Report issues with the device, Stream Deck version, reproduction steps and relevant logs. Check logs for personal paths or profile information before sharing them.

Screensavers and boot/standby logos are separate features:

| Feature | Storage | Display behavior |
| --- | --- | --- |
| Idle screensaver | PC, in Stream Deck's saved assets | Hellgato streams the selected static image or animation while connected |
| Boot/standby logo | N4 Pro device | One static image appears after the boot video and when no app is connected |

Mirabox StreamDock accepts 800×480 JPG/PNG images for the stored logo. Hellgato does not write, upload or reset the device-stored logo; the persistent protocol and on-device image encoding still need verification. Stream Deck's **Advanced → Standby image** setting remains unsupported and may disconnect the virtual device. The SDK's ordinary background-image and animation APIs update the active display; their format does not establish the stored logo's format. The boot video is a separate existing device animation.

Elgato firmware updates do not apply to N4 Pro. Update controls and firmware-label actions are disabled for the selected virtual N4 Pro; genuine Elgato controls keep their normal behavior. The CORA bridge also rejects unsupported write operations before a success acknowledgement, logs the unsupported operation and closes that connection without a success reply. Firmware-version and discovery reads continue to work. The virtual device's reported compatibility version is separate from the N4 Pro's actual firmware.

Closing Stream Deck releases the USB device with its native close notification while leaving Hellgato's network listener available for the next user-initiated Stream Deck launch. Hellgato resumes USB use after the new Stream Deck process passes preparation checks. Exiting or disconnecting Hellgato stops its workers. USB removal closes the physical device handles while preserving the virtual network connection. Held inputs are released and are never replayed. Cached display state is restored when the USB worker reconnects. Return to the device's standalone screen on Stream Deck exit was physically confirmed in IPC build `bb51866`.

Quitting Hellgato can leave the last screen visible for tens of seconds before native standby. After the first manual pairing, Hellgato automatically requests a network rescan while both apps and USB are available. This private host integration is verified for Stream Deck 7.6.0.23012 with Qt 6.11.1; other builds keep manual pairing. It does not launch or close either app, or open a pairing window. Connection retries stop after an explicit Hellgato disconnect. A stalled primary-only handshake also receives a bounded protocol recovery attempt.

## How it works

The design goal is Stream Deck's native user experience with the hardware's native performance. Keep configuration, actions and profiles in the official app, and use the device SDK for execution wherever it avoids unnecessary conversion or transport. Input and output may take different paths: forward input to Stream Deck, while displays can use its saved assets directly. Preserve the user's selected content, timing and behavior when choosing an output path.

Hellgato presents N4 Pro as a network-connected Stream Deck. The official app runs actions and plugins; the Mirabox SDK handles USB input and display output.

<p align="center">
  <img src="visuals/hellgato-network.gif" alt="Conceptual overview of N4 Pro, the Python USB worker, Hellgato SDK session and Stream Deck app." width="800">
</p>

The animation is a conceptual overview: blue carries input toward Stream Deck, and orange carries display output toward N4 Pro. The virtual Stream Deck is the logical device identity exposed by CORA, not a separate process.

The desktop supervisor starts a Node SDK session and a separate Python USB worker. Normal display frames and controls pass through the SDK session to the worker over JSON-RPC 2.0 / NDJSON stdio; input events travel back toward Stream Deck. Saved screensaver originals are decoded and cached in the worker, which plays their frames locally through the Mirabox SDK. These paths share the hardware without sending each cached screensaver frame through Node. CORA uses `127.0.0.1:5343` for pairing and `127.0.0.1:5344` for device traffic. See the [SDK architecture](architecture/0001-device-neutral-sdk.md) for the code boundaries.

The device preferences extension uses Stream Deck's loaded public Qt APIs and matches the selected virtual device's exact serial. It sends authenticated requests to a local control endpoint; the USB worker applies them on its own thread. Each connection refreshes the endpoint token without adding duplicate preference rows. If the expected Qt layout or serial cannot be verified, the extension leaves the host UI unchanged. See [the native extension](../native/hellgato-ui) for its build and integration checks.

The SDK output queue combines replaceable updates to each display surface. The default frame mode sends only the changed touch-strip region after its initial frame. Full LCD writes finish with a hardware refresh after the key and touch surfaces have been submitted. Fully black LCD frames clear native key icons instead of installing opaque black key images, allowing subsequent action icons to appear after initialization.

## Local data

| Location | Contents |
| --- | --- |
| `%ProgramFiles%\Hellgato` | Installed Hellgato Stream Dog application |
| `%LOCALAPPDATA%\Hellgato\settings.json` | Persistent device identity |
| `%LOCALAPPDATA%\Hellgato\devices.json` | Remembered physical devices and their assigned virtual serials |
| `%LOCALAPPDATA%\Hellgato\preferences.json` | Interface language |
| `%LOCALAPPDATA%\Hellgato\display-HGMOCK*.json` | Per-device idle timeout |
| `%LOCALAPPDATA%\Hellgato\device-controls-HGMOCK*.json` | Per-device knob brightness and follow-display preference |
| `%LOCALAPPDATA%\Hellgato\native-ui.json` | Current local control endpoint, exact device serial and session authentication token |
| `%APPDATA%\Elgato\StreamDeck\Assets` | Screen saver originals saved by Stream Deck; the selection is in its per-device Windows registry settings |
| `%LOCALAPPDATA%\Hellgato\bridge.log` | Connection and error logs |
| `%LOCALAPPDATA%\Hellgato\setup.log` | Installer migration and startup-option logs |
| `%LOCALAPPDATA%\Hellgato\ipc-diagnostics.jsonl` | Current SDK connection/error diagnostics |
| `%LOCALAPPDATA%\Hellgato\traces` | Historical compatibility-engine traces |
| `%LOCALAPPDATA%\Hellgato\images` | Previously received display images (preserved) |
| `%LOCALAPPDATA%\Hellgato\profile-import-backups` | Profiles replaced during import |
| Stream Deck installation directory: `version.dll`, `HellgatoStartup.plan`, `HellgatoStartup.json` | Startup DLL, executable-specific plan and ownership manifest |

Version 0.1.0 moves the application from `%LOCALAPPDATA%\Programs\Hellgato` to `%ProgramFiles%\Hellgato`. Run the installer as the same Windows user to update; setup removes the verified previous installation automatically. Per-user data remains under `%LOCALAPPDATA%\Hellgato`; settings, device identity and Stream Deck profiles are retained.

The device registry keeps the existing assigned identity from `settings.json` and binds it to the physical device. A device with a hardware serial keeps that binding after reconnection, restarting the client or changing USB ports. Devices without a hardware serial use a USB-path fingerprint, so recognition after a port change is not guaranteed. Another physical device cannot reuse an already assigned virtual identity. Saved records do not enable simultaneous multi-device operation.

Product text logs (`bridge.log`, `startup-setup.log`, `setup.log`) retain at most two 2 MiB files each. IPC diagnostics retain two 256 KiB files. Writes rotate before exceeding the limit; oversized individual records are omitted. The launcher checks known log files at startup and every five minutes, removing inactive helper logs and rotated logs older than seven days or exceeding their per-file limit. The current bridge and IPC files are retained until their writers rotate them. Historical `traces/*-<numeric-run-id>.jsonl[.1]` files have a 4 MiB per-file, 16 MiB combined and seven-day retention limit; oldest eligible files are removed first. Files modified within ten minutes and locked files are skipped and retried, so these temporary exceptions can exceed the retention budget. Unknown files, links, profiles, settings and images are preserved. Native UI diagnostics overwrite a single current-state snapshot rather than appending. The current SDK engine does not create historical traces or image captures. Compatibility-engine traces also rotate at 4 MiB when explicitly run from source.

Use **Help → Open logs** to access the data folder, or **Help → About Hellgato Stream Dog** for version and license information. Uninstallation runs startup cleanup before removing the bundled helper. Cleanup removes only startup files whose ownership and hashes match `HellgatoStartup.json`; it preserves foreign or modified files and leaves `StreamDeck.exe` unchanged. If cleanup fails, uninstallation stops and retains the helper; close Stream Deck yourself, check the logs and retry. Uninstalling keeps settings and backups. Existing device identities and profile formats remain compatible.

## Build and test

Use Python 3.13.7 x64, Node 24.19.0, npm 11.9.0, pnpm 11.10.0, uv 0.12.23, Git, Visual Studio C++ Build Tools with the x64 MSVC tools and Windows SDK, and Inno Setup 6.7.3. Both native DLLs are built from source. The device preferences extension uses a pinned Qt 6.5.3 MSVC x64 development kit during compilation. It uses the Qt and C++ runtimes already loaded by Stream Deck; another Qt runtime is not bundled. In PowerShell, from the repository root:

```powershell
python -m pip install uv==0.12.23
uv sync --locked --no-install-workspace --python python
pnpm install --frozen-lockfile
pnpm run build:npm
pnpm run typecheck
pnpm test
uv run --frozen --no-sync python scripts/check-python.py
uv run --frozen --no-sync python -m unittest discover -s test -p 'test_*.py'
./.venv/Scripts/python.exe scripts/prepare-windows.py
./.venv/Scripts/python.exe host-integrations/streamdeck/windows/native/ui/prepare.py
./.venv/Scripts/python.exe host-integrations/streamdeck/windows/native/ui/build.py --output work/build-native-ui-package
./.venv/Scripts/python.exe host-integrations/streamdeck/windows/native/startup/build.py
```

Build the installer before running the source client. This also stages its required SDK runtime and separate Python USB worker:

```powershell
./scripts/build-windows.ps1 -Python ./.venv/Scripts/python.exe -Iscc 'C:\Path\To\Inno Setup 6\ISCC.exe'
```

Build inputs are pinned and verified against upstream source metadata and checksums. Outputs are `dist/Hellgato-<version>-Setup.exe` and its SHA-256 sidecar. The version comes only from `packages/windows-release/package.json`, through generated runtime and installer metadata. Personal profiles and logs are excluded. Ordinary pushes and PRs run checks and builds without publishing. [Version management](../.changeset/README.md) batches version and changelog updates into a reviewed PR; after an app version reaches main, a separate workflow prepares an installer draft. The owner publishes it manually. See [Windows release operations](windows-releases.md) for verification and publication. npm publication remains deferred.

Before the first source run, close Stream Deck yourself and prepare startup support:

```powershell
./.venv/Scripts/python.exe scripts/setup-startup.py --install --elevate
./.venv/Scripts/python.exe scripts/setup-startup.py --check
```

Then open Stream Deck yourself and start Hellgato Stream Dog:

```powershell
$env:HELLGATO_NODE = (Resolve-Path ./work/build-runtime/node.exe).Path
./.venv/Scripts/python.exe apps/stream-dog/python/stream-dog.py
```

The native startup build produces `work/build-native-startup/version.dll`; the installer bundles it as `_internal/native/version.dll` along with `scripts/setup-startup.py` and the Python compatibility modules. Setup defaults to `%ProgramFiles%\Elgato\StreamDeck\StreamDeck.exe`; use `--exe` for a different installation. `--check` reads the current preparation state. Repeat `--install --elevate` after a Stream Deck update changes the executable hash. `--remove --elevate` removes only verified Hellgato-owned startup files. Elevation is requested only when the target directory requires it. None of these commands launches, closes or restarts Stream Deck.

`scripts/start-streamdeck.py` remains a legacy developer diagnostic. Its explicit `--restart` option can still restart Stream Deck; the normal Hellgato session does not use it. `--check-only` inspects compatibility and `--require-closed` checks the profile-import prerequisite without stopping the app. Incompatible structure returns exit code 20.

| Directory | Purpose |
| --- | --- |
| `apps/stream-dog/` | Canonical Windows product, GUI and Node session runner |
| `adapters/mirabox-python/` | Bundled physical-only IPC worker |
| `host-integrations/streamdeck/windows/` | Canonical host inspection and native startup/Qt integration |
| `packages/core/src/` | Canonical Stream Deck bridge and contracts |
| `packages/hellgato/` | Session, sidecar, explicit bindings and conformance SDK |
| `app/` | Legacy entrypoint aliases and retained assets |
| `app/n4/` | Legacy physical engine and compatibility helpers |
| `app/cora/` | Compatibility entrypoint wrappers |
| `app/locales/` | Interface translations |
| `native/` | Compatibility build wrappers for the canonical host integration |
| `scripts/` | Dependency preparation, startup setup, diagnostics and builds |
| `installer/` | Packaging and third-party notices |
| `test/` | Compatibility, profiles, UI state and CORA checks |

## Contributing

Open an issue or submit a pull request with a focused change. Run both test suites and add tests for changed behavior. Include the hardware and Stream Deck version for physical tests. Do not commit personal profiles, logs or generated build output.

### npm bridge packages

`hellgato` composes explicit adapters and sessions around `@hellgato/core`. The canonical core source is `packages/core/src`; Stream Dog consumes the verified package artifacts. See [the core API](../packages/core) for usage and supported scope.

Use Node 24 and the exact pnpm version in the root `packageManager` field:

```sh
pnpm install --frozen-lockfile
pnpm run prepare:cora
pnpm run build:npm
pnpm run typecheck
pnpm test
node scripts/pack-npm.mjs --destination dist/npm
node scripts/verify-npm.mjs --artifacts dist/npm
```

Build before running tests or type checks: facade imports resolve the public core exports in generated `packages/core/dist`, which is absent in a clean checkout.

Changesets keeps the two npm versions fixed together. The source facade uses `workspace:*`; pnpm pack converts that to the exact core version inside the tarball. Both tarballs are inspected and installed into an isolated consumer for import and lifecycle checks. The root and Windows metadata workspaces stay private. Packages include the prepared DeckBridge runtime and license, so consumers need no Git checkout or build step.

Changesets manages the fixed SDK pair and the independent Windows version through the [version review workflow](../.changeset/README.md). Local Windows packaging consumes the verified SDK tarballs without publishing them; npm publication remains deferred.

### Translations

The interface supports English, Korean, Spanish, Japanese, French, German, Russian, Italian, Brazilian Portuguese, Simplified Chinese and Traditional Chinese. Initial translations are open to native-speaker review.

1. Open the appropriate file in [`app/locales`](../app/locales). `en.json` is the source catalog.
2. Edit the text values, keeping message keys and placeholders such as `{count}` and `{filename}` unchanged. Use concise, neutral product language and consistent action names. Keep product names unchanged.
3. Preview the language in Hellgato and submit a pull request. You can use GitHub's file editor without setting up a local build.

Translation checks validate JSON, duplicate keys, missing/extra messages and placeholders. If you cannot submit a PR, use the [translation feedback form](https://github.com/restarea92/hellgato/issues/new?template=translation.yml).

The catalogs use standard flat JSON, compatible with translation platforms such as Weblate. No paid translation service or API key is required to run or build Hellgato.


### Packed SDK staging

After installing the pinned Node/pnpm workspace dependencies, run
`pnpm run stage:windows-sdk` to prepare the source product's SDK runtime.
This builds, verifies and offline-installs both SDK tarballs into the generated
`work/stream-dog-sdk` product runtime. `scripts/build-windows.ps1` performs the same
step automatically. The old `app/cora/server.mjs` is now only a launcher; do not copy
core sources or substitute workspace symlinks into a distributed installer.

The ordinary desktop app runs the sidecar SDK and the separately packaged Mirabox
worker. Build and stage that worker with `scripts/build-windows.ps1` before running
the product. See the [architecture](architecture/0001-device-neutral-sdk.md) for
the boundary between host integration, SDK sessions and physical device code.
