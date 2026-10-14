# Mirabox physical worker (candidate)

This package runs independently of the Windows GUI, Node bridge and Stream Deck
installation. Native USB still uses the explicitly prepared Mirabox Python SDK.
The existing product engine remains the default until the new path passes the N4
hardware, reconnect, display, screensaver, Windows shutdown and endurance gates.
Passing the fake backend is **not** physical-device support evidence.

Source-development entry point (from an installed wheel, no repository imports):

    hellgato-mirabox-worker --sdk-path C:\absolute\prepared-sdk\src --build-id <manifest-build-id>

The source candidate is tested on Python 3.12 and targets the existing product
Python 3.13 toolchain. The optional `physical` extra pins Pillow. The vendor SDK
is separately prepared from the pinned repository revision; it is not downloaded
on import or startup. Production callers must validate the executable, native
files and source inventory against a manifest before starting the process.
An independent public worker ZIP is a later delivery gate, not supplied here.

For integration tests without the SDK or Pillow:

    PYTHONPATH=adapters/mirabox-python/src python -m hellgato_mirabox.worker --backend fake --build-id integration-test

`fake` must always be selected explicitly. It provides a two-key, one-dial
in-memory device with 4×4 RGB surfaces, bounded state and optional bounded delay
or input burst for protocol tests. No test injection operations are enabled in
physical mode.

## Protocol and bounds

JSON-RPC 2.0 objects are UTF-8 NDJSON. stdout is reserved at the OS descriptor
level, redirecting even vendor/native print output to stderr. Request IDs are
nonempty strings (up to 128 characters); batch requests and command notifications
are rejected. Requests carry an epoch-millisecond `deadlineAt`, at most 60 seconds
in the future. Required methods: `hello`, `discover`, `open`, `close`, `setOutput`,
`setControl`, `getState`. Unknown methods and unsupported controls fail explicitly.

Hello exchanges protocol 1.0, implementation version, manifest build ID, required
features and byte limits. Major/required-feature mismatch fails before USB access.
Open requires explicit device ID and a strictly increasing positive generation.
Inputs carry device ID, generation and monotonically increasing sequence.
Stale generations and duplicate/non-increasing output sequence are rejected.
An open worker owns at most one physical device.

Maximum NDJSON message: 1,500,000 bytes including newline. Maximum decoded frame:
1,048,576 bytes; maximum dimension: 8192; maximum decoded pixels: 16,777,216.
Canonical base64, JPEG header dimensions, decoder dimensions, surface dimensions
and region bounds are checked independently. The canonical SDK contract schema
is bundled byte-for-byte and CI detects drift. Worker queues are limited to 32
requests / 4.5 MB and 128 outgoing messages / 4.5 MB. Overflow fails the session,
never silently drops key releases. There is one serialized native writer;
heartbeat uses the same lock. Every actual vendor display write rechecks its
deadline after decoding and temporary-file preparation. A timed-out request is
never automatically retried, because already submitted output may have applied.

The initial implementation preserves FIFO frame/region/control ordering and
rejects excess work rather than coalescing partial updates. No coalescing crosses
clear/fill/sleep/wake barriers. EOF cancels queued work and closes the device.
The Node owner first closes normally, then uses bounded termination and forced
termination if necessary. Windows frozen executable/process-tree cleanup and
physical stop/input latency under a display flood still require hardware tests.

## Physical behavior and limitations

The N4 binding retains the existing physical key-number fix, dial direction and
pulse semantics, 112-to-100 touch calibration, 180° framebuffer rotation,
10-key LCD crop grid, four secondary-screen panel writes, and safe shutdown
helper. Vendor path-only image APIs use private bounded temporary files removed
after each call. Frames never travel between processes using disk files.

Native commands report `submitted` / `unconfirmed`, never an invented physical
acknowledgement. The fake backend reports `applied` / `simulation`. N4 dials report
`pulse`, with physical release `unavailable`.

The candidate currently supports key/touch/LCD output and brightness, fill,
clear, sleep and wake. `sleepDuration`, `showLogo`, saved native screensaver
playback, knob brightness, firmware writes and boot-logo operations are not
available through this worker. USB disappearance is reported; reconnect requires
an explicit close/open with a new generation. Product parity and automatic state
restoration are release gates; the legacy product path remains available.

Packaging uses the explicit private marker `0.0.0.dev0`, read dynamically from the
package. It is not a third release/version owner. The phase-A Windows product
metadata owns release numbering, and the manifest build ID binds each candidate
to its reviewed source/artifact inventory. No public Python package is published.

The Node launcher passes a small OS/environment allowlist plus caller-supplied
`env`; it does not inherit `NODE_OPTIONS`, `PYTHONPATH` or unrelated secrets by
default. An explicitly supplied source-development `PYTHONPATH` is trusted caller
configuration. Executable symlinks are resolved before digest verification and
launch; manifest dependency entries that are symlinks are rejected. Executing an
explicit adapter remains code execution, not a security sandbox.
