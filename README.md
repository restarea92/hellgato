<p align="center">
  <img src="app/assets/icons/hellgato-master.png" alt="Hellgato controller" width="160">
</p>

<h1 align="center">Hellgato</h1>

<p align="center">
  <strong>Unofficial hardware. Official software.</strong>
</p>

<p align="center">
  <a href="https://github.com/restarea92/hellgato/releases/download/v0.1.1/Hellgato-0.1.1-Setup.exe"><strong>Download for Windows (.exe)</strong></a> ·
  <a href="docs/guide.md">Guide &amp; development</a> ·
  <a href="https://github.com/restarea92/hellgato/issues">Report an issue</a>
</p>

Hellgato is an open-source project that brings third-party hardware into the official Elgato Stream Deck app. Its Windows client, **Hellgato Stream Dog**, connects your hardware while you configure actions, profiles and plugins in Stream Deck.

<p align="center">
  <img src="app/assets/icons/stream-dog-preview.png" alt="Hellgato Stream Dog pixel-art mascot holding a controller" width="128">
</p>

<p align="center">
  <img src="docs/images/app-window.png" alt="Hellgato connected to a Mirabox N4 Pro, alongside Stream Deck and the Photoshop plugin" width="960">
  <br><sub>Example setup captured with an earlier beta.</sub>
</p>

## Why Hellgato?

Use the official Stream Deck app with your Mirabox hardware without keeping StreamDock installed. In the Windows setup shown below, StreamDock's reported installation size was 1.38 GB, compared with 711 MB for Stream Deck.

| Application | Version | Windows-reported installed size |
| --- | --- | --- |
| StreamDock | 3.10.202.0512 | 1.38 GB |
| Elgato Stream Deck | 7.6.0.23012 | 711 MB |

<p>
  <img src="docs/images/streamdock-installed-size.png" alt="Windows installed-app entry for StreamDock 3.10.202.0512 showing 1.38 GB" width="430">
  <img src="docs/images/stream-deck-installed-size.png" alt="Windows installed-app entry for Elgato Stream Deck 7.6.0.23012 showing 711 MB" width="430">
</p>

These are installation estimates for the shown versions. Hellgato and additional plugins require extra disk space; runtime CPU and RAM usage have not been benchmarked.

## Stream Deck compatibility

Targets the specifically checked Stream Deck builds below on Windows x64. A higher version number alone does not establish compatibility. Startup, USB reconnection and manual Stream Deck relaunch were verified with **7.6.0.23012**. Compatibility is checked before preparing startup support and before connecting.

