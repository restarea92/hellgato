# hellgato

## 0.1.0

Prepared source baseline on 2026-10-13; **not published to npm** as of 2026-10-15. This entry records source changes before Changesets adoption, not a registry release. Later SDK work is tracked by pending changesets.

- Align the facade version and its `@hellgato/core` dependency with the prepared `0.1.0` source baseline.
- Retain the existing `createBridge` and `createN4ProProfile` re-exports, including the core's new LCD/display/sleep events and lifecycle fixes. No separate facade API was introduced in this preparation.

See the [core source history](../core/CHANGELOG.md) and [history sources and the adoption boundary](../../docs/changelog-history.md).

## 0.0.1-beta

Published to npm on 2026-09-30. [Registry metadata](https://registry.npmjs.org/hellgato/0.0.1-beta).

- Introduce the `hellgato` ESM entry point for Node.js 24, re-exporting `createBridge` and `createN4ProProfile` from `@hellgato/core`.
- Pin `@hellgato/core` to `0.0.1-beta` so both package names use the same bridge implementation.
- Document network pairing, hardware-adapter responsibilities and the separation between the developer library and the Windows installer.
