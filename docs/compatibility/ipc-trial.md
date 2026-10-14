# Isolated Windows IPC trial

This document describes a **CI-only experimental candidate**, not a new physical
support claim or public release. The ordinary installer continues selecting the
existing N4 engine. No Rust rewrite or npm publication is involved.

## What is packaged

A Windows build produces two separate deliverables from the same exact source:

- The ordinary installer, using the packed SDK bridge and retained physical engine.
- `Hellgato-Ipc-Candidate-<source>.zip`: a portable supervisor, pinned Node,
  offline-installed SDK, frozen Python worker/native dependencies, exact manifests,
  and a read-only host inspector. It is not independently published as an SDK runtime.

The second artifact is the path that actually exercises the new IPC worker.
`HellgatoWorker.exe` in the ordinary installer is the compatibility execution helper,
not this new worker. There is no automatic GUI engine switch.

The immutable manifests bind executable/dependency hashes, source SHA, build ID and
SDK tarball provenance. The runtime verifies them before starting; CI runs the exact
frozen worker through the real Node owner with the explicit fake backend. That tests
packaging and lifecycle without USB or a live Stream Deck host. Windows job fixtures
also verify parent/descendant termination. These do not replace physical testing.
Candidate staging requires a clean committed checkout and rechecks the entire
packed SDK file inventory; uncommitted source cannot masquerade as that commit's
immutable trial. Local development smoke checks remain separate evidence.

The frozen worker also carries the exact reviewed firmware policy JSON. Staging
compares it to the source before including it in the worker's hashed inventory.
Windows freezing requires the complete pinned vendor SDK to validate offline;
a fake-backend test cannot excuse missing physical-runtime files.

## Before a physical trial

1. Record the candidate artifact/source/digests, Windows version, N4 exact hardware
   revision/firmware, and Stream Deck version. Do not attach raw device serials.
   The candidate currently permits only the historical N4 Pro E baseline firmware
   `V4.N4 Pro E.02.010`. This is an experimental restriction, not new physical-test
   evidence. An unknown or unreadable firmware fails closed before display setup.
2. Keep a verified previous installer available. The trial refuses missing or
   inconsistent saved device identity; use an already registered N4.
3. The installed Stream Deck startup shim must exactly match this candidate's
   bytes and reviewed plan. If it does not, use the **ordinary installer from the
   same CI run**, follow its existing explicit startup-setup UI, and confirm the
   old engine works. The trial itself does not elevate, patch or prepare the host.
4. Genuine Stream Deck + coexistence is unsupported, even with N4 disconnected.
   Do not connect one for this trial. Other unknown hardware combinations are not
   promoted to supported.
5. Fully exit Hellgato from its tray menu and close Stream Deck. Extract the entire
   trial ZIP, then run `Run experimental test.cmd` and read the warning.

Type `TEST` only when ready. The supervisor holds the existing launcher mutex and
the physical worker claims the existing bridge mutex, preventing two USB engines.
It snapshots settings, device identity/control preferences and Stream Deck profiles
while the host is closed. It preserves independent dock/child identity and the dock
MAC. The candidate never creates a new virtual serial or rewrites the original state.

Open the existing Stream Deck app only when prompted. Every connection/reconnection
requires fresh live host PID/creation-time, executable/installed shim/plan hashes and
actual loaded ten-key geometry. A failed check stops instead of preparing the host.
Keep the ordinary Hellgato app closed while this supervisor is running. If the N4
profile does not appear, use Stream Deck's network-device pairing UI with
`127.0.0.1` and port `5343`. This connects to the candidate's preserved virtual
identity; seeing the PC app alone is not a physical display or input check.

## During the trial

Use the console for `status`, `brightness 50`, `knob 50`, `follow on`, `follow off`,
`sleep-time 60`, `sleep`, `wake`, `saver`, or `logo`. Logo means a temporary software
display, never firmware/boot-logo programming. Press Enter, enter `stop`, or press
Ctrl+C to stop. Lost supervisor input also stops the session. A kill-on-close Windows
job owns Node and its descendants; a bounded graceful shutdown comes first.

Candidate output restoration is bounded, generation-stamped and never replays
inputs. Physical SDK writes remain `submitted/unconfirmed`; fake writes alone are
`applied/simulation`. Recovery has a finite attempt budget and fails closed if safe
bounded display restoration is impossible. Saved images never become trace-file IPC.

