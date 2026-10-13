# Hellgato Stream Dog guide

[Back to the README](../README.md)

## Everyday use

- **Connection:** open Stream Deck yourself, connect one N4 Pro over USB and open Hellgato Stream Dog. Startup support prepares the ten-key model during every Stream Deck launch, including launches without USB hardware. Complete Stream Deck's pairing confirmation when prompted. Closing Stream Deck stops the bridge and waits for your next Stream Deck launch. Removing and reconnecting USB reopens the USB handles while the Node bridge stays running. The control changes between Connect, Cancel and Disconnect to match the current state.
- **File menu:** export this device's profiles into one `.hellgatoProfiles` bundle (`Ctrl+E`), or import a bundle or several `.streamDeckProfile` files (`Ctrl+I`). Close Stream Deck yourself before importing. Imports back up replaced profiles; open Stream Deck again yourself afterward to resume a previously active Hellgato connection.
- **Tray:** closing the window keeps Hellgato running. Choose Open or Exit from its tray menu.
- **Startup:** the installer's finish page offers separate checkboxes to launch Hellgato Stream Dog now and at Windows sign-in. Stream Deck keeps its own startup setting. During normal use, Hellgato Stream Dog waits for you to open Stream Deck and does not launch, close or restart it.
- **Prepare Stream Deck:** when startup support needs installation or an update, close Stream Deck from its tray menu, then choose this action in Hellgato Stream Dog. Windows may request administrator permission. Open Stream Deck yourself after preparation finishes.
- **Settings → Preferences:** choose the interface language. Changes apply immediately and persist across restarts. First launch uses the Windows display language, with English as fallback.

The main window lists remembered devices by name, assigned serial and connection status. The assigned `HGMOCK...` serial identifies the virtual Stream Deck device. Connection status follows live USB events; remembered devices stay listed as **Disconnected** when unavailable. Only one registered N4 Pro can be active at a time.

Profile tasks display a separate progress or result notification that can be dismissed when complete. Menus support keyboard navigation with `Alt+F`, `Alt+S` and `Alt+H`, arrow keys, Enter and Escape. The title bar uses dark Windows styling where supported.

Select the virtual N4 Pro in Stream Deck's device preferences to find **Knob brightness** below its standard brightness control. It uses the same control appearance and interaction as display brightness. Dragging updates the knobs immediately; the final independent value is saved per virtual device when the adjustment finishes. Sleep and wake still dim and restore the knobs with the display transition. The control appears only for the configured virtual N4 Pro and is disabled while its authenticated local connection or physical device is unavailable.

Stream Deck account files are not included, and the automatic app-switching field is reset. Action settings are retained and may contain personal paths or credentials; review a profile bundle before sharing it. Export asks for confirmation after you choose a save location; Cancel leaves the destination unchanged and creates no bundle. After transferring profiles, configure app switching and check action paths on the destination PC.

## Compatibility and limitations

