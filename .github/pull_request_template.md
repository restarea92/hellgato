## Summary

Describe the change and checks performed.

## Version impact

- [ ] SDK/core change: add a changeset for `@hellgato/core` or `hellgato`; the version command also bumps the bundled Windows app
- [ ] App-only change: add a changeset for `@hellgato/windows-release`
- [ ] No shipped change: explain why no version bump is needed

Run `pnpm run release:status` and `pnpm run release:metadata:check`. Keep ordinary changesets pending; the Version management workflow batches them into a separate draft version PR.

## Verification

List passed checks and anything not run, including hardware-only behavior. Version PRs and installer drafts do not publish npm or make a Windows release public.
