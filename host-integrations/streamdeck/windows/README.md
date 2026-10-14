# Windows Stream Deck host boundary

`python/hellgato_host` is the canonical implementation for host discovery,
structural compatibility, startup plan/ownership, native readiness, and optional
native UI loading. The old `app/n4` helper names are import aliases to these same
module objects, retained for the legacy product and its test patch points.
The native C++ implementations and isolated fixtures are canonical under
`native/startup` and `native/ui` in this host boundary. The old repository-level
`native/hellgato-startup` and `native/hellgato-ui` Python/CMake entries are thin
compatibility launchers, preserving existing build commands and output paths.

## Read-only candidate readiness

Source entry (usable from any working directory):

```text
python <repo>/host-integrations/streamdeck/windows/python/inspect_readiness.py inspect --json --expected-executable "C:\Program Files\Elgato\StreamDeck\StreamDeck.exe" --expected-startup-dll "C:\candidate\native\version.dll"
```

Write one bounded JSON request to stdin and close stdin:

```json
{"schemaVersion":1,"requestId":"caller-generated-nonce","targetId":"streamdeck-plus-ten-key","profileId":"mirabox-n4-pro-ten-key"}
```

The tool emits exactly one JSON response. `ready: true` includes those same
identifiers, `preparation: ["streamdeck-plus-ten-key"]`, and `host` with the actual
`pid`, decimal-string `creationTime` (Windows FILETIME), `executableDigest`
(SHA-256), and `executablePath`. A failed inspection returns `ready: false`, an
empty preparation list, and `error: {code, message}`. Native startup state is
included when available. A completed negative inspection exits 0; invalid CLI or
request framing exits 2. The request cannot supply a PID, assert readiness, change
paths, or ask the helper to install/prepare/inject. Unknown target/profile IDs are
rejected, even if they describe a superficially similar layout.

The SDK caller must spawn a fresh invocation before opening ports and on each
new host connection/reconnect, verify the nonce/schema/target/profile, enforce a
timeout, and never cache a successful response. The reference DLL path must come
from the verified candidate bundle, not from an untrusted request or merely the
installed ownership manifest.

The inspector checks all of the following without writing files or host memory:

- Exactly one current StreamDeck.exe, OS-reported PID, creation time, and expected
  resolved executable path; identity is rechecked after inspection
- Fresh executable SHA-256 and structural startup plan generation
- Exact Hellgato ownership manifest, unchanged installed files, packaged-reference
  DLL equality, and byte-for-byte equality with the freshly generated plan
- Native readiness record for this exact PID/creation time, ready state with no
  error, loaded executable base, and the exact installed shim's loaded path
- Actual live five-column/two-row keypad and four-encoder descriptors plus exact
  coordinate vectors through read-only process access
- Stable native status, executable, reference DLL, and installed files throughout
  the check

The result is point-in-time compatibility evidence, not a security attestation
against a hostile local process or proof of a physical-device test. A host can
exit immediately after any inspection. The candidate still needs its session
lifetime/connection gating, and genuine Stream Deck Plus coexistence remains
unsupported. Optional Qt native UI loading is not part of this inspection.

## Frozen packaging

Add `host-integrations/streamdeck/windows/python` to PyInstaller `pathex` and
collect `hellgato_host` submodules, including `pefile`. A dedicated executable may
use `python/inspect_readiness.py` as its entry point. The candidate supervisor can
call `hellgato_host.readiness.main(["inspect", ...])` for `--inspect-host` before
entering any supervisor/USB path. Neither route needs the GUI or Mirabox runtime.
The legacy frozen product also preserves the canonical Python source tree so its
`app/n4/_host_compat.py` aliases resolve from `sys._MEIPASS`, independently of cwd.

Tests in `test/test_host_readiness.py` and `test/test_host_boundary.py` cover
readiness fail-closed behavior, bounded protocol, live-memory fixtures, aliases,
and a simulated frozen root. Real frozen Windows and physical host trials remain
separate validation stages.
