# Release rollout validation

This checklist separates implemented offline coverage, real build verification,
and external setup that must still be exercised after explicit approval. No new
Windows or npm release was published while implementing the automation.

## Local evidence

- Node 24.19.0, pnpm 11.10.0, Changesets 3.0.3 and npm 11.9.0 used.
- Actual pnpm-built npm tarballs were inspected, installed together offline with
  an empty npm cache outside the workspace, imported, and exercised through
  start/stop/concurrent lifecycle/restart tests.
- Windows metadata source, Inno include and runtime JSON all report 0.1.2;
  prepared 0.1.2 notes are preserved and no release intent is active.
- Windows publisher tests use mocked remote writes and byte-exact archive fixtures.
- Version tests replay the actual Changesets CLI in detached worktrees and run
  under simulated Windows `core.autocrlf=true`; fixtures are independent of
  current source versions and real pending changesets.
- actionlint validates every workflow. All external Actions are commit-pinned.

## The 24 acceptance checks

| # | Acceptance | Evidence / remaining rollout check |
| --- | --- | --- |
| 1 | Icon-only change selects Windows | Impact mapping covers `app/**`; Windows-only real Changesets simulation leaves npm versions unchanged. |
| 2 | Runtime/Inno drift rejected | Metadata and Python fixtures reject mismatch; Windows CI additionally runs packaged `HellgatoWorker.exe --version` before upload. |
| 3 | npm-only fixed group/exact dependency | Actual CLI simulation plus pnpm tarball inspection and isolated installation; Windows version unchanged. |
| 4 | Shared CORA requires both | Full-diff missing-component fixtures. |
| 5 | Shared upstream outside packages | `scripts/prepare-cora.mjs` impact fixture requires both. |
| 6 | Docs/tests do not release | Explanatory empty changeset/no-release fixtures; no pending bumps means version action is skipped. |
| 7 | Unknown paths/workspaces fail | Unknown-path and extra-workspace tests, including alternative YAML syntax. |
| 8 | Windows-only never publishes missing npm0.1.0 | Selection refusal tests and workflow conditions; no generic Changesets publish command. |
| 9 | Deterministic release PR files | Real CLI replay compares plan, versions, notes, lockfile and hashes. Actual bot PR creation/update remains a post-merge setup check. |
| 10 | Bot PR required CI at exact head | Both version and download-docs workflows explicitly dispatch head-bound checks. Actual check attachment and configured branch protection must be verified after setup. |
| 11 | Ordinary events cannot publish | Approval tests reject wrong PR, author, branch, owner, source and event; unchanged/no plan is a no-op. |
| 12 | Stale plan/main advance | First-parent/base and reviewed-tree guards; stale/recomputed-tamper tests; exact-SHA checkout throughout. |
| 13 | Frozen install/pack on Windows+Linux | Actual Linux and Windows pack/consumer checks passed on the implementation PR, with Windows CLI resolution and canonical provenance regression fixtures. |
| 14 | All selected builds before writes | Read-only barrier requires every selected successful build and re-verifies all selected downloaded artifacts, including recovery, before either publisher. |
| 15 | Tag/artifact/hash tampering blocked | Tag movement, raw ZIP SHA-256, safe exact ZIP members, tarball inventory and source/plan digest tests. |
| 16 | npm partial success resumes | Core-present/facade-missing fixture skips identical core and completes facade only; both remote states preflight before writes. |
| 17 | Windows partial upload resumes safely | Matching draft missing-upload fixtures; unknown author/body/assets fail. |
| 18 | Completed exact retry no-op | Windows, npm registry and npm GitHub finalizer no-write fixtures; conflicting bytes fail. |
| 19 | npm GitHub Latest cannot confuse Windows | Resolver ignores npm namespaces and never calls `releases/latest`; npm finalizer uses `make_latest=false`. |
| 20 | SemVer/page/draft/prerelease cases | Paginated resolver fixtures include0.1.10 vs0.1.9 and reversed dates. |
| 21 | API errors fail closed | HTTP403/429/5xx/malformed JSON fixtures; only explicit optional404 means absent. |
| 22 | README changes only after public confirmation | Resolver verifies public bytes before narrow docs PR generation; URL/path allowlist tests; no auto-merge. |
| 23 | Bootstrap cannot silently change baseline | Windows old public0.1.1 and npm latest0.0.1-beta checks; changed baseline/target conflicts fail; explicit bootstrap consumes only empty changesets. |
| 24 | Final public confirmation | Downloaded Windows assets and npm versions/integrity/latest are checked by code and mocks. First live release/OIDC confirmation remains intentionally unperformed pending setup and explicit initial-release approval. |

