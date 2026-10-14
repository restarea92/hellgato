# @hellgato/core

An explicit, physical-device-neutral Stream Deck network bridge. ESM, Node 24,
no npm runtime dependencies. `hellgato` provides the higher-level adapter/session API.
This package does not open USB, launch Python, install software, patch Stream Deck,
write identity files, or include the Windows product.

## Explicit API

```js
import { createDeviceBridge } from '@hellgato/core';
import { validateDeviceDescriptor } from '@hellgato/core/contracts';

const device = validateDeviceDescriptor({
  id: 'two-key-1', adapterId: 'example', modelId: 'two-key',
  capabilities: {
    keys: 2, dials: 0, touch: null, displays: [],
    dialPress: 'unsupported', dialRelease: 'unavailable',
  },
});
const bridge = createDeviceBridge({
  device,
  sessionId: 'my-session', generation: 1,
  target: {
    id: 'example-two-key',
    geometry: {
      rows: 1, columns: 2, keyCount: 2, keyWidth: 72, keyHeight: 72,
      encoderCount: 0, touchWidth: 0, touchHeight: 0, productName: 'Example',
    },
    config: {
      serialNumber: 'MYDOCK0001', childSerialNumber: 'MYCHILD0001',
      dockFirmwareVersion: '1.0', childFirmwareVersion: '1.0',
      productId: 0x0080, macAddress: [2, 0, 0, 0, 0, 1],
    },
  },
});
bridge.on('output', output => { /* forward normalized frame/control to adapter */ });
await bridge.start();
bridge.submitInput({
  deviceId: device.id, sessionId: bridge.sessionId, generation: bridge.generation,
  sequence: 1, type: 'key', index: 0, state: 'down',
});
bridge.submitInput({
  deviceId: device.id, sessionId: bridge.sessionId, generation: bridge.generation,
  sequence: 2, type: 'key', index: 0, state: 'up',
});
await bridge.stop();
```

The example is a protocol fixture, not evidence that this custom geometry works in
an unmodified Stream Deck installation. A caller must select a host-supported
mapping target and perform any required host readiness checks before `start()`.
Physical capabilities, virtual target capabilities and verified hardware support
are separate facts. Pairing is manual; servers bind only to `127.0.0.1`, with no mDNS.

## Lifecycle and identity

Import and construction are inert: runtime loading and server creation happen at
`start()`. Default ports are 5343/5344; use zero for assigned test ports. Calls to
`start()` and `stop()` are serialized and idempotent. Failed startup closes every
acquired listener/socket and can be retried. `state`, `getState()`, `state` and
`connection` events expose lifecycle separately from primary/child host connectivity.

`deviceId`, `sessionId` and `generation` have distinct fields. Generation increments
when a bridge that reached ready is stopped and restarted. Use the current getter
for new input; stale identities, generations and duplicate/nonincreasing sequences
return false. A failed startup retry keeps its generation. `submitInput`/`sendInput`
validate normalized input against the selected host geometry after physical mapping.
They return false without a connected child. Bad payloads throw `ContractError` or
`RangeError`. Only actually forwarded presses are tracked; disconnect/stop clears
state and sends safe releases where a host connection is still available.

`releaseInputs()` safely resets forwarded state. Low-level `sendKey`, `sendDial`
and `sendTouch` remain available for compatibility; callers using these methods own
input sequencing. `kind: 'pulse'` in normalized dial input explicitly requests a
synthetic down/up click. It does not imply a physical dial release or hold support.

Persistent physical selection and stable virtual serial allocation belong to the
caller/product. Switching mapping profiles must not silently allocate a new serial.

## Public contracts and output

`@hellgato/core/contracts` exports TypeScript types and validators for
`AdapterDescriptor`, `DeviceDescriptor`, `DeviceCapabilities`, `MappingProfile`,
`HostTarget`, `InputEvent`, `DisplayFrame` and `ControlCommand`. `validate*` and
`assert*` return the checked value or throw `ContractError` (`ERR_CONTRACT`, `path`).
The serialized source is `@hellgato/core/schemas/contracts.schema.json`; semantic
checks additionally enforce unique surface IDs, mapping bounds, capability
consistency and image byte/dimension limits. `validateContract` provides structural
schema validation only; use the named validators at application trust boundaries.

Capability status distinguishes native, emulated, unsupported and unverified.
N4 pulse input requires `dialPress: 'pulse'`, `dialRelease: 'unavailable'`. Physical
descriptors can have zero keys, dials or touch, and are not limited to four dials.
The current Stream Deck transport supports up to four target encoders and either
no virtual touch strip or the Stream Deck Plus 800×100 target. These restrictions
belong to the host implementation. Physical sensors can have different dimensions.

`output` emits a public `DisplayFrame` or `ControlCommand` plus device/session,
generation and output sequence. Frame bytes are padded base64, never a path or
vendor object. Supported formats are JPEG and RGB888. JPEG SOI/EOI and SOF dimensions
are checked before forwarding; the physical adapter must still fully decode and
verify the actual image before native output. Limits: 1 MiB compressed bytes, 8192
per dimension, 16,777,216 pixels, exact RGB888 byte length, and bounded regions.
Unsupported/malformed host output emits a structured `diagnostic` and is not forwarded.
New normalized output does not claim BMP/gen1 support.

Virtual surface names are `key:N`, `touch`, and `lcd`. Key/touch geometry comes from
the target. To receive full LCD frames, declare
`target.displays: [{ id: 'lcd', width, height, format: 'jpeg', partial: false }]`.
The facade maps these virtual names to physical display IDs. Controls include
brightness, sleep duration, clear, fill, sleep, wake and show-logo; this does not
promise every adapter implements them. `sleepDuration` is caller-persisted state.

## Legacy compatibility

The original root imports `createBridge(options?)` and `createN4ProProfile(...)`
remain callable, including the no-argument N4 default. They are deprecated in types
and source. The only built-in N4 default lives in a quarantined core legacy module;
the explicit API never imports that module and core never imports the facade.

```js
import { createBridge, createN4ProProfile } from '@hellgato/core';
const bridge = await createBridge({ ...createN4ProProfile(42), port: 0, childPort: 0 });
await bridge.start();
await bridge.stop();
```

Legacy image/display events retain their earlier byte payloads: `image`,
`touchImage`, `lcdImage`, `lcdFrameStarted`, `displayCommand`, `sleepDuration`, and
`brightness`. Connection/log events retain `{ role, detail }`. A future removal
requires a documented major migration. Legacy N4 functionality and historical
hardware evidence do not verify the new architecture on real USB hardware.

## Source and license

`packages/core/src` is the sole authored core source. `app/cora` contains development
compatibility wrappers; packaging copies no application source and rewrites no
application imports. The pinned DeckBridge runtime is reproducibly prepared into
`src/vendor` (ignored generated content) and copied verbatim to `dist/vendor`.

MIT. Includes adapted DeckBridge code, copyright 2026 Lukas, under MIT. See
`THIRD-PARTY-NOTICES.txt`, `dist/vendor/LICENSE` and provenance.json. Independent of
Elgato and Mirabox. No hardware validation or public-release claim is made here.
