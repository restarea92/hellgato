---
"@hellgato/core": minor
"hellgato": minor
---

Expose device-neutral SDK contracts and their JSON schema from `@hellgato/core`, including device descriptors, capabilities, mapping profiles, validated input events and display/control output. Add `createDeviceBridge`, `createSession` and `createSidecarAdapter` for library consumers while preserving `createBridge` and `createN4ProProfile`.

Add the `hellgato/adapters/mirabox-n4-pro` mapping-profile entry point and `hellgato/testing` simulation/conformance helpers. The bounded Python sidecar API remains experimental; simulator results do not establish hardware compatibility.
