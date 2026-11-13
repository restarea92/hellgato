# Full-frame runtime

The current Windows beta has three product boundaries:

| Component | Owns | Entry |
| --- | --- | --- |
| Rust management app | Device selection, adapter installation, settings, backups UI, tray and core lifetime | `apps/stream-dog/rust/src/main.rs` |
| Headless Node core | Stream Deck sessions, virtual identities, retained canvas, scheduling, backups and reconnect supervision | `apps/stream-dog/node/frame-runtime.mjs` |
| Independent device adapter | Discovery, actual firmware reporting, physical layout, input translation and USB output | `adapters/mirabox-rust` or an installed `.hga` |

The Rust host helper owns Stream Deck discovery, startup ownership and optional native extension attachment. The reusable SDK in `packages/core` and `packages/hellgato` supplies the network bridge/session contracts. Python remains in legacy source and native build tooling, outside the shipped runtime.

## Input and display

Input travels **device → adapter → core/SDK → Stream Deck**. Stream Deck runs plugins and returns display updates. The core retains key images and one continuous strip over the background in a per-device canvas. An opaque full-canvas screensaver covers those layers; wake restores retained key/strip state.

Output travels **Stream Deck → virtual-device bridge (receive) → separate compositor (combine) → complete frame → adapter → USB device**. The virtual device is the network identity/communication endpoint; it receives display updates but does not compose them. The canvas is in memory; it is not a Windows virtual monitor driver. Stream Dog manages it without a production preview.

Each device has one transfer in flight and only the newest pending state. Idle screens submit no frames; active output is capped at 60 fps. USB submission timing does not measure plugin execution or LCD scanout. N4 Pro receives the entire canvas over USB. N4/293S compatibility adapters split that same canvas into volatile model-specific regions internally, without persistent background writes.

## Extension and lifecycle

Each session owns its bridge ports, compositor worker, adapter process, identity and recovery state. The runtime owns the local API and shutdown. Closing the GUI hides it; Quit stops its owned core and workers. Registration of a new `.hga` does not require core or GUI source changes. A manufacturer SDK is optional.

Multiple simulated sessions are verified; multiple physical devices remain unverified. See the [user guide](../guide.md#compatibility-and-limitations), [build instructions](../../apps/stream-dog/rust/README.md), [adapter contract](../adapters/frame-adapter-guide.md).
