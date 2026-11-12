# Full-frame runtime

This is the Node/Rust beta architecture. The previous stable Python installer
still has its own build and regression tests until installer migration is
verified. Its architecture is recorded in [0001](0001-device-neutral-sdk.md).

| Boundary | Responsibility | Entry |
| --- | --- | --- |
| Windows app | Device/adapter management, settings, tray and core lifetime | `apps/stream-dog/rust/src/main.rs` |
| Headless core | Host sessions, composition, latest-frame scheduling and reconnect supervision | `apps/stream-dog/node/frame-runtime.mjs` |
| Device session | One bridge, compositor worker and adapter process | `apps/stream-dog/node/frame-session.mjs` |
| Adapter | Discovery, firmware, physical layout, input and USB output | `adapters/mirabox-rust` or an installed `.hga` |
| Host integration | Host readiness, startup ownership and native extension attachment | `host-integrations/streamdeck/windows/rust` |
| Public SDK | Reusable validated bridge/session contracts | `packages/core`, `packages/hellgato` |

Input flows from device to adapter to SDK to Stream Deck. Host display updates
return to one retained offscreen canvas per device, then one complete frame is
submitted to the adapter. This is an in-memory compositor, not an OS virtual
monitor driver. Normal layers retain keys and one continuous touch strip above
the background. An opaque screensaver covers them; wake restores retained state.

The core serializes each device's output and coalesces new changes into the
latest state, capped at 60 submissions per second. Unchanged screens submit no
frames. USB submission timing excludes plugin execution and physical LCD scanout.
The optional developer preview uses the same composed stream and is disabled in
the management app. The runtime owns API, preview and signal lifetimes; sessions
do not create competing status servers or stop/restart installed apps.

N4 Pro transmits the complete canvas over USB. Explicit N4/293S compatibility
adapters split it internally into volatile regions. They do not change the
core's complete-frame contract or repeatedly write persistent backgrounds.
Device sessions save independent identities, ports, canvases and reconnect intent.
New adapters require registration, not core source changes.

Build and QA: [Windows app](../../apps/stream-dog/rust/README.md).
Adapter protocol/package: [development guide](../adapters/frame-adapter-guide.md).
Hardware and installer limits: [beta notes](../compatibility/stream-dog-rust-beta.md).
