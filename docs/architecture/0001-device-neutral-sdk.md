# Device-neutral SDK extraction

Status: implementation candidate. No release or new hardware support is implied.

## Boundaries

- `packages/core/src` is the only source of the Stream Deck bridge. The generated
  DeckBridge runtime is pinned to its existing revision. `app/cora` contains
  temporary compatibility wrappers, not another implementation.
- `@hellgato/core/contracts` owns validated device, capability, input and display
  contracts. The explicit bridge API requires a device and target. The old root
  `createBridge()` and `createN4ProProfile()` remain deprecated compatibility APIs.
- `hellgato` composes an explicitly registered adapter, mapping profile, host
  readiness and the bridge. Import/construction does not discover USB devices,
  install programs, patch the host or start workers.
- `hellgato/adapters/mirabox-n4-pro` is the N4 binding subpath; the Python runtime
  is separately provided. `hellgato/testing` exposes simulation/conformance tools.
- `adapters/mirabox-python` is a physical-worker package. Versioned NDJSON over
  stdio replaces trace files in the candidate API. This is process isolation,
  not a security sandbox for arbitrary third-party code.
- `apps/stream-dog/node` owns product persistence and the compatibility launcher.
  Windows staging installs the actual tarballs offline, with lifecycle scripts
  disabled and no workspace links, and embeds the SDK archive digests and full
  staged file inventory in `product-build-manifest.json`.
- `apps/stream-dog/python/hellgato_stream_dog` owns the canonical GUI, launcher,
  preferences and profile/identity handling. The old `app` entrypoints are
  compatibility aliases to those same module objects, not duplicate sources.
- `host-integrations/streamdeck/windows` owns host inspection/preparation and both
  native integrations. The physical worker does not import this host layer.

The public SDK consists of two fixed-version packages. Windows retains its own
private version source. A device binding does not acquire another package/version
until independent consumers, dependencies or maintainership justify one.

## Deliberately staged rollout

The Windows product continues using its existing N4 compatibility engine. Its
Node bridge now comes from the packed SDK; its proven Python display, settings,
identity, host safety and reconnect behavior are retained. The candidate sidecar
must pass recorded physical display/input/lifecycle parity before it replaces
that engine. Existing trace-file IPC is therefore still present in the product
compatibility path and is not presented as completion of phase A.

New SDK contracts, device bridge, target glue, session, sidecar, N4 binding and
simulation/conformance code are authored in strict TypeScript. The pinned compiler
emits the published ESM and declarations; `pnpm typecheck` checks implementation
bodies, public consumer types and generated-output drift. Source files and legacy
boundary declarations do not enter npm archives. Existing protocol extensions and
pinned vendor/legacy runtime remain staged JavaScript behind narrow typed boundaries.
The product candidate runner is outside this SDK conversion boundary.

The product/host moves, pinned Python build dependencies and quality gates are
implemented alongside compatibility tests. An isolated, manifest-bound
[Windows IPC trial](../compatibility/ipc-trial.md) packages the new physical worker
and supervisor separately from the ordinary installer. Its frozen self-test uses
the fake backend; actual Windows artifact checks and the physical recipe remain
distinct evidence. Bounded recovery, display/control restoration and local rollback
are implemented, but remain physical-test candidates. A directory move or simulated
test alone is not evidence of safe physical lifecycle migration.

Separate runtime/CompatibilityTools ZIP publication is phase B and is not offered
by this candidate. The portable trial is a product test bundle, not that independent
SDK distribution channel.

## Safety and compatibility

Only a single N4 Pro active physical session is supported by the old product.
N4 dial presses are pulses; physical dial release is unavailable. Synthesized
release does not implement hold or press-and-turn support.

The startup shim modifies Stream Deck's shared Plus model before the Node device
session starts. Simultaneous genuine Stream Deck + use remains unsupported even
when N4 is unplugged. We do not claim an automatic coexistence guard. A future
coexistence design must protect before model mutation and against hot-plugging.

The new SDK cannot prepare or modify Stream Deck itself. A caller supplies host
readiness and a verified worker path. Unknown firmware, host combinations, or
missing capabilities must not be silently promoted to supported.

## Releases

Changesets still calculates the fixed SDK pair and independent Windows version.
Archive allowlists, source impact checks and existing release approval remain.
npm public writes are disabled in both workflow and CLI while authentication is
resolved. Package builds, tests, offline installation and version/changelog work
continue. Windows publication is independent and retains its owner-approved plan
and repository enablement gates. This PR does not turn on publication or merge
itself. Reviewed stable/beta plans, immutable production baselines and the
next-to-main promotion handoff are implemented with replay tests; the
[release-channel policy](../release-channels.md) defines their closed publication
gates. No successful live beta/stable publication is implied by those tests.
