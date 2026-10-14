# Release setup and activation checklist

No credentials or repository permissions are changed by this code PR.

## Required before automatic release PRs

1. In [Hellgato Actions settings](https://github.com/restarea92/hellgato/settings/actions),
   allow GitHub Actions to create pull requests. The connector cannot read this
   setting, so its current value is unverified. This grants the existing workflow
   token PR creation within this repository; no personal access token is needed.
2. Confirm the automatically created draft PR receives successful **Checks / test
   (ubuntu-24.04)**, **Checks / test (windows-2022)** and **Windows build / build**
   checks on its exact latest head. The explicit dispatch jobs require their
   declared `actions: write` permission. Treat this as an actual rollout test.

## Required before npm publishing

Read-only registry verification during implementation found both names already
published at `0.0.1-beta`, with `latest` and `beta` pointing there. Core also has
`0.0.0-stage`. Neither has `0.1.0`. This does not establish authenticated package
ownership or trusted-publisher configuration. Recheck these facts at activation.

For each package, `@hellgato/core` and `hellgato`:

1. Verify the npm account or organization owns the exact name. An HTTP 404 lookup
   is not proof of permission to create the name. Confirm or explicitly approve
   initial publication of the verified 0.1.0 artifact through npm's supported
   owner flow only if the package does not yet exist; the currently observed names already exist.
2. Under the package's Publishing access settings, add a trusted publisher:
   - Provider: GitHub Actions
   - Organization/user: `restarea92`
   - Repository: `hellgato`
   - Workflow filename: `release.yml`
   - Environment: leave empty for this implementation (no environment job exists)
3. Enable **direct npm publish** for that publisher. Staged publishing alone does
   not complete this workflow; `stage approve` is a separate human 2FA boundary.

Adding each trusted publisher creates persistent publish access and must be
explicitly approved at setup time. The scope is that package and that workflow;
no long-lived npm token is requested. The workflow's npm CLI 11.9.0 exceeds the
OIDC minimum. Use GitHub-hosted runners, exact repository metadata, and retain
package 2FA protections. See [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/).

## Required before first public release

1. Review and merge the implementation PR with all final CI passing.
2. Review the exact selected component(s), first version, previous public version,
   release notes and bootstrap plan in a separate owner-authored release PR.
   Windows 0.1.2 and npm 0.1.0 are independent choices.
3. Approve activation of repository Actions variable `RELEASE_ENABLED=true` only
   after the intended package access and bootstrap are ready. This activates the
   already-reviewed publication workflow for owner-merged release plans; it does
   not itself approve a release or permit a plain code push to publish.
4. Confirm the first real run's artifact identities, public downloaded SHA-256,
   npm version/integrity/latest tags and final summary. Dry-run success does not
   substitute for this check.

## Recommended protections (separate approved settings change)

At implementation time the active ruleset only blocks deletion and non-fast-
forward updates of `main`; it does not enforce CI checks or protect release tags.
The separate legacy branch-protection endpoint returns HTTP 403 to the connected
integration, so protection settings outside the visible ruleset remain unverified.

- Require a pull request and the observed checks above before merging into main.
- Require the branch up to date, so stale version plans are regenerated.
- Protect `v*`, `hellgato@*` and `@hellgato/core@*` against deletion and updates,
  while allowing the release workflow's initial tag creation.
- Review `.github/workflows/**`, `.release/**` and release scripts carefully.
  Do not require a second approver that a sole maintainer cannot supply.
- Keep default token permissions read-only; jobs request only their needed scope.

These protections strengthen race/maintainer safety. Their exact rules, bypass
actors and required check names must be confirmed before applying; this PR does
not silently add them or claim they already exist. A GitHub App is only a fallback
if actual required-check behavior cannot be satisfied by the existing token.

## Architecture migration publication hold

npm publication is currently hard-disabled in source, independently of
`RELEASE_ENABLED`. Both the `npm-publish` and `npm-finalize` jobs are disabled;
the npm publishing API/CLI and release finalization CLI refuse the action before
network access. No environment variable or recovery dispatch overrides this
hold. The disabled job no longer requests an OIDC identity token.

SDK tests, tarball packing/verification, Changesets versioning and the fixed npm
pair continue to run. Independent Windows build/release jobs retain their
existing approval and artifact checks. `RELEASE_ENABLED` therefore cannot reopen
npm publication; the historical npm activation checklist above is deferred until
a separate reviewed code change restores publication and the owner approves the
specific release. This change creates no credentials or persistent permissions.

The public publisher remains stable-only and fails closed on prerelease metadata.
Migration candidates are CI artifacts only; beta publication and independent
worker/CompatibilityTools assets require their own reviewed channel policy. A
successful artifact build is not a beta/stable release or hardware validation.

### Prepared versus published Windows baseline

Source `@hellgato/windows-release` is currently `0.1.2`; the last confirmed
public Windows baseline is `0.1.1`. Recheck the public state before any release.
A new Windows patch changeset generates a normal `0.1.2 -> 0.1.3` plan. If `0.1.2`
has never been published, the Windows verifier correctly refuses that plan:
`Approved oldVersion does not match the latest published Windows stable`.
This is a Windows baseline mismatch, not a dependency on npm publication.
A mixed plan with a valid Windows baseline can build both components and publish
Windows while npm publication remains held.

The existing bootstrap only accepts the separately reviewed Windows
`0.1.1 -> 0.1.2` transition, from a clean prepared source with no pending version
bumps. Before architecture changesets enter main, an independently approved
Windows-only bootstrap on that prepared baseline is the existing supported way
to establish it. Do not publish an unverified architecture candidate merely to
prime that baseline.

If the prepared `0.1.2` is intentionally skipped or version-bump changesets have
already entered main, keep candidates as CI artifacts. A separate narrowly
reviewed release-policy change must distinguish the source version from the
confirmed public baseline, bind the intended transition to the approved plan,
and retain deterministic replay, monotonic versions, exact artifacts and owner
approval. Do not edit `oldVersion`, pretend an unpublished version exists, or
relax the current baseline check. npm does not need to be enabled for that
Windows-only follow-on.