During fresh connection inspection, product output admission pauses. Its pending
output is limited to 32 entries and 8 MiB, including one submitted operation awaiting
the worker reply. A newer full frame may supersede pending frames of the same surface,
format and dimensions after the last control command; partial frames and control
order remain intact. Only completed submissions enter restoration history. An
irreducible burst still stops with `OUTPUT_OVERFLOW` rather than losing partial data.

### Diagnosing black key icons

`status` and the supervisor's periodic status records include
`observations.display`. These observations retain fixed numeric summaries, never
image bytes, image hashes, profile names, paths or device identities.

- `surfaces` has ten `key:0` through `key:9` entries, `touch` and `lcd`.
  `received` counts validated frames entering the product queue; `coalesced`
  counts pending frames replaced by newer matching full frames. `submitted`
  counts successful worker replies, `failed` counts rejected submissions and
  `restored` counts successful replay replies separately. Counts accumulate for
  the run. A worker reply can mean a cached update while idle; it does not prove
  that a vendor call or physical display update occurred.
- `worker` is the latest worker summary, or `null` when unavailable. It resets
  on a new device open. `keyCacheCount` describes the current normal key cache;
  `keyCacheClears` counts cache-clear operations. Its ten `keys` entries track
  physical key-write attempts, completed submissions and failures, including
  LCD crops and all-icon clears. Each records the last writer type, dimensions,
  mean RGB brightness and whether every pixel was black. Writer types distinguish
  direct `key` frames from `lcd`, `clear`, `fill`, `initialize`, `restore`,
  `screensaver` and `logo` writes.
- `vendorReturnKinds` and `lastVendorReturnKind` distinguish `none`, `zero`,
  `negative`, `positive` and `other` SDK return values. Positive nonzero values
  remain submitted/unconfirmed under the existing writer policy; their presence
  needs investigation against the pinned SDK. All-icon clears hide the native
  return value, so `none` is not evidence of success. No return category proves
  physical delivery.

For a new committed, manifest-bound trial, collect a status while paired and
another after changing profiles and returning. If no key frames are received,
investigate host decoding/delivery. If replies succeed but physical key attempts
do not increase, compare idle state and cached updates. If key attempts occur,
compare their brightness, return categories and last writer. An `lcd` or `clear`
writer with black pixels supports an overwrite hypothesis; a positive return
supports a vendor-call failure hypothesis. Neither establishes the cause without
the physical observation and timing of the trial. Stop before USB reconnect
tests until this basic image path is understood.

Use [the hardware recipe](../../tests/hardware/README.md) for the named physical
combination: normal controls/display/idle behavior, 20 USB reconnects and 20 host
restarts, crash cleanup, reboot and sleep/resume, contention, and a full workday with
measured latency/resources. Software fault-injection and instrumentation are our
responsibility; do not ask a tester to invent malformed protocol commands.

## Logs and return to the existing engine

Each run lives under `%LOCALAPPDATA%\Hellgato-Ipc-Candidate\<source>\<run>`.
The console prints its exact path. Keep `config.json`, screensaver snapshots and
`backup/` private: they contain identities, profiles or personal images. Diagnostics
are bounded and omit raw serials, paths, profiles and frame payloads. Review even
redacted diagnostics before sharing. Nothing is uploaded automatically.

Normal rollback is simply stop the candidate, close its console, and launch the
ordinary Hellgato app. If profiles/settings need restoring, close both apps and run:

    HellgatoIpcTest.exe --restore-backup "<run-folder>\backup"

Read the warning and type `RESTORE`. The helper verifies backup bytes, only targets
known per-user locations, preserves displaced settings, and retains the previous
profile directory alongside `ProfilesV3`. New files absent at baseline are preserved;
this is not a cleanup or deletion operation. Restore while both apps are stopped.

Backups are published only after every copy and the final source snapshot verify.
An interrupted restore attempts to put every displaced setting and the previous
profile directory back. If any undo fails, the helper retains recovery staging,
prints the local recovery paths, and asks you to keep both apps closed. Do not
delete these recovery copies or retry by manually overwriting a live profile tree.

A CI artifact passing this recipe remains a candidate until the named physical
results, final source/build verification and separate publication approval exist.
