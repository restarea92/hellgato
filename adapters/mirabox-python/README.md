# Mirabox physical worker (candidate)

This package runs independently of the Windows GUI, Node bridge and Stream Deck
installation. Native USB still uses the explicitly prepared Mirabox Python SDK.
The existing product engine remains the default until the new path passes the N4
hardware, reconnect, display, screensaver, Windows shutdown and endurance gates.
Passing the fake backend is **not** physical-device support evidence.

The bundled experimental firmware policy currently admits only the exact string
`V4.N4 Pro E.02.010`, based on the historical product baseline. This is permission
to run the isolated trial, not candidate hardware verification. Missing, unknown
or different firmware is rejected before display initialization; no runtime
override is provided. Discovery may briefly open only the native transport to
read firmware, then closes it before returning the candidate descriptor. It does
not initialize the screen or forward inputs. Open checks the firmware again.

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
10-key LCD crop grid, saved framebuffer/panel strip policy, and safe shutdown
helper. Vendor path-only image APIs use private bounded temporary files removed
after each call. Frames never travel between processes using disk files.

Native commands report `submitted` / `unconfirmed`, never an invented physical
acknowledgement. The fake backend reports `applied` / `simulation`. N4 dials report
`pulse`, with physical release `unavailable`.

The physical worker isolates the pinned SDK's relative temporary image files in
its own temporary working directory. Normal shutdown and Python exceptions restore
the original directory and remove those files. Forced process/OS termination can
leave an OS-temporary directory behind; it must not be included in diagnostics.

The candidate supports key/touch/LCD output, brightness, fill, clear, sleep/wake,
software `sleepDuration`, saved screensaver playback, and optional checked knob
brightness. `showLogo` is an optional software display overlay. Firmware writes
and boot-logo operations remain unavailable. USB disappearance is reported; reconnect requires
an explicit close/open with a new generation. Product parity and automatic state
restoration are release gates; the legacy product path remains available.

## Explicit assets and state semantics

Physical startup accepts `--strip-mode frame|panels` (standalone default: panels).
The product must pass its saved `stripMode`; frame mode preserves the legacy
rotated 800×130 initial strip with a 20px offset and inverted partial-update
coordinates. Full LCD output and wake restore reinitialize that baseline. Panel
mode writes only intersecting secondary-screen panels.

Physical startup may add `--screensaver-path C:\absolute\saved-animation.webp`
and `--logo-path C:\absolute\hellgato-front-64.png`. These are caller-selected,
read-only local files; neither paths nor arbitrary file reads are accepted over
IPC. The worker never reads Stream Deck settings, registry or user preferences.
Assets are loaded once per open and must be supplied again on restart. Invalid
assets fail open and close the device instead of silently changing the source.

Saved PNG/JPEG/GIF/WEBP/BMP preparation retains the legacy centered 800×480 canvas,
alpha compositing, 180° rotation and frame timing. Bounds: 64 MiB source, 4 million
pixels per frame, 256 million total transformed pixels, 1,000 frames and 128 MiB
encoded playback bytes. Cancellation is checked during preparation and before
individual native writes. The software logo accepts one PNG, at most 1 MiB and
800×480 pixels. Neither asset is written to firmware or persistent device storage.

Controls added to the canonical schema:
- `sleepDuration`, integer seconds 0–2,147,483,647; zero disables the local idle timer
- `screensaver`, enter idle and play the configured saved source, or truly sleep
  when no saved source exists
- `knobBrightness`, integer 0–100; requires the prepared SDK's checked LED symbol
- `followDisplayBrightness`, boolean; independent knob brightness still fades and
  switches off during idle/sleep
- `showLogo`, existing command, available only with the explicit software PNG

`brightness: 0` keeps the remembered normal brightness and enters idle, matching
legacy behavior. Remaining descending host brightness fade commands do not wake
an idle saver. `sleep` always turns the screen off; `wake` and physical input
restore cached surfaces while dim, then fade to the normal brightness. The known
key-release / disabled sleep timer / descending brightness manual-sleep sequence
is preserved. These are N4 behaviors, not generic SDK assumptions.

Normal frames received during idle update bounded in-memory surfaces without
waking. A complete LCD frame is normal display content under the generic frame
contract; host-specific saved-saver selection/suppression belongs to the product
bridge. No worker disk trace or frame-file IPC is introduced. The saved source
must not be confused with streamed host LCD frames when restoring normal content.

`getState` reports submitted intent, not hardware readback: `brightness` is the
remembered normal level; `screenBrightness` is the last submitted screen level
(or null before a write); `sleeping`, `idle`, `sleepDuration`,
`screensaverReady`, `screensaverActive`, `logoActive`, `knobBrightness`,
`followDisplayBrightness` and `knobBrightnessLevel` describe worker state.
Physical `stateVerification` is `unconfirmed`; fake is `simulation`.
Successful control responses also include bounded `idle`, `sleeping`,
`screensaverActive` and `logoActive` hints. Clear/fill responses describe their
normal-cache effect with `normalStateEffect`: `background-only` for the special
legacy idle/wake clear preserving key/strip caches, `replace-all` for ordinary
full-surface replacement, or `surface` for a scoped key/touch update. These hints
allow an owner to journal the actual restore semantics without image snapshots;
they are not physical acknowledgements. Fake global clears always replace all.
Reconnect resets volatile preferences and surfaces. The caller must replay
reviewed settings/content in its new generation. No identity or preference files
are created or changed by the worker.

Input callbacks only publish input and mark activity; USB wake/restore is serviced
by the serialized writer. Idle/animation polling also runs while output is queued,
so a display flood cannot indefinitely suppress timer or input-wake processing.
Actual native calls cannot be interrupted; bounded process termination remains
the owner's final shutdown guarantee.

On Windows the physical process holds the exact legacy
`Local\Hellgato.N4Pro.Bridge` lifetime mutex before SDK import/discovery. It
rejects another active engine, retains ownership across device close/reopen and
releases only after confirmed shutdown. Device IDs use the byte-identical legacy
registry fingerprint without exposing raw serials or importing product code.
The fake backend never obtains this physical lock. Dynamic vendor imports disable
Python bytecode writes before importing the prepared SDK, preserving the frozen
worker dependency inventory instead of adding `__pycache__` files.

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
