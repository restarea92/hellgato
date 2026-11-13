# Stream Dog Rust beta

The Windows x64 public beta installer is
`Hellgato-0.2.0-Setup.exe`. It bundles Node, Rust workers,
manufacturer DLLs and startup support for Stream Deck 7.6.0.23012 / Qt 6.11.1.
It contains no Python runtime. Untested host builds and firmware produce
compatibility warnings without blocking core connection. Optional native UI
loading failures also leave the core running. Startup patches remain bound to
their actual executable structure; an unprepared host may have a different key
layout. Private automatic rescan is enabled only where its host structure was verified.

## Installation and lifecycle

1. Finish active work and close Stream Deck yourself.
2. Run setup. It stops Hellgato and removes legacy Python executables and
   `_internal` only from the registered installation. Settings and profiles remain.
3. Choose whether to start in the tray at Windows sign-in, then open both apps.
4. Discover/select your device and connect. Initial Network Device pairing in
   Stream Deck may require manual addition.

Close REA Bridge before opening the same USB device. Foreign startup DLLs are
never overwritten; resolve conflicts through their owner's uninstall procedure.
This is a public beta. Automatic updating is not enabled.
A real installed upgrade/uninstall cycle has not yet been verified.

X and Alt+F4 hide the window while keeping connections alive. Click the tray icon
or launch the app again to reopen it. Quit from the tray or File menu to stop
the core. `--background` starts hidden; `--stop` requests orderly shutdown.

## Supported combinations and limits

| Model | USB / tested firmware | Output | Verification |
| --- | --- | --- | --- |
| N4 Pro E | `5548:1021` / `V4.N4 Pro E.02.010` | Whole-display USB frame | Display and controls confirmed on hardware |
| N4 / TS10N4 | `0400:3001`, revision 2 / `V3.TS10N4.01.006` | Internal key/touch-region splitting | Simulation only |
| 293S / TS115 | `0400:1000`, revision `0300` / `V2.293S.00.003` | Internal splitting into 15 keys and 3 display-only regions | Simulation only |

The N4 canvas is 800x480; 293S uses 532x262 and a standard MK.2 15-key host
target. Its three side regions are not separate actions. N4 touch positions use
four known region centers; 293S releases are synthesized pulses. Compatibility
output is serialized and does not guarantee 60 fps. Orientation, input, JPEG
limits and the shared transport DLL still need physical verification on both
beta models. No stored `LOG` background uploads or firmware updates are used.

Each device has its own session, virtual serial/MAC, ports, canvas and reconnect
state. Two simulated sessions and independent disconnection passed automated
checks. Multiple physical devices and simultaneous host recognition remain
unverified. The Mirabox ownership lock currently allows one device per model
family. Automatic reconnect after initial manual pairing also needs hardware
verification. Preserve existing profiles and paired identities during testing.

Report exact model/firmware, adapter installation status, discovery/connection,
orientation, all controls, animated images, folder navigation, screensaver
restoration and reconnect results. Logs are `frame-events.jsonl` and
`last-session.json` under
`%USERPROFILE%/AppData/Local/Hellgato/stream-dog-rust`, with additional devices
under `devices/<session-id>`. Do not share `control.json` or `native-ui.json`;
they contain authentication tokens.

See the [adapter development guide](../adapters/frame-adapter-guide.md).

## Device backups

Select a paired device, then open **Backups**. Save to the default location or
choose a separate destination folder. Default backups are stored below
`%LOCALAPPDATA%/Hellgato/stream-dog-rust/backups/<virtual-serial>` and can be
renamed, reordered or individually deleted in the app. Deleting a backup never
deletes current Stream Deck data. External backups are not added to this list.

Each snapshot contains the selected device's profiles, shared plugins, icons,
assets and settings, and that device's Hellgato settings/identity. Other devices'
profiles, old backup archives, logs, dumps and bundled Node files are excluded.
Linked development plugins are copied as regular files. `backup.json` records
the snapshot's files and SHA-256 digests. Close Stream Deck before taking a
consistent backup. The backup is a folder, not a Stream Deck profile-import file;
automatic restore is not included in this beta.
