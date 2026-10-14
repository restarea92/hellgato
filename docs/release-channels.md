# Release channels and explicit public baselines

## Implemented policy and activation boundary

Stable remains the default. The code-owned mapping is:

| Channel | Source branch | Release PR branch | npm dist-tag | GitHub prerelease |
| --- | --- | --- | --- | --- |
| stable | `main` | `changeset-release/main` | `latest` | false |
| beta | `next` | `changeset-release/next` | `beta` | true |

There are three deliberately distinct records:

- Schema 1 `.release/plan.json`: existing stable release intent, unchanged by
  default; an optional reviewed Windows public-baseline record handles prepared
  source versions that were never published.
- Schema 3 `.release/plan.json`: channel-aware release intent. Every selected
  component must carry its exact, previously committed public-baseline record.
- Schema 2 `.release/candidate-plan.json`: artifact-only candidate. It cannot
  authorize publication, even when copied to the public plan path.

Both public-plan formats require an owner-merged canonical release PR, original
repository, exact base/head/merge tree, deterministic Changesets replay, artifact
source identity, manifest digests and immutable-byte checks. A plan file on its
own is never approval. Windows uses the existing `RELEASE_ENABLED` gate for both
channels. Beta Windows releases are prereleases, never GitHub Latest; the stable
download documentation is updated only after a stable Windows release.

npm publishing and GitHub npm finalization remain **unconditionally disabled**
in the real APIs, CLIs and workflow jobs. There is no flag or environment-variable
bypass. The real OIDC transport, publication ordering, retries, registry-byte
readback and GitHub finalizer are implemented behind that hold and exercised
through the same engine with explicit fake I/O. Enabling publication needs a
separately approved code change and the documented Trusted Publisher setup for
`release.yml`. This implementation grants no credentials, OIDC permissions or
repository settings and performs no public writes.

## Reviewed beta and stable-promotion plans

Use Node 24.19.0, pnpm 11.10.0, npm 11.9.0 and frozen dependencies. First inspect
public state and commit the applicable baseline records below with the relevant
changesets. The release PR workflow runs:

```sh
# next: enter or advance beta
node scripts/release-version.mjs --channel beta
# main with a reviewed active beta track: exit prerelease mode
node scripts/release-version.mjs --channel stable
```

Generation requires the exact clean committed source base. The canonical
Changesets action release branch is also accepted only when its checkout is
exactly that target branch's base. The generated plan always records the source
branch, not the temporary release branch. A separately reviewed integration of
the beta track into main precedes promotion; moving versions or prerelease files
by hand is not a substitute for replay or approval.

### Moving a verified beta snapshot to main

Use the explicit source-only handoff instead of manually copying versions or
relabeling the beta plan. Fetch the original GitHub repository's main and next
refs, and select the exact next beta release commit. Main must already be its
ancestor; if main advanced independently, integrate that change on next and
produce a fresh reviewed beta release snapshot first.

```sh
git switch -c release/handoff-next-main origin/next
node scripts/release-handoff.mjs --base <exact-origin-main-sha> --source <exact-origin-next-sha> --reason "Explain why this exact reviewed beta source is ready for stable promotion."
# Commit only .release/channel-handoff.json and open its PR against main.
```

The generator verifies the original origin URL, pinned refs, canonical beta
plan and full deterministic replay. Validation accepts only that exact next
tree plus the one generated handoff record. Commit this source-only PR with a
normal preserving merge, not squash/rebase, so its exact next ancestry remains
verifiable. A stale main/next ref, extra source edit or modified carried plan
fails the handoff. The carried beta plan grants no main release approval.

After the handoff, review fresh public-baseline records in a separate main
metadata PR: sourceVersion must now be the current beta version, and every
public ID/tag/digest must be freshly verified. Give those policy-file changes a
scoped no-release explanation. Main remains in read-only beta source CI during
this step. The release-PR workflow can then generate the separate stable
schema-3 pre-exit plan. No handoff record exempts later ordinary source or
version edits from impact checks, and no handoff authorizes publication.

The first beta runs the actual Changesets CLI `pre enter beta`, then `version`.
Advancing beta runs `version`; no new inputs means no new beta intent. Promotion
runs `pre exit` and `version`. Pinned Changesets 3.0.3 archives consumed inputs in
`.changeset/pre/*.md` and uses only `mode`/`tag` in `pre.json`. Plans record and
replay that exact format. Orphan state, old-format fields, unknown channels,
duplicate archived IDs, changed source versions, mixed source edits, forged
baselines and noncanonical generated files are rejected.

Review the generated version changes, notes and exact public observations, then
commit the release PR. The approved build uses:

```sh
node scripts/release-version.mjs --check --head <exact-release-head>
node scripts/sync-release-metadata.mjs --check --plan .release/plan.json
node scripts/build-npm.mjs --plan .release/plan.json
node scripts/pack-npm.mjs --destination dist/npm --plan .release/plan.json
node scripts/verify-npm.mjs --artifacts dist/npm --plan .release/plan.json
```

The fixed SDK pair remains equal, and the packed facade depends on the exact
core version. Beta source publishConfig is `beta`; promotion restores `latest`.
These source changes do not themselves move registry tags. The empty-cache
consumer installs the exact tarballs outside the repository and verifies the
public runtime and type entry points.

For a Windows-only plan, the Windows staging script uses `--consumer-plan
.release/plan.json` instead of `--plan` for SDK build/pack/verification. It checks
the committed fixed SDK pair without inventing npm release intent, including an
unchanged beta SDK. The resulting private SDK manifest cannot enter a publisher.

