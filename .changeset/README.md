# Changesets

Include a changeset in the same PR as each release-affecting change. Use `pnpm changeset`; write the user-facing summary in English. `patch` is a compatible fix, `minor` adds a compatible feature, and `major` changes the public contract. This also applies before 1.0: a `major` input remains a major bump.

- Windows: `@hellgato/windows-release`
- npm: `@hellgato/core` and `hellgato` form a fixed group; selecting either releases both at the same version.
- Shared `app/cora/**`, upstream preparation, licensing, and shared build inputs require both components.

Run `pnpm release:impact --base origin/main --working-tree` before finishing a PR. Unknown paths and unknown workspaces fail closed; classify them explicitly. Documentation/test-only work requires an empty changeset with an explanatory body or an explicit scoped no-release exception. Policy-only work requires a scoped exception in `.release/no-release/<id>.json`: `schemaVersion: 1`, the exact `baseSha`, a meaningful `reason`, and exact changed `paths`. Exceptions are part of the review; a label alone is insufficient. Exceptions never permit unknown paths, a new workspace, manual version edits, or an arbitrary release plan.

`pnpm release:version` is the only normal version command. It consumes changesets with the pinned Changesets CLI, generates metadata/notes/lockfile and a verifiable release plan. Do not edit generated versions or plans by hand. `pnpm release:plan:check --base <main-sha>` replays the operation from that exact base.

The initial adoption intentionally contains no pending release changeset and no active `.release/plan.json`. Windows 0.1.2 is prepared, not automatically published. A separately approved bootstrap PR can run `pnpm release:version --bootstrap windows --from 0.1.1 --base <main-sha>` to record the existing 0.1.2 and its preserved notes without bumping to 0.1.3. npm bootstrap additionally requires owner verification of registry/ownership; the currently verified public baseline is `0.0.1-beta`, so use `--bootstrap npm --from 0.0.1-beta` in that separately reviewed PR after rechecking registry state. `--from 0.0.0` is reserved for a genuinely unpublished package baseline. Bootstrap does not publish or configure access.
