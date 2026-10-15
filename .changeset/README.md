# Changesets and version review

Add a changeset with `pnpm changeset` for a shipped change. Summaries are user-facing English. Use `patch` for a compatible fix, `minor` for a compatible feature, or `major` for a breaking public contract, including before 1.0.

- `@hellgato/core` and `hellgato` are one fixed SDK group. Select either; both receive the same SDK version.
- `@hellgato/windows-release` is the private Windows app version marker, with its own independent version number.
- An SDK version bump automatically adds an app patch because the app bundles that SDK. An explicit app minor/major wins over the automatic patch.
- App-only changes select only `@hellgato/windows-release`; SDK versions stay unchanged.
- Documentation, tests and automation-only changes need no version bump. An explanatory empty changeset is optional.

Changesets accumulate on `main`. The **Version management** workflow maintains one draft PR on `changeset-release/main`, refreshed from current main, instead of merging a version on every commit. Multiple pending changes use their largest requested bump, rather than adding one bump per commit.

Review and merge that version PR when the batch is ready. It contains package versions, changelogs, the lockfile, app metadata and Windows release notes. Its Windows build runs automatically against the exact generated PR SHA; the PR comment links the result and identifies that SHA. A refresh returns the PR to draft for review again.

Local equivalent:

```sh
pnpm install --frozen-lockfile
pnpm run release:status
pnpm run release:version
pnpm run release:metadata:check
pnpm run test:version
```

`release:version` consumes the pending changesets using the pinned Changesets CLI and generates `app/release.json`, `installer/version.iss`, and `docs/releases/<app-version>.md`. Running it again with no pending changes is a no-op. Do not manually edit version fields or generated metadata. If generation fails, inspect the error and restore only its uncommitted generated files/consumed changesets before starting again from a clean checkout.

Version preparation never publishes npm, pushes tags or creates a public release. After a reviewed app version reaches main, the separate Windows workflow builds that merged source and prepares an unpublished installer draft. The owner publishes that draft manually. npm publication remains disabled and will be connected separately later.

The old release-plan/bootstrap/channel-handoff commands are not part of this workflow. Their offline artifact-validation helpers remain for compatibility with existing packaging code; no active workflow invokes the old coordinator or its publication commands. See [Windows release operations](../docs/windows-releases.md).
