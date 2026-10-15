# @hellgato/core

## 0.1.0

Prepared source baseline on 2026-10-13; **not published to npm** as of 2026-10-15. This entry records source changes before Changesets adoption, not a registry release. Later SDK work is tracked by pending changesets.

- Add full-LCD image events, display commands for logo/color fills, and configurable sleep-duration reporting alongside key and touch images.
- Reject unsupported firmware-write operations and expose rejection diagnostics without forwarding those writes to the device.
- Close active network connections when stopping the bridge and forward server diagnostics through the public log event.
- Prepare the package version and release metadata for the stable Windows app while retaining the existing `createBridge` and `createN4ProProfile` API.

See [history sources and the adoption boundary](../../docs/changelog-history.md).

## 0.0.1-beta

Published to npm on 2026-09-30. [Registry metadata](https://registry.npmjs.org/@hellgato%2Fcore/0.0.1-beta).

- Introduce the ESM network bridge for Node.js 24, exposing `createBridge` and `createN4ProProfile` with configurable geometry and stable device identities.
- Forward validated key, dial and touch inputs to Stream Deck and emit key images, touch-strip images, brightness and connection events.
- Provide explicit start/stop lifecycle handling, local-only primary/child servers and cleanup after failed startup. Importing or creating the bridge does not open ports.
- Bundle the adapted MIT-licensed DeckBridge runtime and notices, with no npm runtime dependencies.

This package contains network transport only. Hardware drivers, the desktop UI and Stream Deck compatibility/ten-key adjustments are supplied separately by the Windows application.

## 0.0.0-stage

Published to npm on 2026-09-30. [Registry metadata](https://registry.npmjs.org/@hellgato%2Fcore/0.0.0-stage).

- Temporary staged-publication placeholder containing only a package manifest and README; it has no operational bridge implementation. Superseded by `0.0.1-beta` later that day.
