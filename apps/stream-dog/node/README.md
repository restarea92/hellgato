# Stream Dog headless core

`frame-runtime.mjs` is the service entrypoint. The Rust app starts it with an
explicit configuration and owns its lifetime. `frame-runtime-manager.mjs` manages
registered adapters and independent device sessions. `frame-session.mjs` connects
one adapter process to the SDK bridge and full-frame compositor worker.

`full-frame-*` modules implement retained layers, scheduling, rendering,
screensaver restoration and an optional developer preview. Production disables
the preview. `trace-log.mjs` supplies bounded diagnostics.

See the [architecture](../../../docs/architecture/0002-full-frame-runtime.md),
[build/QA commands](../rust/README.md) and
[adapter contract](../../../docs/adapters/frame-adapter-guide.md).
