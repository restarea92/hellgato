# Release operations

## Versions and approval

Changesets is the only version decision tool. `@hellgato/core` and `hellgato`
form a fixed npm group; the private `@hellgato/windows-release` metadata workspace
versions Windows independently. The root workspace version is not a release.

Windows reads `packages/windows-release/package.json`; the metadata generator
produces `installer/version.iss` and `app/release.json`. The Inno installer and
packaged application both consume these files. Do not edit generated versions.
Run `pnpm run release:metadata:check` to reject drift, or
`pnpm run release:metadata` to regenerate after a legitimate version operation.

A normal code PR includes a changeset. `app/cora/**` and shared upstream
preparation affect both release units even though they are outside `packages/`.
`pnpm run release:impact --base <base-sha>` checks the complete PR diff.
Unknown paths/workspaces fail closed. A documented no-release exception must be
reviewed by the owner; an unreasoned label is insufficient. Before 1.0, an
incompatible npm API change is a **minor** release by project policy; the author
must choose minor explicitly. Changesets does not reinterpret a major bump.

On main, `release-pr.yml` uses the pinned Changesets version action to generate a
draft `changeset-release/main` PR containing versions, changelogs, exact Windows
notes, lockfile and `.release/plan.json`. The plan records the main base SHA,
consumed changesets, selected components, old/new versions, tags and generated
file hashes. It is not another version source. The action dispatches both Checks
and Windows build at the exact PR head because bot-created events alone are not
a reliable CI trigger. No automatic merge is enabled.

**The repository owner merging that release PR is the publication approval.**
The publisher verifies the author, branch, same-repository base, merged state,
owner identity, exact merge SHA, reviewed tree and first parent. A plan prepared
against older main fails until regenerated. Ordinary main pushes, tag pushes,
GitHub release events and unpublished npm versions never constitute approval.

## Current adoption state and first releases

The implementation ships with publication disabled: repository Actions variable
`RELEASE_ENABLED` must equal `true` before any public write. Builds and dry-run
validation still execute. A green dry run does not prove npm OIDC permissions or
live publication behavior.

Prepared Windows 0.1.2 is retained, with runtime version drift fixed; public
Windows 0.1.1 download links remain valid until a new release is confirmed. The
old successful 0.1.2 build is not a releasable artifact. npm source versions remain
0.1.0; both npm names currently publish 0.0.1-beta (latest/beta), and core also has 0.0.0-stage. No 0.1.0 is public. Registry reads do not prove ownership or authorize first stable publication.

Use a separate, owner-authored `release/bootstrap-*` PR for an explicit first
release plan, with the current candidate version unchanged and the actual prior
public version recorded. The bootstrap plan must be validated and reviewed just
like a generated plan. Do not add a patch changeset merely to move already
prepared Windows 0.1.2 to 0.1.3. Do not create placeholder npm packages. Existing
same-name/version registry content must match exactly or be investigated.

After obtaining the separate initial-release approval, generate from a clean main
checkout on the owner-authored bootstrap branch:

```sh
# Choose one independently approved component:
pnpm run release:version --bootstrap windows --from 0.1.1
# Or, after confirming the existing npm beta baseline:
pnpm run release:version --bootstrap npm --from 0.0.1-beta
```

Commit the generated plan and files, run full plan validation, then open the
bootstrap PR. The source candidate version does not increase. Changed registry
baseline or conflicting existing version stops publication for review.

See [setup checklist](release-setup.md) before activating publication. First
Windows publication, npm name/ownership bootstrap and persistent publisher
access each require their own explicit approval.

## Build, publish and confirmation

`release.yml` pins the approved main merge SHA for every checkout. Windows keeps
the Windows 2022 / Python 3.13.7 / native DLL / PyInstaller / pinned Inno 6.7.3
pipeline, migration checks and CORA tests. npm uses Node 24.19.0, pnpm 11.10.0,
Changesets 3.0.3 and npm CLI 11.9.0. Dependency installation is frozen; no release
build uses a potentially untrusted dependency cache.

