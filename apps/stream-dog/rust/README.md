# Stream Dog Windows app

The Rust app manages the headless Node core and independent device adapters.
See the [architecture](../../../docs/architecture/0002-full-frame-runtime.md),
[adapter guide](../../../docs/adapters/frame-adapter-guide.md) and
[user guide](../../../docs/guide.md).

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

Prepare inputs before staging: the reviewed Node executable, native Qt extension,
owned startup files and manufacturer DLLs. The manual **Windows Rust build**
workflow documents clean-machine preparation using an extracted official host
reference; staging defaults use the existing local preparation paths. It verifies
and copies them into a fresh portable directory; Python runtime files are
rejected. Keep the bundle together. This command does not replace an installed app.

The app imports paired identity from persistent `settings.json` and stores its
preferences under `%USERPROFILE%/AppData/Local/Hellgato/stream-dog-rust`.
Stop the previous Hellgato app before opening USB. Open Stream Deck yourself for this development run; the installer separately manages automatic shutdown and restart. X hides the app
to the tray, while Quit stops the core and releases devices.

Build the installer from a verified bundle with the existing Inno Setup compiler:

```powershell
./scripts/stream-dog/build-installer.ps1 -Bundle work/stream-dog-bundle -Iscc work/toolchain/InnoSetup/ISCC.exe
```

## Verification

The development app keeps the virtual host connection alive without hardware by
default. This per-device setting is independent of showing/hiding the native
virtual controller. `--virtual-device` opens the selected controller, including
when an instance is already running. The floating window retains application
focus for action targets, supports key down/up and touch gestures, and turns a
dial by angular dragging or its hover minus/plus arrows. The drag gauge follows
the pointer from its starting direction, with eight marks and 45-degree input steps. Click without
dragging sends a dial click. The N4 Pro
controller uses a rounded gunmetal 2D body with key bezels, a continuous strip and
dials. Its default size is 50%; Settings offers DPI-aware 25% steps without
changing the stream or disconnecting the host. Only the device body is shown;
drag its empty areas to move it, and show/hide it from the management app.

Native fixture cases `virtual-2d`, `virtual-hover` and `virtual-knob`
can be rendered with the existing `--ui-qa` command. They do not send host input.

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
USB output, host restart, Windows drag/snap or an installed upgrade. The 0.2.1 installed upgrade was verified separately.
The management app disables live preview; the runtime smoke enables it for image
verification. See [the hardware recipe](../../../docs/compatibility/hardware-testing.md)
for physical checks.
