# Stream Dog user guide

The Windows x64 **0.2.1 public beta** bundles a Rust management app, Node core and independent Rust device adapters. No Python installation or StreamDock app is required.

## Installation and updates

1. Install the official [Stream Deck app](https://www.elgato.com/downloads).
2. Download the installer and checksum from the [0.2.1 release](https://github.com/restarea92/hellgato/releases/tag/v0.2.1), finish active work, then run setup. The installer is unsigned.
3. Setup automatically closes Hellgato and Stream Deck. It removes verified Hellgato-owned legacy Python workers and runtime files from the registered installation while preserving settings, profiles and plugins. Stream Deck reopens afterward if it was running; the desktop apps launch without installer elevation.
4. Open Stream Dog, select the matching adapter, discover the USB device and connect it. Close other software using the same USB device, including REA Bridge.
5. If Stream Deck does not find it, add a Network Device at `127.0.0.1:5343` and confirm pairing. Additional devices use their own connection details.

Use **Prepare Stream Deck** when startup support needs installation or repair. Hellgato checks ownership and will not overwrite another product's startup DLL. Resolve that conflict through the owning product. Untested host versions warn rather than being rejected solely by version or hash; native layout changes still depend on compatible executable structures.

Automatic updating is not enabled. Install a later beta over the existing installation. A real installed 0.2.1 upgrade and unelevated restart have been verified; a complete uninstall cycle has not. Older releases remain available for rollback.

## Everyday use

The sidebar lists devices and connection state. Select a device to see its model, virtual serial, connection controls and stream statistics. Stream Deck owns action assignment, profiles and plugins. The GUI currently uses Korean labels.

X and Alt+F4 hide the window to the tray and keep the connection alive. Click the tray icon or launch Stream Dog again to reopen it. **Quit** from the tray or File menu stops the core and releases devices. `--background` starts hidden; `--stop` requests shutdown.

Each device retains its own virtual identity, session and canvas. The core composes keys, the continuous touch strip and screensavers before submitting frames. Unchanged screens submit no frames; active output is capped at 60 fps. USB submission time excludes plugin execution and physical LCD scanout. The management app has no live preview.

## Device backups

Select a paired device and open **Backups**. Save to the default location or choose an external destination folder.

Default snapshots live in `%LOCALAPPDATA%/Hellgato/stream-dog-rust/backups/<virtual-serial>`. The app can rename, manually reorder and delete them individually. Deleting a snapshot does not delete current Stream Deck data. External backups are not listed in the app.

A snapshot includes the selected device's profiles and Hellgato identity/settings, plus shared Stream Deck plugins, icons, assets and settings. Shared resources are not exclusive to that device. Linked development plugins are copied as regular files. Other devices' profiles, existing backup archives, logs, dumps and bundled Node files are excluded. `backup.json` records files and SHA-256 digests.

Close Stream Deck before taking a consistent snapshot. This is a backup folder, not a Stream Deck profile-import file. **Automatic restore is not implemented**; retain an untouched copy before any manual recovery.

## Additional adapters

Adapter Management lists installed adapters and their installation state. Choose **Add .hga**, review the external-code warning and bundled DLL inventory, then confirm registration. Discovery executes the adapter.

An `.hga` is a ZIP package containing an adapter executable, dependencies and a hashed manifest. Inventory checks establish integrity, not malware safety. External adapters can execute native code; packed or obfuscated DLLs cannot be established safe by these checks. Only install packages from sources you trust. See the [adapter guide](adapters/frame-adapter-guide.md) to develop one.

## Compatibility and limitations

| Model | USB / reference firmware | Output | Verification |
| --- | --- | --- | --- |
| N4 Pro E | `5548:1021` / `V4.N4 Pro E.02.010` | Complete 800×480 USB frame | Display, controls, screensaver restoration and knob brightness confirmed on hardware |
| N4 / TS10N4 | `0400:3001`, revision 2 / `V3.TS10N4.01.006` | 800×480 canvas split into volatile key/touch regions | Simulation only |
| 293S / TS115 | `0400:1000`, revision `0300` / `V2.293S.00.003` | 532×262 canvas split into 15 keys and 3 display regions | Simulation only |

- The tested host is **Stream Deck 7.6.0.23012 / Qt 6.11.1**. Other host builds and device firmware are allowed with warnings. Optional native UI attachment can fail without stopping the base core. Private automatic rescan only runs on verified host structures.
- Startup support changes Stream Deck process memory on launch, including when USB is unplugged. It leaves `StreamDeck.exe` unchanged on disk. A genuine **Stream Deck + cannot be used simultaneously** with this shared layout patch.
- N4 Pro supports independent knob brightness. RGB colors/effects, vibration, physical dial release/hold/press-and-turn and standby/boot-logo uploads are not implemented.
- N4 touch uses four known region centers; 293S release events are synthesized. Its side display regions are not separate actions. Both compatibility adapters still need physical verification; their output is not guaranteed to reach 60 fps.
- Independent sessions and recovery passed simulation. Multiple physical devices, simultaneous host recognition and reconnect after initial manual pairing remain unverified. Mirabox currently allows one connected device per model family.
- Streaming does not flash firmware or repeatedly upload persistent device backgrounds.

## Troubleshooting and reports

Check adapter installation state, USB ownership and the Stream Deck connection status first. An untested-version warning is a compatibility notice, not proof of a failed connection. Reopen the window from the tray before starting another copy.

Diagnostics are `frame-events.jsonl` and `last-session.json` under `%LOCALAPPDATA%/Hellgato/stream-dog-rust`; additional sessions use `devices/<session-id>`. **Do not share `control.json` or `native-ui.json`: they contain authentication tokens.** Redact personal paths and profile content from reports.

[Report an issue](https://github.com/restarea92/hellgato/issues) with app version, model, VID/PID, firmware, Stream Deck version and the exact failing action. Use the short [hardware checklist](compatibility/hardware-testing.md) for a new model. Development commands live in the [Rust app README](https://github.com/restarea92/hellgato/blob/main/apps/stream-dog/rust/README.md).
