# hellgato

## 0.2.1

### Patch Changes

- ab1a725: Recover incomplete Stream Deck child pairing by refreshing its capabilities, then resetting only the stalled dock connection if necessary. Limit retries, preserve regular unpairing after a stable session, and cancel recovery when the bridge stops.
- Updated dependencies [ab1a725]
  - @hellgato/core@0.2.1

## 0.2.0

### Minor Changes

- b7c8f2d: Expose device-neutral SDK contracts and their JSON schema from `@hellgato/core`, including device descriptors, capabilities, mapping profiles, validated input events and display/control output. Add `createDeviceBridge`, `createSession` and `createSidecarAdapter` for library consumers while preserving `createBridge` and `createN4ProProfile`.
  
  Add the `hellgato/adapters/mirabox-n4-pro` mapping-profile entry point and `hellgato/testing` simulation/conformance helpers. The bounded Python sidecar API remains experimental; simulator results do not establish hardware compatibility.
- 6cbc736: Generate SDK JavaScript and declaration files from strictly checked TypeScript sources, including typed bridge output/connection listeners and session events.
  
  Fix SDK sidecar reuse across sessions by tracking worker-process generations. Retain device ownership until close succeeds so failed cleanup can be retried safely.

### Patch Changes

- Updated dependencies [b7c8f2d]
- Updated dependencies [6cbc736]
  - @hellgato/core@0.2.0

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
