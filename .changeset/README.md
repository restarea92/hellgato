# Versioning

Use `pnpm changeset` for a shipped change. Write a short user-facing English summary. Documentation, tests, website content and automation-only fixes need no version bump.

- `@hellgato/windows-release` is the private Windows app version marker. App-only changes select this package.
- `hellgato` and `@hellgato/core` form one fixed SDK version group, independent of the app version. An SDK bump also adds an app patch because the desktop bundle includes it; an explicit larger app bump wins.
- Choose patch for a compatible fix, minor for a compatible feature, major for a breaking public contract, including before 1.0.

Pending changesets accumulate until the **Version management** workflow refreshes one draft metadata PR on `changeset-release/main`. An empty `.changeset` directory apart from configuration and this README means there is no pending version change. GitHub's directory timestamp shows its latest touching commit, not the installed application version. The root workspace and adapter package versions are not the Windows release number. Source versions also do not establish that a matching package has been published to npm.

```sh
pnpm release:status
pnpm release:version
pnpm release:metadata:check
pnpm test:version
```

`release:version` consumes pending changesets and generates versions, changelogs, lockfile entries, app metadata and Windows release notes. With no pending changes it is a no-op. Review that PR before merging; do not hand-edit generated version fields.

Version preparation does **not** automatically build or publish a release. The separate **Windows Rust build** and **Windows Rust beta draft** workflows are manual; see [release operations](../docs/windows-releases.md). Public 0.2.x desktop builds use GitHub's prerelease flag. npm publication and automatic desktop updates remain disabled.