| Stream Deck version | Release date | Test status |
| --- | --- | --- |
| 7.5.1 | [2026-07-28](https://help.elgato.com/hc/en-us/articles/49238316077713-Elgato-Stream-Deck-7-5-1-Release-Notes) | Earlier beta tested |
| 7.6.0.23012 | [2026-09-22](https://help.elgato.com/hc/en-us/articles/51156609278737-Elgato-Stream-Deck-7-6-Release-Notes) | Startup, USB reconnection and manual relaunch verified |

## Supported devices

Targets one registered N4 Pro at a time. Simultaneous use of multiple devices is unsupported. Hardware testing used the device and stock firmware below.

| Device | Tested firmware | Controls | Platform | Status |
| --- | --- | --- | --- | --- |
| Mirabox N4 Pro E | `V4.N4 Pro E.02.010` | 10 LCD keys, 4 dials, touch strip | Windows x64 | Startup, reconnection and native knob brightness verified |

**Version:** `0.1.1`. Other devices and firmware versions have not been validated.

## Get started

1. Install the official [Stream Deck app](https://www.elgato.com/downloads).
2. Run the [Hellgato Stream Dog installer](https://github.com/restarea92/hellgato/releases/download/v0.1.1/Hellgato-0.1.1-Setup.exe) and approve administrator permission. Setup announces that it will close Stream Deck and Hellgato, closes them, removes the verified previous installation and installs into `%ProgramFiles%\Hellgato`.
3. On the finish page, choose whether to launch Hellgato Stream Dog now and whether to launch it at Windows sign-in.
4. Open Stream Deck yourself, connect your N4 Pro over USB and open Hellgato Stream Dog if it is not already running.
5. In Stream Deck, open **Network** and connect to `127.0.0.1:5343` on first use.

Complete any pairing confirmation in Stream Deck when prompted.

The installer bundles the runtimes and USB SDK; no source code, build tools or StreamDock app are required. Updating from an earlier installation retains your settings and profiles. Install any extra Stream Deck plugins separately.

The installer adds a startup DLL beside Stream Deck once. Each Stream Deck launch prepares the ten-key model before it becomes available to devices, even when N4 Pro is unplugged. Hellgato waits for Stream Deck to be opened by you; closing Stream Deck stops the bridge until your next launch. Reconnecting USB restores the hardware connection while keeping the virtual device connection intact.

If preparation is needed after installation or a Stream Deck app update, close Stream Deck yourself and choose **Prepare Stream Deck** in Hellgato Stream Dog. A changed executable hash requires a new plan; unsupported internal structures are rejected. See the [guide](docs/guide.md#build-and-test) for source setup and diagnostics.

## Features

- Configure actions, profiles, brightness and sleep timing in Stream Deck.
- See remembered devices with their names, assigned Stream Deck serials and live USB connection status. Disconnected devices remain in the list; their physical identities are saved for future connections.
- Adjust knob brightness below the display brightness control in Stream Deck's device settings. Drag to change it immediately; the independent value is saved when the adjustment finishes. Sleep turns knob lighting off and wake restores it.
- Set screensavers in Stream Deck; Hellgato plays the saved originals through the N4 SDK. Sleep turns the screen off when no screensaver is set, or dims the screensaver and turns off knob lighting. Wake restores the display and brightness.
- Export profiles as a `.hellgatoProfiles` bundle (`Ctrl+E`); import bundles or `.streamDeckProfile` files (`Ctrl+I`) after closing Stream Deck. Replaced profiles are backed up.
- Elgato firmware updates are hidden for the virtual N4 Pro, and unsupported firmware writes are rejected by the bridge.
- Tray operation, optional Windows sign-in startup and eleven interface languages.

### Planned N4 Pro controls

- Knob RGB colors: all four together or individually, and default-effect reset.
- Vibration on/off.
- Existing boot video on/off.
- Upload and reset the device-stored boot/standby logo.

## Known limitations

The installer is unsigned.

Ten-key and touch-strip support adjust Stream Deck process memory at startup. The full `StreamDeck.exe` stays unchanged on disk; installation adds `version.dll`, `HellgatoStartup.plan` and the `HellgatoStartup.json` ownership manifest beside it. Uninstallation removes only files whose ownership and contents match that manifest. The ten-key model changes Stream Deck's shared Plus model, so simultaneous use with a genuine Stream Deck + is unsupported, including when the N4 is unplugged but the startup shim remains installed.

N4 Pro does not report dial release events. Dial holds, actions triggered on release, press-duration detection, momentary actions, hold-to-repeat, press-and-turn, and overlapping held dial presses cannot be reproduced accurately with stock firmware.

N4 Pro stores a single static boot/standby logo on the device, separate from PC-stored screensavers. It appears after the boot video and when no app is connected. Hellgato does not write, upload or reset this logo. Stream Deck's **Advanced → Standby image** setting remains unsupported and may disconnect the virtual device.

Touch alignment across plugins, extended use and reboot recovery need broader hardware testing. [Report issues](https://github.com/restarea92/hellgato/issues) with your device, Stream Deck version and reproduction steps. Review logs and exported profiles for personal information before sharing.

## Documentation and contributing

See the [guide](docs/guide.md) for everyday use, local data, builds and contributing, or the [Node bridge API](packages/core) for device integrations. Bug fixes, hardware reports and [translation feedback](https://github.com/restarea92/hellgato/issues/new?template=translation.yml) are welcome.

## SDK architecture candidate

Development now separates the reusable core, explicit adapter/session SDK, and
Stream Dog product. Windows builds consume verified npm tarballs offline; npm
publication is temporarily disabled while versioning, changelogs and package
validation continue. Existing Windows release approval gates remain in force.

The new Python sidecar is experimental. The installed product retains its existing
N4 compatibility engine until candidate-specific hardware parity is recorded.
See the [architecture and staged rollout](docs/architecture/0001-device-neutral-sdk.md),
[adapter guide](docs/adapters/contributing.md), and
[evidence requirements](docs/compatibility/architecture-candidate.md).

## License

[MIT](LICENSE). Bundled dependencies keep their own licenses; see [third-party notices](installer/THIRD-PARTY-NOTICES.txt).

Hellgato is an independent project, not affiliated with or endorsed by Elgato or Mirabox.
