<p align="center">
  <img src="app/assets/icons/hellgato-master.png" alt="Hellgato icon" width="160">
</p>

<h1 align="center">Hellgato</h1>

<p align="center">
  <strong>Unofficial hardware. Official software.</strong><br>
  A little mischief. A lot of buttons.
</p>

<p align="center">
  <a href="https://github.com/restarea92/hellgato/releases">Download</a> ·
  <a href="docs/guide.md">Guide &amp; development</a> ·
  <a href="https://github.com/restarea92/hellgato/issues">Report an issue</a>
</p>

Hellgato is an open-source Windows bridge that brings third-party hardware into the official Elgato Stream Deck app. Set up your actions, profiles and plugins in Stream Deck. Let Hellgato handle the introductions.

**Currently supported:** one stock Mirabox N4 Pro — ten LCD keys and four dials — on Windows x64. **Version:** `0.0.1-beta`.

## Plug in. Stir things up.

1. Install the official [Stream Deck app](https://www.elgato.com/downloads).
2. Grab `Hellgato-0.0.1-beta-Setup.exe` from [Releases](https://github.com/restarea92/hellgato/releases) and install it.
3. Connect your N4 Pro over USB and open Hellgato.
4. In Stream Deck, open **Network** and connect to `127.0.0.1:5343` on first use.

The installer bundles the runtimes and USB SDK. No StreamDock required. Install any extra Stream Deck plugins separately.

## Small app. Useful tricks.

- **Your setup travels.** Export profiles as a `.hellgatoProfiles` bundle (`Ctrl+E`), or import bundles and `.streamDeckProfile` files (`Ctrl+I`). Imports back up replaced profiles and restart Stream Deck.
- **Close the window. Keep the buttons.** Hellgato stays in the tray; use its menu to exit.
- **Make yourself at home.** Eleven interface languages, plus optional startup at Windows sign-in.

Need logs, backup locations or build instructions? They're in the [guide](docs/guide.md).

## A little beta, a little bite.

The installer is unsigned. Compatibility has been validated with **Stream Deck 7.6.0.23012**; later releases may need updates.

Ten-key support temporarily adjusts the running Stream Deck process's memory; the executable on disk stays unchanged. Using a genuine Stream Deck + at the same time is outside this beta's scope. Some dial, touch, animation and reboot behavior still needs hardware testing.

Found a gremlin? [Open an issue](https://github.com/restarea92/hellgato/issues) with your device, Stream Deck version and steps to reproduce it. Review logs and exported profiles for personal information before sharing.

## Bring something to the party.

Bug fixes, hardware reports and translation polish are welcome. See the [contributor guide](docs/guide.md#contributing), browse the [language files](app/locales), or send [translation feedback](https://github.com/restarea92/hellgato/issues/new?template=translation.yml).

## License

[MIT](LICENSE). Bundled dependencies keep their own licenses; see [third-party notices](installer/THIRD-PARTY-NOTICES.txt).

Hellgato is an independent project, not affiliated with or endorsed by Elgato or Mirabox.
