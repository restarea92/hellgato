# Changelog history and provenance

Historical entries were reconciled on **2026-10-15** against GitHub releases/tags, repository changes and the public npm registry. All dates below are UTC. Package changelogs contain curated changes; the original `docs/releases/*.md` files remain unchanged for detailed behavior, compatibility evidence, upgrade instructions and limitations.

## Changesets adoption boundary

- [c823d2e](https://github.com/restarea92/hellgato/commit/c823d2e2647a384d9ef8ae4aeb7360eb004549a8) introduced Changesets, component changelogs and the independent Windows version marker on **2026-10-13 at 23:04:57Z**. Its parent was [f6f6641](https://github.com/restarea92/hellgato/commit/f6f664127589a6a4a8a92079a1bcfa11b73b02ca).
- [PR #3](https://github.com/restarea92/hellgato/pull/3) merged that work into `main` at [9bb3295](https://github.com/restarea92/hellgato/commit/9bb32954a7167877c4b620a7658e814b7cdc3b96) on **2026-10-14 at 00:00:29Z**.
- At adoption, Windows `0.1.1` was published, Windows `0.1.2` was only prepared, and npm source manifests were `0.1.0` while the public functional npm version was `0.0.1-beta`. The initial Windows `0.1.2` icon-only preparation was subsequently expanded before publication; it is not a separate release.
- Windows `0.1.2` and `0.1.3` were published manually **after** adoption. Their entries are included to reconcile the historical changelog with what actually shipped. They are not described as pre-Changesets releases or npm publications.
- The SDK `0.1.0` entries describe only the prepared source baseline through `f6f6641`. The later device-neutral contracts, session composition, sidecar and typed-SDK changes stay in the pending changesets. Source version fields alone are not publication evidence.

## Published npm history

The complete [core registry record](https://registry.npmjs.org/@hellgato%2Fcore) and [facade registry record](https://registry.npmjs.org/hellgato) showed these versions at verification time:

| Package | Version | Published at (UTC) | Status |
| --- | --- | --- | --- |
| `@hellgato/core` | `0.0.0-stage` | 2026-09-30T12:43:19.847Z | Published placeholder; manifest and README only |
| `@hellgato/core` | `0.0.1-beta` | 2026-09-30T12:48:17.590Z | Functional beta; `latest` and `beta` tags |
| `hellgato` | `0.0.1-beta` | 2026-09-30T12:44:16.481Z | Facade beta; `latest` and `beta` tags |

Neither registry record contained `0.1.0`. Both source manifests were advanced to `0.1.0` by [8320e8b](https://github.com/restarea92/hellgato/commit/8320e8bef21d7419a3858c88e28ea5a0578113ca) on 2026-10-13. That prepared version is explicitly marked unpublished in both changelogs.

Published artifact contents were read directly and their SHA-512 integrity values verified against the registry. No package code or installation scripts were executed:

- [Core staging tarball](https://registry.npmjs.org/@hellgato/core/-/core-0.0.0-stage.tgz): `stub: true`, no implementation; registry SHA-1 `4ab940bfd606aa1219d6e6c8f7c86472be961bf3`.
- [Core beta tarball](https://registry.npmjs.org/@hellgato/core/-/core-0.0.1-beta.tgz): bridge/profile implementations, vendored runtime and licenses; registry SHA-1 `429e30c0c8b6584fc375147cce22a88ccda24e67`.
- [Facade beta tarball](https://registry.npmjs.org/hellgato/-/hellgato-0.0.1-beta.tgz): two core re-exports and an exact `@hellgato/core: 0.0.1-beta` dependency; registry SHA-1 `9d719f3922d2932a8cf705bd324db960f281179c`.

The registry manifests do not contain `gitHead`. Consequently, these entries are grounded in the actual published artifacts and do not claim an exact publishing commit. In particular, the Windows `v0.0.1-beta` tag predates the [npm packaging commit](https://github.com/restarea92/hellgato/commit/462e97e0e229739f64d97c72c49bc7357bc4ea92), so that tag must not be treated as an npm source tag.

The prepared core history is supported by these source changes:

- [Display-command handling](https://github.com/restarea92/hellgato/commit/a101a8ebd8d3e2ffca46b20e9feb8bedf3678f28).
- [Full-LCD, sleep-duration and display events](https://github.com/restarea92/hellgato/commit/00eb84070cef6704f9f92db33b0fd14b87f1404f).
- [Firmware-write rejection and socket cleanup](https://github.com/restarea92/hellgato/commit/5fc8348bf623e449b2cd767f87fda1f808824522).
- [Server diagnostic forwarding](https://github.com/restarea92/hellgato/commit/482a47416438b87eaa9e111e07d997b572a373e4).

## Published Windows history

The [release inventory](https://api.github.com/repos/restarea92/hellgato/releases?per_page=100) contained eleven non-draft releases, each with its versioned installer and SHA-256 companion. Publication times come from `published_at`, not tag creation or commit dates. The private `@hellgato/windows-release` package is a version marker for this app, not an npm release.

| Version | Published at (UTC) | Release tag commit |
| --- | --- | --- |
| [0.1.3](https://github.com/restarea92/hellgato/releases/tag/v0.1.3) | 2026-10-15T00:26:53Z | `34c5594c5cebc7a4e1c4f170bcbd88bd9158e908` |
| [0.1.2](https://github.com/restarea92/hellgato/releases/tag/v0.1.2) | 2026-10-14T13:27:40Z | `7bc62ff1c12a394ee31a2c4aa81ffc3f56f9a7c7` |
| [0.1.1](https://github.com/restarea92/hellgato/releases/tag/v0.1.1) | 2026-10-13T08:34:38Z | `b993f40374a4e899bca8bf7dcca8ae1a8f977dc7` |
| [0.1.0](https://github.com/restarea92/hellgato/releases/tag/v0.1.0) | 2026-10-13T08:18:22Z | `85058a729cbd9f56cdaf3ea833e3c00b01703e86` |
| [0.0.7-beta](https://github.com/restarea92/hellgato/releases/tag/v0.0.7-beta) | 2026-10-12T16:28:03Z | `00eb84070cef6704f9f92db33b0fd14b87f1404f` |
| [0.0.6-beta](https://github.com/restarea92/hellgato/releases/tag/v0.0.6-beta) | 2026-10-02T05:01:58Z | `902b6d8e142b08e5203f615d1dfe702948a73201` |
| [0.0.5-beta](https://github.com/restarea92/hellgato/releases/tag/v0.0.5-beta) | 2026-10-01T17:10:50Z | `8366a4dbf325269055b5992f6fbf1c7fe41422e4` |
| [0.0.4-beta](https://github.com/restarea92/hellgato/releases/tag/v0.0.4-beta) | 2026-10-01T14:40:30Z | `7a4637c14e72daa30aa7ac24770574a46411a96e` |
| [0.0.3-beta](https://github.com/restarea92/hellgato/releases/tag/v0.0.3-beta) | 2026-10-01T14:26:55Z | `82103e7d408d03796d4a811f08407635c2d37165` |
| [0.0.2-beta](https://github.com/restarea92/hellgato/releases/tag/v0.0.2-beta) | 2026-10-01T11:55:52Z | `328d839cf5955a223c9f9a389a6a5896a5aec897` |
| [0.0.1-beta](https://github.com/restarea92/hellgato/releases/tag/v0.0.1-beta) | 2026-09-30T10:55:54Z | `c15529d56c25925028c2452727ee70b5444d63f9` |

The [`v0.1.2` annotated tag](https://api.github.com/repos/restarea92/hellgato/git/tags/302b59f74ba39c63877fc527bb0d8f22a0dfed9d) resolves to `7bc62ff1`, even though the release's `target_commitish` is the mutable name `main`. Tag resolution, not that name, identifies the historical source. The short-lived revert/resume commits around `0.1.3` do not constitute additional releases.

Changes were assigned using the published release bodies, preserved release notes and commit history between the corresponding tag commits. Repeated claims that earlier fixes were retained and repeated upgrade instructions were omitted from the summaries. The profile-export confirmation merged before `0.0.7-beta` is included with its source link even though the old release notes did not mention it.

## Continuing the changelogs

Keep plain `## <version>` headings so Changesets can prepend new sections and Windows note generation can extract exactly one version. Dates and publication/preparation status belong in the body. A generated version section records prepared changes; its existence alone never proves publication.

The current SDK heading order is `0.1.0` (prepared, unpublished), then `0.0.1-beta` (published), plus `0.0.0-stage` for core only. Windows starts at published `0.1.3` and continues back through `0.0.1-beta`.

With the changesets pending at the verified main snapshot [5308656](https://github.com/restarea92/hellgato/commit/5308656700fd88acadada262442d781b7eadc592), version preparation is expected to prepend SDK `0.2.0` and Windows `0.1.4`, preserving these historical sections below. Those next versions are examples of the pending plan, not releases created by this history cleanup. The screensaver full-frame fix in that snapshot belongs to the next Windows version, not published `0.1.3`.

### Reconcile already-shipped pending Windows notes

Three pending changesets from the manual-release transition were still byte-identical to the files at the actual Windows `0.1.2` tag commit. Repeating their app changes under the next Windows version would incorrectly present shipped functionality as new. Their original contents remain accessible at these immutable source links:

| Original changeset at `0.1.2` | Introducing commit | Reconciliation |
| --- | --- | --- |
| [device-neutral-sdk-foundation](https://github.com/restarea92/hellgato/blob/7bc62ff1c12a394ee31a2c4aa81ffc3f56f9a7c7/.changeset/device-neutral-sdk-foundation.md) | [b7c8f2d](https://github.com/restarea92/hellgato/commit/b7c8f2df7d0193857bde88408fcaf8b8731e0516) | Keep both SDK minor bumps; remove the already-shipped Windows target and narrow the summary to SDK contract, API and package-entry-point exposure. |
| [isolated-ipc-candidate](https://github.com/restarea92/hellgato/blob/7bc62ff1c12a394ee31a2c4aa81ffc3f56f9a7c7/.changeset/isolated-ipc-candidate.md) | [6cbc736](https://github.com/restarea92/hellgato/commit/6cbc736f8ae5fab26bce354b56cd02fe8b6860b5) | Keep both SDK minor bumps; remove the Windows target and obsolete trial/release-workflow wording. Retain SDK-generated types and sidecar lifecycle fixes. |
| [ipc-display-observations](https://github.com/restarea92/hellgato/blob/7bc62ff1c12a394ee31a2c4aa81ffc3f56f9a7c7/.changeset/ipc-display-observations.md) | [7559023](https://github.com/restarea92/hellgato/commit/75590239ba084dc98451bfddd9bb87fd45a7dafb) | Retire this app-only pending file: the separated worker integration, identity/preferences, knob controls and display restoration were already included in Windows `0.1.2`. |

The [published `0.1.2` notes](https://github.com/restarea92/hellgato/releases/tag/v0.1.2) describe the separated runtimes, first/reconnected LCD images, profile/sleep restoration and physical checks. The versioned source records the additional integration details. Internal trial tools and release scaffolding are not presented as newly shipped product features.

The SDK portions remain pending because neither npm package has published those APIs. Their revised summaries are explicitly about SDK packages and library consumers; the version wrapper may include them under `Included SDK changes` when bundling the next SDK version into Windows. It must not repeat the previous desktop integration or installer work as new Windows features.

The app-only `screensaver-full-frame-output` changeset remains unchanged. Its [introducing commit](https://github.com/restarea92/hellgato/commit/5308656700fd88acadada262442d781b7eadc592) is later than published Windows `0.1.3`, so the next app notes retain that actual new fix plus the new bundled SDK version.

This reconciliation retires only the already-shipped app-only pending entry and narrows the two mixed entries. It does not run version preparation in the working checkout, bump manifests, modify historical release notes, change GitHub releases or publish npm packages. The expected next versions remain SDK `0.2.0` and Windows `0.1.4`.