## Scope and remaining decisions

The supported release targets remain the two existing npm packages and Windows.
The architecture split changes their internals while preserving separate product
and SDK release decisions. Adding future internal workspaces requires an explicit
impact-map/policy update;
separately owned community repositories are not silently included in this
repository's release permissions.

The draft implementation PR's final GitHub checks are the authority for actual
Windows build status. A Linux test run is not a Windows installer test. Physical
hardware/UI verification and npm trusted-publisher authentication remain separate
from build and unit-test success. See [setup and activation](release-setup.md).


## Action runtime migration

The workflow action runtimes are distinct from the application's pinned Node
24.19.0 toolchain. The [GitHub Node 20 retirement notice](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)
requires Node 24-native action metadata rather than warning-suppression variables.

- `pnpm/action-setup` uses the exact [v5.0.0 commit](https://github.com/pnpm/action-setup/releases/tag/v5.0.0),
  which declares Node 24 and retains the previously verified Windows installation
  layout. The application still uses pnpm 11.10.0. The newer v6 installer changes
  that layout, so adopting v6 requires separate Windows resolver verification.
- [upload-artifact v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)
  explicitly keeps `archive: true` and `overwrite: false`; the artifact digest
  remains the original ZIP SHA-256, not the executable or tarball hash.
- [download-artifact v8.0.2](https://github.com/actions/download-artifact/releases/tag/v8.0.2)
  explicitly keeps extraction enabled and fails digest mismatches. Original
  artifact IDs, run IDs and unique run-attempt names remain required. Independent
  exact ZIP/member/byte verification still runs before publication.

Hosted runners observed during validation use runner 2.337.0, above the Node 24
minimum 2.327.1. No forced-runtime environment workaround is used.

## Architecture migration regression gates

- A checked-in 38-file Mirabox inventory removes the live Git tree lookup that
  failed with HTTP 403 in Checks run 38489835820. Cache hits rehash all SDK files
  without SDK network traffic; misses fetch only pinned immutable files. Wrong
  digest/size, missing offline files, traversal, duplicate inventory paths,
  symlinks and extra cached runtime files fail closed.
- Unit and installer migration checks no longer prepare the Windows SDK. The
  actual Windows build still calls preparation and requires the verified SDK.
- Shared core/facade source and schemas require both SDK and Windows impact.
  Stage-A Python worker/product/host changes require Windows impact. Unknown
  paths and unclassified workspaces remain errors.
- npm workflow publication and CLI/API publication are hard-disabled. Existing
  publisher ordering, baseline, digest, OIDC and partial-resume checks remain
  executable against fake I/O. No live publication is attempted by those tests.
- Legacy stable plans remain supported. Schema-3 reviewed beta and stable
  promotion plans bind committed public baselines and full Changesets replay.
  Schema-2 artifact-only candidates and private source-CI packages cannot publish.
- Exact next-to-main source handoffs are separate from stable version approval:
  source tree and beta plan bytes are fixed, extra edits and stale refs fail,
  preserving merge ancestry is required, and promotion needs fresh reviewed
  baselines plus a separate pre-exit plan. End-to-end fixtures cover that path.
- npm registry confirmation downloads the official exact-version tarballs and
  compares SHA-256, SHA-512, and size with the approved artifacts. Metadata-only
  matches, changed unrelated dist-tags, or stable values on beta fail closed.
- Original artifact verification binds the exact run attempt, source-head
  ancestry, successful unique build job, immutable archive identity, and manifest
  source SHA. An ordinary stable Windows build does not inherit a lingering plan
  digest; npm-only version PRs keep Windows in its independent source context.
- Local beta Windows tests exercise the same publication engine with explicit
  in-memory transports, including stable promotion, immutable retries, stale
  public baselines, malformed channel plans, and build-byte tampering. No live
  GitHub or npm write is performed. The real Windows installer still requires
  Windows CI and upgrade/downgrade/device evidence before release approval.
