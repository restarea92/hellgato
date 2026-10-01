# Hellgato guide

[Back to the README](../README.md)

## Everyday use

- **Connection:** the control changes between Connect, Cancel and Disconnect to match the current state. USB reconnects are handled automatically.
- **File menu:** export this device's profiles into one `.hellgatoProfiles` bundle (`Ctrl+E`), or import a bundle or several `.streamDeckProfile` files (`Ctrl+I`). Imports back up replaced profiles and restart Stream Deck.
- **Tray:** closing the window keeps Hellgato running. Choose Open or Exit from its tray menu.
- **Startup:** the installer offers Windows sign-in startup, enabled by default.
- **Settings → Preferences:** choose the interface language. Changes apply immediately and persist across restarts. First launch uses the Windows display language, with English as fallback.

The main window shows the device and connection status. Profile tasks display a separate progress or result notification that can be dismissed when complete. Menus support keyboard navigation with `Alt+F`, `Alt+S` and `Alt+H`, arrow keys, Enter and Escape. The title bar uses dark Windows styling where supported.

Stream Deck account files are not included, and the automatic app-switching field is reset. Action settings are retained and may contain personal paths or credentials; review a profile bundle before sharing it. After transferring profiles, configure app switching and check action paths on the destination PC.

## Compatibility and beta limits

Hellgato checks Stream Deck's internal structure and live device geometry before connecting. The current resolver has been validated on **Stream Deck 7.6.0.23012**; compatibility with every later release is not guaranteed.

Ten-key support and touch strip feedback scheduling require temporary changes to the running Stream Deck process's memory. Screen update requests enable 16 ms feedback checks for that composer; after two seconds without requests, it returns to Stream Deck's original idle wait. This improves animation delivery without polling continuously while idle. Internal code signatures and loaded bytes are checked before patching; unsupported layouts are rejected. Its executable on disk is unchanged. Simultaneous use with a genuine Stream Deck + is outside this beta's scope.

N4 Pro dial clicks are forwarded as press/release pairs because the device does not report a separate release. Dial holds are not supported. Touch alignment, sustained animation and Windows reboot recovery need further hardware validation. Report issues with the device, Stream Deck version, reproduction steps and relevant logs. Check logs for personal paths or profile information before sharing them.

## Local data

| Location | Contents |
| --- | --- |
| `%LOCALAPPDATA%\Programs\Hellgato` | Installed application |
| `%LOCALAPPDATA%\Hellgato\settings.json` | Persistent device identity |
| `%LOCALAPPDATA%\Hellgato\preferences.json` | Interface language |
| `%LOCALAPPDATA%\Hellgato\bridge.log` | Connection and error logs |
| `%LOCALAPPDATA%\Hellgato\traces` and `images` | Local device events and received display images |
| `%LOCALAPPDATA%\Hellgato\profile-backups` | Profiles saved before Stream Deck restarts |
| `%LOCALAPPDATA%\Hellgato\profile-import-backups` | Profiles replaced during import |

Use **Help → Open logs** to access the data folder, or **Help → About Hellgato** for version and license information. Uninstalling keeps settings and backups. Existing device identities and profile formats remain compatible.

## Build and test

Use Python 3.13 x64, Git and Inno Setup 6.7.3. In PowerShell, from the repository root:

```powershell
python -m venv work/build-venv
./work/build-venv/Scripts/python.exe -m pip install -r installer/requirements-build.txt
./work/build-venv/Scripts/python.exe scripts/prepare-windows.py
./work/build-runtime/node.exe scripts/prepare-cora.mjs

./work/build-venv/Scripts/python.exe -m unittest discover -s test -p 'test_*.py'
./work/build-runtime/node.exe --test test/*.test.mjs

$env:HELLGATO_NODE = (Resolve-Path ./work/build-runtime/node.exe).Path
./work/build-venv/Scripts/python.exe app/hellgato.py
```

Build the installer:

```powershell
./scripts/build-windows.ps1 -Python ./work/build-venv/Scripts/python.exe -Iscc 'C:\Path\To\Inno Setup 6\ISCC.exe'
```

Build inputs are pinned and verified against upstream source metadata and checksums. Outputs are `dist/Hellgato-0.0.3-beta-Setup.exe` and its SHA-256 sidecar. Personal profiles and logs are excluded. The Windows beta release workflow runs manually or when release notes under `docs/releases/` are added or changed on `main`. Update the version, installer filenames and workflow release command together before publishing release notes. Other pushes run checks without publishing.

| Directory | Purpose |
| --- | --- |
| `app/` | Windows launcher, interface and localization |
| `app/n4/` | Hardware input, display, profiles and compatibility |
| `app/cora/` | Stream Deck network transport |
| `app/locales/` | Interface translations |
| `scripts/` | Dependency preparation, geometry adjustment and builds |
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

Keep both package versions and the facade's exact core dependency in sync. The root package stays private. Packages include the prepared DeckBridge runtime and its license, so consumers need no Git checkout or build step. Publish the reviewed tarballs with the `beta` tag; npm publication is manual and is independent of the Windows installer release.

### Translations

The interface supports English, Korean, Spanish, Japanese, French, German, Russian, Italian, Brazilian Portuguese, Simplified Chinese and Traditional Chinese. Initial translations are open to native-speaker review.

1. Open the appropriate file in [`app/locales`](../app/locales). `en.json` is the source catalog.
2. Edit the text values, keeping message keys and placeholders such as `{count}` and `{filename}` unchanged. Use concise, neutral product language and consistent action names. Keep product names unchanged.
3. Preview the language in Hellgato and submit a pull request. You can use GitHub's file editor without setting up a local build.

Translation checks validate JSON, duplicate keys, missing/extra messages and placeholders. If you cannot submit a PR, use the [translation feedback form](https://github.com/restarea92/hellgato/issues/new?template=translation.yml).

The catalogs use standard flat JSON, compatible with translation platforms such as Weblate. No paid translation service or API key is required to run or build Hellgato.