All selected builds must succeed before the first public write. Windows-only
never publishes npm, including unpublished 0.1.0; npm-only does not publish an
installer. Both npm packages are packed by pnpm, which converts source
`workspace:*` into the exact published core version. Tarballs are inspected and
installed together into an isolated consumer outside the repository for import
and start/stop lifecycle tests. Privileged publication never repacks or executes
package lifecycle scripts.

Artifacts have exact names and a manifest binding repository, release SHA, plan
digest, original build run/attempt, versions, filenames and file digests. npm also
records SHA-512 integrity and the verified tarball file list. The upload action's
artifact ID is bound externally after upload; public release manifests retain
that ID and source provenance. Artifacts are retained for 90 days, subject to the
repository's retention policy. Downloads select IDs, not “latest successful”.

npm direct publishing uses OIDC on the hosted `npm-publish` job in top-level
`release.yml`, with `id-token: write` only there. Core is confirmed before facade
is published. No `NPM_TOKEN` is introduced. GitHub finalization is a separate job
without npm OIDC. npm tags are `@hellgato/core@VERSION` and `hellgato@VERSION`;
its GitHub release has `make_latest=false`.

Windows stable tags remain `vMAJOR.MINOR.PATCH`. The resolver reads every Releases
API page, excludes draft/prerelease/npm releases, requires installer and checksum,
validates modern component manifests and chooses the numerically highest stable
SemVer. Only 0.1.0 and 0.1.1 have explicit legacy manifest exemptions. It never
uses `/releases/latest`. API, pagination and malformed manifest failures abort.

Publication confirms downloaded public bytes and registry integrity/dist-tags.
The workflow summary reports each selected component separately; partial success
is not an overall success. Confirmed Windows releases produce a narrow download
documentation PR. Documentation never points at a merely prepared version, and
that PR is not automatically merged.

## Safe retry and recovery

The repository-wide `hellgato-publication` concurrency group serializes normal
runs, rebuilds and recovery without cancelling active publication.

- Same tag at the same SHA is accepted; a different target stops. Tags never move.
- Same npm version/integrity is confirmed and skipped; different bytes stop.
- Core success/facade failure resumes only the missing facade after verifying core.
- Identical fully published Windows output is a no-op; published assets never change.
- Only a matching automation-owned draft can resume missing uploads. Unknown
  drafts, unexpected assets or conflicting content stop for investigation.
- HTTP 403/429/5xx and malformed responses are failures, never “not published”.

For publish-only recovery, dispatch **Release approved components** on main with
`confirm=true`, the exact `release_sha`, original `source_run_id`, original
`source_run_attempt`, and artifact IDs for **every selected component**. Original
successful build jobs are checked even if that run later failed publication.
Do not substitute recovery run IDs for original source IDs. The compatibility
**Windows release recovery** workflow delegates to this same entry point and is
suitable only for Windows-only plans.

If an artifact expired, omit all source/artifact fields to request a full rebuild
of the exact already-approved SHA. All selected builds and verifications run
again. If already published bytes differ from the rebuild, recovery stops rather
than overwriting them; use retained verified public artifacts or a new version.

A new higher Windows release blocks an old release from moving Latest backward.
After a published bug, add a fix changeset and release a new version. Deletion,
unpublish, tag force-push, access changes and automatic unknown-draft cleanup are
not recovery mechanisms.

## Local validation

```sh
pnpm install --frozen-lockfile
pnpm run release:metadata:check
pnpm run release:impact --base <base-sha>
pnpm run prepare:cora
pnpm test
pnpm run build:npm
node scripts/pack-npm.mjs --destination dist/npm
node scripts/verify-npm.mjs --artifacts dist/npm
python -m unittest discover -s test -p 'test_windows_release.py' -v
```

Windows CI additionally runs the complete Python suite, installer migration
PowerShell tests, native builds and actual installer packaging. Offline fixtures
cover approval, impact, version drift, selection, tampering, pagination and retry
failure modes. OIDC setup, bot PR required-check attachment, physical Windows
hardware and a live publication are distinct checks, not claims implied by unit
test success.
