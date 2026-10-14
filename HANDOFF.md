# Windows handoff: 2026-10-14

Continue on `feat/device-neutral-sdk` in
[`restarea92/hellgato`](https://github.com/restarea92/hellgato), draft
[PR #5](https://github.com/restarea92/hellgato/pull/5). The IPC candidate remains
experimental: key actions and touch-strip images work, but **every physical key
icon is black, including after changing profiles and returning**. Physical parity
is not verified. The ordinary installer still uses `legacy-compatibility`.

## Source and completed changes

The original cloud bundle was hash-verified and imported without changing its
commit: `6cbc736f8ae5fab26bce354b56cd02fe8b6860b5`, parent
`15785fa177720d6ad054406eecbf4dd4a538258e`. It was pushed unchanged. The bundle
SHA-256 is `84bef539c3583348f67baa1f36f23ee4d13cd7e1607deeb46ee9fa909cc4bb84`.

The subsequent Windows fixes are committed in order:

| Commit | Change |
| --- | --- |
| `3c727cf92797a178b263b64c54087546e38632d8` | Windows validation handles UTF-8 sources and logical/short-path aliases; test fixtures preserve the shared dependency junction. |
| `fc337f4bb545436f6e009dc5d14216907ef42d83` | Deep profile backup/restore works with Windows long paths disabled, retaining reparse and containment checks and interrupted-restore recovery. |
| `777bd21e42103f0a5f4ef34596f80f556eccf569` | Device scrollbar is hidden without overflow, uses the dark theme, and preserves selection/scroll position. |
| `2be7b0fd4ae7fd2d5ad030525a9926a4c380ecb2` | Product IPC output waits for fresh host approval and device setup, submits one operation per worker reply, and bounds/coalesces pending full frames without losing partial/control order. |

The scrollbar installer was installed locally and the user confirmed the fix.
Displayed Windows version remains `0.1.2`; determine source identity from the
build manifest rather than that unchanged display version. The pairing fix was
tested in a separate portable candidate, not installed as the default engine.

Locally retained artifacts (not uploaded as releases):

| Artifact | Source | SHA-256 |
| --- | --- | --- |
| `Hellgato-Ipc-Candidate-2be7b0fd4ae7.zip` | `2be7b0f` | `6ee4b986d60c0e98b81bed420329655506356d571e3633fd64eb7ce9664596aa` |
| `Hellgato-0.1.2-Setup.exe` installed on this PC | `777bd21` | `12d6ad8f5f6c981d463ccdf29936bd4342355145f3eb51fe69d989e62f2befd3` |

This handoff adds documentation only. The physical observations below belong
to the `2be7b0f` artifact; a new build must bind its own exact source and hashes.

## Validation evidence

| Scope | Result and limit |
| --- | --- |
| Cloud source `6cbc736` | 235 Node tests passed. Python: 401 methods passed, 14 deferred methods in 13 skip records. |
| Windows Python after `fc337f4` | 425 methods: 424 passed, one intentional non-Windows subprocess case skipped, no failures. Both installed Stream Deck startup-plan methods ran and passed on this PC. |
| Windows Python quality | Ruff coverage: 43 canonical files; strict mypy: five boundary files; negative consumers and 12 quality-gate tests passed. OS `LongPathsEnabled=0` was preserved. |
| Scrollbar change | Nine UI tests and Python quality passed. Empty/single/three-row lists hide the scrollbar; overflowing lists show it; resize/selection checks passed. User confirmed the installed single-device view. |
| Windows Node after `2be7b0f` | 247 tests: 246 passed, one intentional Windows skip, no failures. Related candidate tests: 33 total, 32 passed, one skip. |
| Pairing fix gates | Python candidate 40/40; Python quality 12/12; TypeScript and architecture checks passed. Fresh packed SDK staging passed. |
| Frozen `2be7b0f` candidate | Eight self-test checks passed with the explicit fake backend. Receipt states physical hardware was not verified. Manifest verification and archive hashing passed. |
| GitHub CI | Original `6cbc736` Windows checks/build exposed the locale/path failures fixed above. Do not reuse that failed run as evidence for the later commits. Check the new PR runs and exact-head Windows dispatch before using their artifacts. |

Full local logs remain in ignored `work/` on the original PC. They are not a
portable CI receipt. No public npm publication or release was performed.

## Actual device trial

Tested code/artifact source: `2be7b0fd4ae7fd2d5ad030525a9926a4c380ecb2`.
Windows 11 Pro x64, version `10.0.26200`; Stream Deck `7.6.0.23012`.
The worker admitted N4 Pro E firmware `V4.N4 Pro E.02.010`; this does not broaden
the firmware policy. Settings were `stripMode=frame`, `screensaverMode=stream`.

- Hellgato was fully closed; the portable supervisor owned the IPC worker.
  Stream Deck was the only ordinary app running. USB stayed connected during
  the display test.
- Initial pairing requires the Stream Deck network-device UI: `127.0.0.1`,
  port `5343`. Discovery/seeing the PC app alone does not demonstrate the
  physical device is connected.
- The earlier `777bd21` trial failed with `OUTPUT_OVERFLOW` during pairing.
  The entered address/port were correct. After `2be7b0f`, manual pairing
  succeeded and that overflow was not reproduced in this trial.
- The user confirmed physical key actions work and the touch-strip image is
  visible. All key icons remain black after switching existing profiles and
  returning. Earlier replies saying everything worked referred to the PC app
  and were corrected; they are not hardware passes.
- The native knob-brightness UI row was absent. This isolated supervisor does
  not load the ordinary product's native UI extension. UI/control integration
  and the complete dial/touch-input recipe remain unverified.
- Before USB removal, the session was `ready` with zero diagnostics. Writes
  were `submitted/unconfirmed`; there was no physical image acknowledgement.
- At departure, the device disappeared. Recovery used its three-attempt budget
  and stopped with `DEVICE_NOT_FOUND`, with one clean stop recorded. The
  supervisor, Node/worker descendants, and ports 5343/5344 were absent afterward.
  The original Stream Deck app was left running; no forced app shutdown occurred.

The final aggregate counters were 22,601 frame writes, 121 control writes,
four restoration writes, 43 observed inputs and 35 forwarded inputs. Eight
inputs occurred before host delivery was available. These totals do not identify
which display surface was written and do not establish USB latency or display
parity.

## Next investigation: black key images

The cause is still unresolved. Both engines use
120-by-120 RGB key images, a temporary PNG and `set_key_image(index + 1)`.
The candidate already uses a private writable working directory for the vendor
SDK. Its scheduler only coalesces matching surfaces; LCD frames do not coalesce
away key frames. Profile switching did not recover icons, weakening the theory
that only initial connection frames were lost.

Add privacy-safe surface-level received/coalesced/submitted counters and worker
key-cache counts. If needed, include dimensions, format, aggregate brightness
and the last writer type. Distinguish missing key JPEGs, black payloads and later
LCD/clear overwrites before changing image handling. Current LCD handling clears
the key cache and writes ten LCD crops; stream-screensaver behavior also needs
comparison with the legacy path. These are hypotheses, not confirmed causes.
Never log image bytes, profiles, device identities or credentials. Make a new
committed, manifest-bound trial for instrumentation; do not edit a live bundle.

## Continue on another Windows PC

Use a fresh clone rather than resetting an existing modified checkout:

```powershell
git clone --branch feat/device-neutral-sdk --single-branch https://github.com/restarea92/hellgato.git hellgato-handoff
Set-Location hellgato-handoff
git status --short --branch
git log -6 --oneline
```

Read this file, applicable `AGENTS.md`, and the local `repo-writing` and
`codebase-conventions` skills. Preserve any existing checkout and personal
profiles. Another PC has its own saved identity/profiles; the candidate requires
an already registered device and does not create or migrate that identity.

Prefer the **exact-head** Windows build's `windows-ipc-candidate-*` and
`windows-installer-*` Actions artifacts, verifying their source manifests,
checksums and frozen self-test receipt. PR artifacts can bind a synthetic merge
SHA; inspect the actual checkout instead of assuming it equals the PR head.
No binaries, settings, private backups or personal images are committed here.

For a local build, follow the pinned toolchain in
[the Windows workflow](.github/workflows/windows-build.yml): Python 3.13.7,
Node 24.19.0, pnpm 11.10.0, npm 11.9.0, uv 0.12.23 and Inno Setup 6.7.3.
After frozen workspace installation and locked Python environment setup:
close ordinary Hellgato and any candidate before the Windows tests; launcher
mutex fixtures intentionally fail if another launcher owns the real mutex.

```powershell
pnpm run prepare:cora
pnpm run typecheck
pnpm run check:architecture
node --test test/*.test.mjs
& ./.venv/Scripts/python.exe scripts/check-python.py
& ./.venv/Scripts/python.exe -m unittest discover -s test -p 'test_*.py'
./scripts/build-windows.ps1 -Python ./.venv/Scripts/python.exe -Iscc 'C:\Path\To\Inno Setup 6\ISCC.exe'
```

Build from clean committed source. Do not regenerate versions or public release
plans merely to build this branch. The ordinary installer retains the previous
N4 engine. On the destination PC, confirm that engine works before testing IPC.
If startup preparation differs, use the ordinary installer from the same build
and its explicit setup flow; the candidate does not patch or elevate the host.

For the isolated trial, follow [the IPC instructions](docs/compatibility/ipc-trial.md).
Close both ordinary apps first, start `Run experimental test.cmd`, type `TEST`,
and wait for the verified backup. Open Stream Deck only when prompted, keep
Hellgato closed, then pair manually if needed. Inspect the **N4's physical**
button/touch displays. Avoid USB reconnects until the basic display issue is
diagnosed. Stop the candidate before starting ordinary Hellgato again.

## Remaining scope and safeguards

Follow [the full hardware recipe](tests/hardware/README.md): ten keys/four dials,
input ordering, all images/touch regions, sleep/wake/screensaver, both brightness
controls, first/repeat pairing, 20 USB and 20 host cycles, crash/contended cleanup,
reboot/sign-in/sleep-resume, preservation/rollback and a full workday compared
with the prior product. None of these comprehensive gates is completed here.

The latest private backup on the original PC has 53 verified files, including
49 profile files, under `%LOCALAPPDATA%\Hellgato-Ipc-Candidate\2be7b0fd4ae7fd2d5ad030525a9926a4c380ecb2\20261014T080040-12248\backup`.
Keep this, earlier backups and rollback binaries private and intact. Do not
restore another PC's state by blindly copying this folder. Normal rollback is
stopping the candidate and starting the ordinary app; use the documented
verified restore command only if restoration is needed, with both apps closed.

npm automation is implemented, including ordered publication/readback and beta
promotion validation, but real publishing and GitHub npm finalization remain
unconditionally disabled in workflow/API/CLI code. See
[release channels](docs/release-channels.md) and
[release setup](docs/release-setup.md). This handoff does not authorize enabling
publication, authentication/access changes, merging, a public release, or Rust
porting. Keep PR #5 draft and retain the production engine until actual parity.
