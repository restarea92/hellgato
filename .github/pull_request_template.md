## Change

Describe the user-visible change and validation.

## Release impact

- [ ] Windows
- [ ] npm (`@hellgato/core` and `hellgato`, fixed together)
- [ ] Shared CORA/upstream source (both)
- [ ] No release: explain the exact paths and why shipped bytes/API are unchanged

Add a changeset for each affected release unit. Run `pnpm run release:impact --base <main-base-sha>` and `pnpm run release:metadata:check`.

Release PRs: review old/new versions, selected components, notes and base SHA. Merging an owner-approved release PR authorizes the selected release after publication setup is explicitly enabled. Do not edit generated versions or reuse unverified artifacts.
