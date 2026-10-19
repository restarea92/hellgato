# Windows versioning and installer drafts

## One version PR, separate publication

Add ordinary feature/fix changesets as described in [the Changesets guide](../.changeset/README.md). The **Version management** workflow refreshes one draft version PR on main pushes. The SDK pair keeps one version; the Windows app keeps its own. A core/SDK bump also bumps the bundled app, while an app-only bump leaves both npm package versions unchanged.

Review the generated package changelogs, `app/release.json`, `installer/version.iss`, and `docs/releases/<app-version>.md`. The workflow builds the exact version PR SHA automatically using the read-only Windows builder and adds its result/run link to the PR. GitHub's default token does not start a new `pull_request` workflow for a bot-created PR, so this validation belongs to the main-triggered workflow run; the comment states exactly which PR SHA was built. Required PR checks, if configured separately, may still need the existing Checks/Windows build workflows dispatched on the version branch.

Merging the version PR records the reviewed versions. A separate **Windows installer draft** workflow builds the actual merged commit, which can differ from the earlier PR SHA, and prepares a GitHub draft release. Ordinary app commits, generated PR updates, release tags and publication events do not start this draft workflow. It only selects a real Windows version increase on main, or an explicitly selected retry of that version commit.

No workflow publishes npm. No workflow changes a draft release to public. A version number in source is not proof that the same version is available to users.

## Review and publish the installer draft

1. Merge the reviewed version PR when the accumulated batch is ready.
2. Wait for its Windows build and the draft creation job to succeed.
3. Open the matching draft in [GitHub Releases](https://github.com/restarea92/hellgato/releases). Verify its app version, exact source commit, app changelog, versioned installer and SHA-256 sidecar.
4. Download and test the installer as appropriate. The owner clicks **Publish release** only when ready.
5. Verify the public downloads after publication. Keep previous releases available for rollback. Update README download links only after verifying the new public assets.

Draft releases are unpublished. The build's internal provenance manifest records the source SHA, toolchain, run identity and file digests; local profiles, settings, logs and intermediate directories are never release assets.

## Safe retries and conflicts

The draft workflow is serialized. A matching complete draft is reused, not duplicated. A compatible incomplete draft can be completed from the same verified artifact. Already-public releases are never edited or overwritten. A different source, unexpected assets, changed bytes, a conflicting tag, a newer app version or an uncertain GitHub response stops the job for review instead of retrying publication in a loop.

Prefer **Re-run failed jobs** when build succeeded and draft creation failed. The draft job uses the successful build's artifact ID/name, including its original attempt, rather than guessing a new artifact from the current attempt number. A full rebuild may produce different installer bytes; conflicting draft assets are preserved, not silently replaced. Read the error before deciding whether to keep the existing draft or prepare a new version.

The existing **Windows build** workflow remains available for PRs, main changes and manual build-only runs. Its artifacts expire after 14 days; release drafts retain their attached files. Building does not create a public release.

## Setup and remaining boundaries

Version PR creation uses only the built-in GitHub token with scoped repository-content and PR-write permissions. GitHub must allow Actions to create PRs (`Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests`). If disabled, the workflow reports the failure; enabling that repository setting is an owner action. No personal token, npm credential, trusted-publisher setup or automatic merge is configured here.

The old release-plan/bootstrap/channel-handoff orchestration is not used by these workflows. Existing packaging/provenance helpers and historical release notes remain intact. The Windows draft workflow replaces the disabled manual Windows publication workflow. npm's existing publication hold remains in place.

## Local build and hardware checks

Follow [the pinned Windows build instructions](guide.md#build-and-test):

```powershell
pnpm run release:metadata:check
./scripts/build-windows.ps1 -Python ./.venv/Scripts/python.exe -Iscc <path-to-ISCC.exe>
```

The build emits `dist/Hellgato-<version>-Setup.exe` and its `.exe.sha256` sidecar. It packages the separated USB worker, offline-installed SDK and existing native integrations. `-UsePreparedNative` may reuse the two prepared native DLLs when their sources have not changed.

Hardware claims still require physical observations. Normal N4 controls, profile images, brightness and sleep/wake, USB reconnection, and Stream Deck exit/restart were previously verified for this implementation. Hellgato-only exit can leave the last image for tens of seconds before standby. After initial pairing, automatic network reconnection was verified with Stream Deck 7.6.0.23012 / Qt 6.11.1 when restarting Hellgato while Stream Deck remained open; other host builds keep manual pairing. The full endurance matrix and other hardware combinations remain unverified.
