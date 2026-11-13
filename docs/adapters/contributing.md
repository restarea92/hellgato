# Contributing an adapter

For the Windows app, implement an independent worker and register an `.hga` package using the [frame adapter guide](frame-adapter-guide.md). The core accepts a complete composed frame; hardware-specific USB commands stay inside the adapter. A manufacturer SDK is optional.

Include the exact model, VID/PID, firmware, redistributable dependency licenses and physical verification results. Clearly distinguish simulation from hardware tests and declare compatibility-region output where required. Use the [hardware checklist](../compatibility/hardware-testing.md).

The separate [public TypeScript SDK](../../packages/hellgato/README.md) also supports in-process and sidecar sessions; its API is not the Windows `.hga` protocol.
