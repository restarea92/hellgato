# Windows Stream Deck host integration

The current app uses the Rust `HellgatoHostWorker.exe` in [rust](rust). It discovers the actual Stream Deck process, checks its path and lifetime, manages owned startup support and attaches the optional native Qt extension. The Node core supervises it; device USB access belongs to the adapter.

The tested executable digest identifies the reviewed baseline. A different version or digest produces a warning rather than an unconditional core connection failure. Startup changes still require compatible executable structures, and private automatic rescan remains restricted to verified native addresses. Optional UI attachment failure leaves the base core running.

## Native components

- [Startup support](native/startup/README.md): owned proxy DLL and executable-bound layout plan. The plan changes process memory on host startup, not `StreamDeck.exe` on disk. A genuine Stream Deck + cannot coexist with the shared layout patch.
- [Native UI](native/ui/README.md): optional Qt extension, including independent knob brightness.
- `python/hellgato_host`: legacy runtime compatibility and shared native-build preparation helpers. It is not shipped as a Python worker in the current app.

Builds extract and verify a reviewed official Stream Deck reference into an isolated directory. Preparation never establishes that an arbitrary newer host has been physically tested. The installer automatically closes the apps and restores a previously running Stream Deck; a developer helper invocation does not provide that installer lifecycle.

See [Windows build and QA](../../../apps/stream-dog/rust/README.md) and [release operations](../../../docs/windows-releases.md).