Hellgato targets **Stream Deck 7.5.1 and later** on Windows x64. Startup, USB reconnection and manual Stream Deck relaunch were verified with **7.6.0.23012**; **7.5.1** was tested with an earlier beta. See the [Stream Deck version table](../README.md#stream-deck-compatibility) for the test scope and release dates. Compatibility checks inspect Stream Deck's internal structure during preparation and its live device geometry before connecting. Passing these checks alone does not establish hardware usability.

Hardware testing used a **Mirabox N4 Pro E** running stock firmware **`V4.N4 Pro E.02.010`**. The bridge targets one connected N4 Pro at a time; see the [device and firmware table](../README.md#supported-devices) for the test scope.

The installer requires administrator permission to install into `%ProgramFiles%\Hellgato`. It displays an app-closing notice, closes running Stream Deck and Hellgato processes, and removes the verified previous installation before installing the new client. Settings and profiles are retained. Its finish page offers separate choices to launch Hellgato Stream Dog immediately and at Windows sign-in. Stream Deck is opened by the user after installation.

Setup prepares `version.dll`, `HellgatoStartup.plan` and an ownership manifest, `HellgatoStartup.json`, beside `StreamDeck.exe`. The startup DLL loads during every Stream Deck launch, verifies the executable SHA-256 and expected code structure, then prepares the ten-key model and touch strip feedback before the model is published to devices. It runs whether N4 Pro is plugged in or not. The full executable remains unchanged on disk.

The plan is specific to the inspected Stream Deck executable. After an app update changes its SHA-256, close Stream Deck yourself and choose **Prepare Stream Deck** in Hellgato Stream Dog, or use the source command below. Rerunning the installer also refreshes startup support and closes the running apps as part of setup. A hash or structure mismatch fails closed instead of applying stale patches. The ten-key layout changes Stream Deck's shared Plus model; simultaneous use with a genuine Stream Deck + is unverified.

Screen update requests enable 16 ms feedback checks for that composer; after two seconds without requests, it returns to Stream Deck's original idle wait.

N4 Pro does not report dial release events, so holds, release-triggered actions, press-duration detection, momentary actions, hold-to-repeat, press-and-turn and overlapping held dial presses cannot be reproduced accurately. This limit concerns dial buttons, not LCD keys or touch-strip holds.

Brightness, idle timeout, full LCD images, full-screen and individual-key color fills are forwarded to hardware. Configure screensavers in Stream Deck as usual: Hellgato reads the selected original from Stream Deck's settings and plays cached full-screen frames through the N4 SDK. Animated WebP/GIF frame timing is preserved without sending separate images for every key. The SDK streams from the PC; this does not store the animation in device firmware.

Sleep without a screensaver uses N4 Pro's native screen-off command. With a screensaver, the image appears first and dims over 0.3 seconds to the LCD's minimum brightness, while knob lighting turns off immediately. Wake restores action images while dim/off, then fades back to the Stream Deck brightness and restores the selected knob brightness. Display animations do not reset the idle timer. Sleep timing is stored per virtual device and returned to Stream Deck after reconnection. Manual Sleep is recognized early when a recent key release is followed by timeout-disable and brightness-fade commands; when Stream Deck sends only brightness changes, screen-off waits for its zero-brightness command.

Hardware checks confirmed the 5×2 layout when Stream Deck starts without USB hardware, operation of both rightmost keys after connection, USB reconnection while the virtual network device stays connected, and reconnection after manually quitting and reopening Stream Deck. Hellgato leaves a manually closed Stream Deck closed. Native knob brightness controls were also verified. Earlier beta checks covered five-minute idle activation, screensaver dimming and wake behavior.

Automated registry tests cover persistence across restarts, physical-serial matching after USB port changes, USB-path fallback and protection against reusing an assigned identity for another device. Physical identity recording and LCD button images were also verified after the Program Files migration. The image worker uses the writable user-data directory for SDK temporary images.

Touch alignment across profiles and plugins, extended animation playback and Windows reboot recovery need broader hardware testing. Report issues with the device, Stream Deck version, reproduction steps and relevant logs. Check logs for personal paths or profile information before sharing them.

Screensavers and boot/standby logos are separate features:

| Feature | Storage | Display behavior |
| --- | --- | --- |
| Idle screensaver | PC, in Stream Deck's saved assets | Hellgato streams the selected static image or animation while connected |
| Boot/standby logo | N4 Pro device | One static image appears after the boot video and when no app is connected |

Mirabox StreamDock accepts 800×480 JPG/PNG images for the stored logo. Hellgato does not write, upload or reset the device-stored logo; the persistent protocol and on-device image encoding still need verification. The SDK's ordinary background-image and animation APIs update the active display; their format does not establish the stored logo's format. The boot video is a separate existing device animation.

Elgato firmware updates do not apply to N4 Pro. Update controls and firmware-label actions are disabled for the selected virtual N4 Pro; genuine Elgato controls keep their normal behavior. The CORA bridge also rejects unsupported write operations before a success acknowledgement, logs the unsupported operation and closes that connection without a success reply. Firmware-version and discovery reads continue to work. The virtual device's reported compatibility version is separate from the N4 Pro's actual firmware.

Closing Stream Deck stops the USB and CORA workers; Hellgato waits for the next user-initiated Stream Deck launch. Exiting or disconnecting Hellgato stops its workers. USB removal alone closes the USB handles and waits to reopen the device while preserving the Node CORA process. Pending hardware controls are cancelled during USB teardown, the heartbeat wait is interrupted, and the device is notified after display writers stop and before reader teardown when the transport is still available. Native disconnect and transport errors are logged. The last LCD image can remain until the device returns to its stored logo; hardware transition timing still needs measurement.

## How it works

The design goal is Stream Deck's native user experience with the hardware's native performance. Keep configuration, actions and profiles in the official app, and use the device SDK for execution wherever it avoids unnecessary conversion or transport. Input and output may take different paths: forward input to Stream Deck, while displays can use its saved assets directly. Preserve the user's selected content, timing and behavior when choosing an output path.

Hellgato presents N4 Pro as a network-connected Stream Deck. The official app runs actions and plugins; the Mirabox SDK handles USB input and display output.

<p align="center">
  <img src="visuals/hellgato-network.gif" alt="N4 Pro exchanges input and display updates with the Python USB worker, Node.js CORA bridge and Stream Deck app." width="800">
</p>

Blue carries input toward Stream Deck; orange carries display updates toward N4 Pro. The virtual device is the identity exposed by CORA. The Python worker sends input over `stdin` and reads display events and images from local files. CORA uses `127.0.0.1:5343` for pairing and `127.0.0.1:5344` for device traffic. Screensavers use the original asset saved by Stream Deck, bypassing its per-frame display transport.

The device preferences extension uses Stream Deck's loaded public Qt APIs and matches the selected virtual device's exact serial. It sends authenticated requests to a local control endpoint; the USB worker applies them on its own thread. Each connection refreshes the endpoint token without adding duplicate preference rows. If the expected Qt layout or serial cannot be verified, the extension leaves the host UI unchanged. See [the native extension](../native/hellgato-ui) for its build and integration checks.

DisplayMirror combines repeated updates to each key or touch region within a batch. The default frame mode sends only the changed touch-strip region after its initial frame.

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
| `%LOCALAPPDATA%\Hellgato\traces` and `images` | Local device events and received display images |
| `%LOCALAPPDATA%\Hellgato\profile-import-backups` | Profiles replaced during import |
| Stream Deck installation directory: `version.dll`, `HellgatoStartup.plan`, `HellgatoStartup.json` | Startup DLL, executable-specific plan and ownership manifest |

Version 0.1.0 moves the application from `%LOCALAPPDATA%\Programs\Hellgato` to `%ProgramFiles%\Hellgato`. Run the installer as the same Windows user to update; setup removes the verified previous installation automatically. Per-user data remains under `%LOCALAPPDATA%\Hellgato`; settings, device identity and Stream Deck profiles are retained.

The device registry keeps the existing assigned identity from `settings.json` and binds it to the physical device. A device with a hardware serial keeps that binding after reconnection, restarting the client or changing USB ports. Devices without a hardware serial use a USB-path fingerprint, so recognition after a port change is not guaranteed. Another physical device cannot reuse an already assigned virtual identity. Saved records do not enable simultaneous multi-device operation.

Use **Help → Open logs** to access the data folder, or **Help → About Hellgato Stream Dog** for version and license information. Uninstallation runs startup cleanup before removing the bundled helper. Cleanup removes only startup files whose ownership and hashes match `HellgatoStartup.json`; it preserves foreign or modified files and leaves `StreamDeck.exe` unchanged. If cleanup fails, uninstallation stops and retains the helper; close Stream Deck yourself, check the logs and retry. Uninstalling keeps settings and backups. Existing device identities and profile formats remain compatible.

## Build and test

Use Python 3.13 x64, Git, Visual Studio C++ Build Tools with the x64 MSVC tools and Windows SDK, and Inno Setup 6.7.3. Both native DLLs are built from source. The device preferences extension uses a pinned Qt 6.5.3 MSVC x64 development kit during compilation. It uses the Qt and C++ runtimes already loaded by Stream Deck; another Qt runtime is not bundled. In PowerShell, from the repository root:

```powershell
python -m venv work/build-venv
./work/build-venv/Scripts/python.exe -m pip install -r installer/requirements-build.txt
./work/build-venv/Scripts/python.exe scripts/prepare-windows.py
./work/build-runtime/node.exe scripts/prepare-cora.mjs
./work/build-venv/Scripts/python.exe native/hellgato-ui/prepare.py
./work/build-venv/Scripts/python.exe native/hellgato-ui/build.py --output work/build-native-ui-package
./work/build-venv/Scripts/python.exe native/hellgato-startup/build.py

./work/build-venv/Scripts/python.exe -m unittest discover -s test -p 'test_*.py'
./work/build-runtime/node.exe --test test/*.test.mjs
```

Before the first source run, close Stream Deck yourself and prepare startup support:

```powershell
./work/build-venv/Scripts/python.exe scripts/setup-startup.py --install --elevate
./work/build-venv/Scripts/python.exe scripts/setup-startup.py --check
```

Then open Stream Deck yourself and start Hellgato Stream Dog:

```powershell
$env:HELLGATO_NODE = (Resolve-Path ./work/build-runtime/node.exe).Path
./work/build-venv/Scripts/python.exe app/hellgato.py
```

The native startup build produces `work/build-native-startup/version.dll`; the installer bundles it as `_internal/native/version.dll` along with `scripts/setup-startup.py` and the Python compatibility modules. Setup defaults to `%ProgramFiles%\Elgato\StreamDeck\StreamDeck.exe`; use `--exe` for a different installation. `--check` reads the current preparation state. Repeat `--install --elevate` after a Stream Deck update changes the executable hash. `--remove --elevate` removes only verified Hellgato-owned startup files. Elevation is requested only when the target directory requires it. None of these commands launches, closes or restarts Stream Deck.

`scripts/start-streamdeck.py` remains a legacy developer diagnostic. Its explicit `--restart` option can still restart Stream Deck; the normal Hellgato session does not use it. `--check-only` inspects compatibility and `--require-closed` checks the profile-import prerequisite without stopping the app. Incompatible structure returns exit code 20.

Build the installer:

```powershell
./scripts/build-windows.ps1 -Python ./work/build-venv/Scripts/python.exe -Iscc 'C:\Path\To\Inno Setup 6\ISCC.exe'
```

Build inputs are pinned and verified against upstream source metadata and checksums. Outputs are `dist/Hellgato-0.1.1-Setup.exe` and its SHA-256 sidecar. Personal profiles and logs are excluded. The Windows release workflow runs manually or when `docs/releases/0.1.1.md`, `.github/workflows/windows-release.yml` or files under `native/` change on `main`. Update the version, installer filenames, release-note trigger and workflow release command together before publishing release notes. Other pushes run checks without publishing.

| Directory | Purpose |
| --- | --- |
| `app/` | Windows launcher, interface and localization |
| `app/n4/` | Hardware input, display, profiles and compatibility |
| `app/cora/` | Stream Deck network transport |
| `app/locales/` | Interface translations |
| `native/hellgato-ui/` | Public Qt device preference extension and isolated test host |
| `native/hellgato-startup/` | Startup DLL that validates and prepares the model before publication |
| `scripts/` | Dependency preparation, startup setup, diagnostics and builds |
| `installer/` | Packaging and third-party notices |
| `test/` | Compatibility, profiles, UI state and CORA checks |

## Contributing

Open an issue or submit a pull request with a focused change. Run both test suites and add tests for changed behavior. Include the hardware and Stream Deck version for physical tests. Do not commit personal profiles, logs or generated build output.

### npm bridge packages

`hellgato` re-exports `@hellgato/core`. Both are built from `app/cora/bridge.mjs`, which the Windows app also uses. See [the core API](../packages/core) for usage and supported scope.

With the pinned Node 24 runtime prepared, build and pack from the repository root:

```powershell
./work/build-runtime/node.exe scripts/build-npm.mjs
npm pack ./packages/core --pack-destination ./work
npm pack ./packages/hellgato --pack-destination ./work
```

Keep both package versions and the facade's exact core dependency in sync. The root package stays private. Packages include the prepared DeckBridge runtime and its license, so consumers need no Git checkout or build step. Publish stable tarballs with the `latest` tag; npm publication is manual and is independent of the Windows installer release.

### Translations

The interface supports English, Korean, Spanish, Japanese, French, German, Russian, Italian, Brazilian Portuguese, Simplified Chinese and Traditional Chinese. Initial translations are open to native-speaker review.

1. Open the appropriate file in [`app/locales`](../app/locales). `en.json` is the source catalog.
2. Edit the text values, keeping message keys and placeholders such as `{count}` and `{filename}` unchanged. Use concise, neutral product language and consistent action names. Keep product names unchanged.
3. Preview the language in Hellgato and submit a pull request. You can use GitHub's file editor without setting up a local build.

Translation checks validate JSON, duplicate keys, missing/extra messages and placeholders. If you cannot submit a PR, use the [translation feedback form](https://github.com/restarea92/hellgato/issues/new?template=translation.yml).

The catalogs use standard flat JSON, compatible with translation platforms such as Weblate. No paid translation service or API key is required to run or build Hellgato.
