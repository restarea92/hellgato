<p align="center">
  <img src="app/assets/icons/hellgato-master.png" alt="Hellgato controller" width="160">
</p>

<h1 align="center">Hellgato</h1>

<p align="center">
  <strong>Unofficial hardware. Official software.</strong>
</p>

<p align="center">
  <a href="https://github.com/restarea92/hellgato/releases/download/v0.0.6-beta/Hellgato-0.0.6-beta-Setup.exe"><strong>Download for Windows (.exe)</strong></a> ·
  <a href="docs/guide.md">Guide &amp; development</a> ·
  <a href="https://github.com/restarea92/hellgato/issues">Report an issue</a>
</p>

Hellgato is an open-source Windows bridge that brings third-party hardware into the official Elgato Stream Deck app. Configure your actions, profiles and plugins in Stream Deck; Hellgato connects them to your hardware.

**Plug in your hardware. Put Stream Deck to work.**

<p align="center">
  <img src="docs/images/app-window.png" alt="Hellgato connected to a Mirabox N4 Pro, alongside Stream Deck and the Photoshop plugin" width="960">
  <br><sub>Example setup captured with an earlier beta.</sub>
</p>

## Supported devices

| Device | Controls | Platform | Status |
| --- | --- | --- | --- |
| Mirabox N4 Pro (stock firmware) | 10 LCD keys, 4 dials, touch strip | Windows x64 | Supported beta device |

**Version:** `0.0.6-beta`. Currently supports one connected N4 Pro. Other devices have not been validated. Remaining hardware checks are listed below.

## Get started

1. Install the official [Stream Deck app](https://www.elgato.com/downloads).
2. Download and run the [Hellgato Windows installer](https://github.com/restarea92/hellgato/releases/download/v0.0.6-beta/Hellgato-0.0.6-beta-Setup.exe).
3. Connect your N4 Pro over USB and open Hellgato.
4. In Stream Deck, open **Network** and connect to `127.0.0.1:5343` on first use.

The installer bundles the runtimes and USB SDK; no source code, build tools or StreamDock app are required. Install any extra Stream Deck plugins separately.

## How it works

Hellgato presents your N4 Pro as a network-connected Stream Deck. The official app runs your actions and plugins and sends display updates back to the hardware.

<p align="center">
  <img src="docs/visuals/hellgato-network.gif" alt="Animated Hellgato architecture: N4 Pro exchanges input and display updates with a Python USB worker, a Node.js CORA bridge and the Stream Deck app. A translucent Virtual Stream Deck shows the identity recognized by the app." width="800">
</p>

**Blue** carries key, dial and touch input toward the app. **Orange** carries key images, touch-screen updates and brightness toward the device. The translucent **Virtual Stream Deck** represents the device identity exposed by CORA.

The Python USB worker combines the **Mirabox SDK** for hardware communication with **DisplayMirror** for screen updates. It sends input commands to the Node.js **CORA bridge** through `stdin` and reads returning images and events from JPEG files and JSONL. CORA communicates with Stream Deck over local TCP: `127.0.0.1:5343` for pairing and `127.0.0.1:5344` for input and display traffic.

DisplayMirror merges repeated updates to the same key or touch region within each batch. In the default frame mode, it sends only the updated touch-strip region after the initial frame, reducing redundant USB work.

An [interactive diagram and reusable 3D assets](docs/visuals/README.md) are also included.

## Everyday essentials

- **Your setup travels.** Export profiles as a `.hellgatoProfiles` bundle (`Ctrl+E`), or import bundles and `.streamDeckProfile` files (`Ctrl+I`). Imports back up replaced profiles and stop Stream Deck while applying them. An active Hellgato session resumes afterward.
- **Close the window. Keep the buttons.** Hellgato stays in the tray; use its menu to exit.
- **Make yourself at home.** Eleven interface languages, plus optional startup at Windows sign-in.

Need logs, backup locations or build instructions? They're in the [guide](docs/guide.md).

## Beta notes

The installer is unsigned. Compatibility has been validated with **Stream Deck 7.6.0.23012**; later releases may need updates.

Ten-key support and touch strip feedback scheduling temporarily adjust the running Stream Deck process's memory; the executable on disk stays unchanged. Using a genuine Stream Deck + at the same time is outside this beta's scope.

Dial clicks are forwarded as press/release pairs; dial holds are not supported. Stream Deck sleep and screen-fill commands do not yet change the hardware display. Touch alignment, sustained animations and reboot recovery still need further hardware testing.

Found an issue? [Let us know](https://github.com/restarea92/hellgato/issues) with your device, Stream Deck version and steps to reproduce it. Review logs and exported profiles for personal information before sharing.

## TODO

- [ ] Validate touch alignment and sustained animations across profiles and plugins.
- [ ] Verify Windows sign-in startup, USB reconnection and stability during extended use.
- [ ] Apply Stream Deck sleep and screen-fill commands to the hardware display.
- [ ] Expand testing across Stream Deck versions and additional hardware.
- [ ] Review interface translations with native speakers.

Longer term: explore a shared Rust core and macOS support, after the Windows beta is more settled.

## Contributing

Bug fixes, hardware reports and translation polish are welcome. See the [contributor guide](docs/guide.md#contributing), browse the [language files](app/locales), or send [translation feedback](https://github.com/restarea92/hellgato/issues/new?template=translation.yml).

For device developers, the [Node bridge API](packages/core) is available as `hellgato@beta` and `@hellgato/core@beta`. Both share the desktop app's bridge implementation; USB adapters and Windows compatibility adjustments remain separate.

## License

[MIT](LICENSE). Bundled dependencies keep their own licenses; see [third-party notices](installer/THIRD-PARTY-NOTICES.txt).

Hellgato is an independent project, not affiliated with or endorsed by Elgato or Mirabox.
