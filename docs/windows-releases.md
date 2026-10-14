# Manual Windows releases

Windows releases are published manually. GitHub Actions runs checks and builds;
its publication job is disabled. Automatic release coordination and npm
publication remain deferred. Building the installer does not publish either SDK
package.

## Prepare the source

Use a clean committed checkout of `restarea92/hellgato` with the intended app
changes, release notes and version metadata. Windows uses
`packages/windows-release/package.json`; `app/release.json` and
`installer/version.iss` are generated from it. Changesets maintains Windows and
the fixed SDK package pair independently. Check metadata before packaging:

```sh
pnpm run release:metadata:check
```

The stable tag must be `v<version>` and point to the exact source commit used for
the installer. Confirm the GitHub account, repository, tag and release notes
before publication. Never move an existing release tag or overwrite published
installer bytes; conflicting content requires investigation or a new version.

## Build and verify

Follow [the Windows build instructions](guide.md#build-and-test) with the pinned
dependencies. From the repository root:

```powershell
./scripts/build-windows.ps1 -Python ./.venv/Scripts/python.exe -Iscc <path-to-ISCC.exe>
```

The build emits `dist/Hellgato-<version>-Setup.exe` and its `.exe.sha256` sidecar.
It packages the separated USB worker, offline-installed SDK and existing native
integrations. `-UsePreparedNative` may reuse the two prepared native DLLs when
their sources have not changed.

Check the executable version, embedded source manifest and SHA-256 against the
selected commit and sidecar. Run the checks appropriate to the changes. Hardware
claims must reflect physical observations: normal N4 controls, profile images,
brightness and sleep/wake, USB reconnection, and Stream Deck exit/restart were
verified for this implementation. Hellgato-only exit can leave the last image for
tens of seconds before standby; restarting Hellgato while Stream Deck remains
open can require manual pairing. The full endurance matrix and other hardware
combinations are not verified.

## Publish and confirm

Push the reviewed source and exact stable tag, then create the GitHub release
with its matching notes from `docs/releases/<version>.md`. Upload only the
versioned installer and its matching SHA-256 sidecar. A draft release can be used
to inspect the assets and notes before making it public.

Download both assets from the published release and compare the installer digest
with the sidecar and original build. Confirm that the release tag still resolves
to the selected commit. Update README download links only after public asset
verification. Preserve the previous release for users who need to roll back.

Local profiles, settings backups, diagnostic logs and intermediate build folders
are private working files and must not be included in release assets.
