# hellgato

Device-neutral session composition for a Stream Deck network bridge. This package connects an explicitly selected adapter, physical device, mapping profile, and optional host-readiness provider to `@hellgato/core`.

**Architecture candidate, not a new hardware-support release.** The in-process simulators and versioned Python sidecar are testable without USB hardware. The new N4 worker path remains hardware-gated. The production Windows N4 engine is not switched by importing this package, and independent worker downloads are not provided by npm.

## Public entry points

- `hellgato`: `createSession`, `createSidecarAdapter`, `createDeviceBridge`, and retained deprecated `createBridge` / `createN4ProProfile`
- `@hellgato/core/contracts`: normalized descriptors, profiles, events, runtime validators, and TypeScript contracts
- `hellgato/adapters/mirabox-n4-pro`: explicit experimental N4 binding, mapping factory, and support limitations
- `hellgato/testing`: button-only and non-N4 simulators, mapping fixtures, input replay, and adapter conformance

Importing or constructing a session does not discover hardware, run a worker, open ports, patch Stream Deck, or write identity files. No adapter is automatically downloaded or selected.

## Minimal device-neutral session

The example uses only installed package exports; no repository paths are required. Node 24 is currently supported.

```js
import { createSession } from 'hellgato';
import {
  createNonN4Simulator,
  createSimulatorProfile,
} from 'hellgato/testing';

const adapter = createNonN4Simulator();
const session = createSession({
  adapter,
  deviceId: adapter.device.id,
  profile: createSimulatorProfile(adapter.device),
  port: 0,
  childPort: 0,
});

session.on('diagnostic', detail => console.error(detail.code, detail.message));
await session.start();
console.log(session.ports);
adapter.emitInput({ type: 'key', index: 0, state: 'down' });
adapter.emitInput({ type: 'key', index: 0, state: 'up' });
await session.stop();
```

This verifies composition and lifecycle, not a real Stream Deck application's compatibility with a simulated geometry. Inputs report `delivered: false` until the actual host child connection exists. The compact fixture has four keys, two dials, a 320×60 physical touch sensor and an 800×100 output surface. Its mapping scales touch input into the currently supported 800×100 virtual Stream Deck Plus strip. Physical sensor coordinates, physical output dimensions and host target geometry are different contracts. The button-only simulator declares no dials or touch and uses a disabled 0×0 virtual touch target.

## Adapter contract

Implement `descriptor`, `discover`, `open`, `close`, `setOutput`, `setControl`, `onInput`, and `onState`. Optional lifecycle hooks are `preflight`, `start`, and `stop`; optional diagnostics use `onDiagnostic`. Subscription methods return unsubscribe functions. All lifecycle and output methods may be asynchronous. Cancellation-aware methods must honor their `AbortSignal`. `open(deviceId, {generation, sessionId, signal})` explicitly acquires one device; `close` must release any partially acquired ownership too. A session prevents two concurrent sessions from sharing its adapter instance; cross-process USB ownership remains the physical adapter's responsibility.

A `DeviceDescriptor` separately describes live firmware capabilities. Dial pulses are not real release events. Capability statuses are `native`, `emulated`, `unsupported`, or `unverified`; only supported statuses satisfy required features. Control output requires a corresponding `capabilities.features[control].status` of `native` or `emulated`.

Input envelopes include `deviceId`, `generation`, `sequence`, and optionally `sessionId`. Sequences increase monotonically within a connection generation. Old generations, foreign identities and out-of-order input are ignored. Malformed current-generation input fails the session closed. Mappings explicitly cover every physical key/dial with a target index or `null` omission. Silent truncation and duplicate target mappings are rejected.

Frame output uses `{type:'frame', surfaceId, format, width, height, data, region?}` with normalized identity/generation/sequence fields. `data` is base64 JPEG or RGB888. Width and height identify the full surface; region dimensions identify encoded patch bytes. Frame and scoped-control surface IDs are mapped through `profile.displays`. Image dimension/format transformations belong in the adapter; the generic session does not silently resize. Partial regions and control boundaries preserve order in a bounded queue; saturation stops the session instead of losing regions.

## Lifecycle and readiness

`start()` performs:

