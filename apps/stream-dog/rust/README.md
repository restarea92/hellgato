# Stream Dog Windows app

The Rust app manages the headless Node core and independent device adapters.
See the [architecture](../../../docs/architecture/0002-full-frame-runtime.md),
[adapter guide](../../../docs/adapters/frame-adapter-guide.md) and
[beta compatibility notes](../../../docs/compatibility/stream-dog-rust-beta.md).

## Build and run

Use Node 24, the pinned pnpm, Rust and the Windows x64 toolchain from the repository root:

```powershell
pnpm install --frozen-lockfile
pnpm stage:windows-sdk
cargo build --release --manifest-path adapters/mirabox-rust/Cargo.toml
cargo build --release --manifest-path host-integrations/streamdeck/windows/rust/Cargo.toml
cargo build --release --manifest-path apps/stream-dog/rust/Cargo.toml
node scripts/stream-dog/stage.mjs --destination work/stream-dog-bundle
./work/stream-dog-bundle/HellgatoStreamDog.exe
```

Staging requires the reviewed Node executable, native host extension, owned
startup files and manufacturer DLLs from the existing installation. It verifies
and copies them into a fresh portable directory; Python runtime files are
rejected. Keep the bundle together. This command does not replace an installed app.

The app imports paired identity from persistent `settings.json` and stores its
preferences under `%USERPROFILE%/AppData/Local/Hellgato/stream-dog-rust`.
Stop the previous Hellgato app before opening USB. Open the reviewed Stream Deck
build yourself; host preparation never restarts it automatically. X hides the app
to the tray, while Quit stops the core and releases devices.

Build the installer from a verified bundle with the existing Inno Setup compiler:

```powershell
./scripts/stream-dog/build-installer.ps1 -Bundle work/stream-dog-bundle -Iscc work/toolchain/InnoSetup/ISCC.exe
```

## Verification

```powershell
node --test test/frame-*.test.mjs test/full-frame-*.test.mjs
cargo test --manifest-path adapters/mirabox-rust/Cargo.toml
cargo test --manifest-path host-integrations/streamdeck/windows/rust/Cargo.toml
cargo test --manifest-path apps/stream-dog/rust/Cargo.toml
node scripts/stream-dog/qa.mjs
./work/stream-dog-bundle/HellgatoStreamDog.exe --smoke --state-dir ./work/native-smoke
node scripts/stream-dog/smoke-runtime.mjs --bundle work/stream-dog-bundle
node scripts/stream-dog/smoke-runtime.mjs --bundle work/stream-dog-bundle --mirabox
node scripts/stream-dog/smoke-fleet.mjs --bundle work/stream-dog-bundle
```

The renderer checks the production view through GPU readback without Computer
Use. Runtime smokes use simulated devices, including forced-exit recovery,
independent sessions and complete-frame restoration. They do not verify physical
USB output, host restart, Windows drag/snap or a real installed upgrade.
The management app disables live preview; the runtime smoke enables it for image
verification. See [the hardware recipe](../../../docs/compatibility/hardware-testing.md)
for physical checks.