## Ordinary code PRs and artifact-only candidates

Read-only build context is separate from publication approval. An ordinary PR
whose base is next or post-handoff main, or a build on either branch with
active beta prerelease state, uses `--source-channel beta`. A lingering prior plan is deliberately ignored.
The source-CI SDK manifest is private, has no plan digest, and cannot accept an
approval file. Checks and Windows build select this context from their verified
branch/event inputs. Canonical release PR builds and approved reusable release
builds select an explicit public plan instead. In an npm-only version PR the
Windows job remains a source consumer; its unchanged product version is never
misinterpreted as selected Windows release intent. Stable source manifests use
no plan digest unless the exact plan was explicitly passed.

To experiment with versions without public release intent, use a clean next
checkout without a public Windows-baseline override:

```sh
node scripts/release-candidate.mjs --channel beta
# Review and commit generated candidate files.
node scripts/release-candidate.mjs --check --head <exact-candidate-head>
node scripts/build-npm.mjs --candidate-plan .release/candidate-plan.json
node scripts/pack-npm.mjs --destination dist/npm --candidate-plan .release/candidate-plan.json
node scripts/verify-npm.mjs --artifacts dist/npm --candidate-plan .release/candidate-plan.json
```

`release-candidate.mjs --channel stable` tests pre-exit promotion on the same
artifact-only track. Windows metadata/manifest commands accept the explicit
candidate plan for builds only. Candidate artifacts cannot satisfy public
approval and cannot be made publishable by relabeling their channel or schema.

## Windows display and numeric versions

Display SemVer stays `X.Y.Z-beta.N`; Windows file/installer comparison uses
`X.Y.Z.(N+1)`. Stable uses `X.Y.Z.65535`. Core fields are limited to 0–65535 and
beta N to 0–65533. Therefore beta.9 < beta.10 < matching stable < following
patch beta. Inno consumes the generated numeric version; product metadata
preserves the display version and explicit channel.

Unit and mocked release tests validate numeric ordering, provenance, channel
selection, source-CI isolation, create/resume behavior and immutable retries.
They do not replace actual Windows install/upgrade/downgrade or physical-device
validation. Use the Windows CI artifact and the hardware checklist before
approving a release that depends on those behaviors.

## Windows source versus public version

Prepared source `0.1.2` and public Windows `0.1.1` are different facts. Without
an explicit record, a normal patch remains source `0.1.2 -> 0.1.3` and requires
its original public predecessor. The legacy no-bump `0.1.1 -> 0.1.2` bootstrap
remains separate and restricted.

To skip an unpublished prepared source version, verify the relevant public
channel resolver, release ID, dereferenced tag, installer/checksum and raw
manifest bytes. Commit `.release/windows-public-baseline.json` before generating
the release plan:

```json
{
  "schemaVersion": 1,
  "component": "windows",
  "channel": "stable",
  "sourceVersion": "0.1.2",
  "version": "0.1.1",
  "tag": "v0.1.1",
  "releaseId": 123,
  "releaseSha": "<verified 40-character tag commit SHA>",
  "installerSha256": "<verified downloaded installer SHA-256>",
  "manifestSha256": null,
  "reason": "Explain why the unpublished prepared version is being skipped."
}
```

This is a template, not verified public data. Schema-3 plans also support an
actual beta public baseline with its matching channel/version/tag. Public
version must be no greater than source, and target must strictly increase.
A null manifest digest is allowed only for legacy stable 0.1.0/0.1.1 when no
manifest actually exists. Other versions require the downloaded manifest digest.
All identity and byte observations must still match at publication time.

The plan copies the committed record into `components[].publicBaseline` while
`oldVersion` remains the actual source version. There is no arbitrary `--from`
override for a normal release, and the record cannot be combined with bootstrap.
A completed exact-byte retry is a no-op even after a newer release appears;
it never promotes the older release to Latest. An incomplete retry rechecks the
same reviewed public baseline and monotonic target, including after upload.
Update or remove the record in a reviewed source PR before a later transition.

## npm public observations and readback

Schema-3 npm plans require `.release/npm-public-baseline.json` committed before
version generation. Its exact shape is:

```json
{
  "schemaVersion": 1,
  "component": "npm",
  "sourceVersion": "0.1.0",
  "packages": [
    { "name": "@hellgato/core", "latest": null, "beta": null },
    { "name": "hellgato", "latest": null, "beta": null }
  ],
  "reason": "Describe the verified public tags and the intended channel transition."
}
```

Use null only for a verified absent tag. Each present tag is an object with
`version` and exact `sha512-...` `integrity`. The example makes no assertion that
these real packages or tags are absent. Both tags for both packages are checked
before any write and again during publication. Beta preserves latest exactly;
stable preserves beta exactly. Targets must exceed both reviewed observations.

The shared publication engine publishes core first and facade second, passes an
explicit channel tag, disables scripts and requests provenance. It downloads
present target tarballs from the official exact-version npm URL with no
redirects, bounded size and a timeout, checks SHA-512 against registry metadata,
then checks SHA-256, SHA-512 and byte length against the approved artifact.
Metadata alone cannot confirm publication or permit an immutable retry. Identical
bytes reuse the prior isolated-consumer proof; no package code runs in the
publisher's credential context. Missing, conflicting, changed-tag or partial
results fail closed and retain the original artifacts for inspection/retry.