1. Static adapter preflight (including a sidecar's executable/manifest checks)
2. Adapter startup / IPC handshake and discovery
3. Explicit candidate selection, mapping/capability checks, and fresh host-readiness inspection
4. Bridge port binding, then explicit device opening
5. Output forwarding only after open succeeds

Failure cleans up in reverse order. `start` and `stop` are concurrent-call safe. `stop` cancels pending startup and reports cleanup failures. Resource ownership remains reserved while cleanup is unresolved; retry `stop()` before attempting another session. A successful explicit `reconnect()` cleans up, increments generation, rediscovers, and checks host readiness again. Input is never replayed. Automatic USB recovery, persistent display-state restoration, a multi-device manager, and automatic crash backoff are not implemented by this candidate facade.

Readiness is required when `profile.requirements.hostPreparation` is nonempty. Supply a function or `{inspect}` object returning:

```js
{
  ready: true,
  targetId: profile.target.id,
  profileId: profile.id,
  preparation: ['streamdeck-plus-ten-key'],
  host: {
    pid: 1234,
    creationTime: '2026-10-14T00:00:00Z',
    executableDigest: 'a real SHA-256 from the host inspector',
  },
}
```

PID, creation time and executable SHA-256 are required for prepared hosts. A readiness result is evidence supplied by your host-integration layer, not proof manufactured by the SDK. The provider must inspect the current host rather than return cached preparation. It is called before initial ports open, on explicit reconnect, and on each new host child connection. Input and output remain gated until the latest connection's check succeeds. Late results from disconnected hosts cannot authorize a new connection. The SDK never prepares or patches the host itself.

Session status separates lifecycle state, adapter connection, and host connection. A device disconnect fails closed and requires an explicit reconnect in this candidate.

## Explicit N4 sidecar binding

```js
import { createSession } from 'hellgato';
import {
  createMiraboxN4ProAdapter,
  createN4ProMappingProfile,
} from 'hellgato/adapters/mirabox-n4-pro';

// Values come from your verified product bundle or separately supplied runtime.
const adapter = createMiraboxN4ProAdapter({
  command: verifiedWorkerExecutable,
  args: [],
  manifest: verifiedRuntimeManifest,
});
const profile = createN4ProMappingProfile({
  virtualSerial: savedDockSerial,
  childSerial: savedChildSerial,
});
const session = createSession({
  adapter,
  deviceId: selectedPhysicalDeviceId,
  profile,
  readiness: currentHostInspector,
});
await session.start();
```

The runtime manifest identifies schema version, platform, architecture, build ID, executable SHA-256, and optional dependency file inventory. N4 currently requires an explicitly supplied Windows x64 manifest and verified worker path. Source-worker interpreter scripts must also appear in the verified manifest file inventory. The generic `createSidecarAdapter` supports explicit external-language workers; omitting its manifest means the application assumes responsibility for trusting and identifying those bytes.

The N4 physical runtime, actual USB display parity, firmware compatibility, installer recovery, endurance, and genuine Stream Deck Plus coexistence are not established by simulator/IPC tests. N4 clicks remain pulse-only, and real dial release, hold and press-and-turn are unavailable. Existing virtual identity must be supplied and preserved; the mapping factory never invents a new identity from a physical serial.

## Conformance and replay

```js
import { createButtonSimulator, runAdapterConformance, replayInputs } from 'hellgato/testing';
const adapter = createButtonSimulator();
const report = await runAdapterConformance(adapter, { deviceId: adapter.device.id });
console.log(report.checks, report.physicalHardwareVerified); // always false

await replayInputs(recordedNormalizedInputs, event => inspectRecordedEvent(event));
```

Conformance exercises discovery, normalized payloads when provided, explicit device open, subscriptions and cleanup. It is not a complete USB lifecycle/performance test. Replay validates the complete bounded fixture before delivering anything and retains recorded generation unless the caller explicitly remaps it. Do not replay recorded clicks into a live device unless that is intentional.

## Compatibility

Existing root `createBridge()` and `createN4ProProfile()` imports remain available and deprecated for the transition. Their N4 defaults belong to the bounded core legacy module. New `createSession` calls require an explicit adapter, device ID and mapping. `@hellgato/core` is a Stream Deck bridge, not a USB driver, and this facade never imports product installation paths.
