# Contributing an adapter

Implement the public `hellgato` adapter contract, use validators from
`@hellgato/core/contracts`, and test with `hellgato/testing`. Do not import SDK
internal files or depend on the Stream Dog installation directory.

## Required contributions

1. A stable adapter/model ID, implementation/API versions, supported OS/architecture,
   actual firmware capabilities, license and maintainer.
2. Explicit discovery and selection: never choose arbitrarily from multiple devices.
3. Open/close ownership with cancellation and cleanup on partial startup, EOF and crash.
4. Ordered inputs with device ID, generation and sequence. Physical click pulses and
   emulation limits must be declared accurately; do not invent a physical release.
5. Bounded output with frame/region dimensions, verified payloads, serialized USB
   writes, explicit applied/accepted/unknown status, and no unsafe timed-out retries.
6. Schema fixtures, simulator/conformance results, anonymized replay, and a physical
   evidence row if support is claimed. An implemented VID/PID alone is insufficient.

The in-process adapter example can be exercised using only the two installed SDK
packages:

```js
import { createSession } from 'hellgato';
import { createButtonSimulator, createSimulatorProfile } from 'hellgato/testing';

const adapter = createButtonSimulator({ keys: 6 });
const session = createSession({
  adapter,
  deviceId: adapter.device.id,
  profile: createSimulatorProfile(adapter.device),
  port: 0, childPort: 0,
});
await session.start();
await session.stop();
```

This verifies composition and TCP lifecycle; it does not establish that a real
Stream Deck host accepts the simulator geometry. Follow the package README for
readiness and physical runtime manifest requirements. Python IPC protocol and
worker documentation are under `adapters/mirabox-python`.

## Support levels

- `experimental`: contract/simulator evidence only; excluded from the product.
- `community-verified`: reviewed owner evidence for the exact combination.
- `project-supported`: named maintainer, regression fixtures and product recovery evidence.
- `blocked`: known safety or compatibility issue; refuse connection.

Stream Dog only includes reviewed bindings. External adapters are explicitly
installed and registered by SDK developers. No automatic marketplace, runtime
download, firmware write or boot-logo upload is introduced.
