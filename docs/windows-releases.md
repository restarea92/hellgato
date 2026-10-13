# Windows builds and stable releases

## Ordinary changes never publish

`windows-build.yml` builds relevant `main` pushes and pull requests, including
`native/**`, and supports a build-only manual run. It also serves as the reusable
build for the release workflow. Its token has only `contents: read`.

The existing Windows 2022 / Python 3.13.7 build, pinned Inno Setup installer and
checksum, Python tests, installer migration tests, native builds, PyInstaller,
Inno Setup packaging, CORA tests and installer SHA-256 generation are retained.
The `.exe` and `.sha256` are saved as Actions artifacts, not GitHub Releases.
The existing `checks.yml` remains unchanged.

## Prepare a new stable version explicitly

First merge the workflow changes into `main`. For each future Windows release:

1. Change `AppVersion` in `installer/hellgato.iss` and add non-empty
   `docs/releases/<version>.md` in a reviewed release-preparation commit. The
   Windows version is read from the installer, not the independently versioned
   npm package. Filenames are derived from this same version; do not hard-code
   the next version in the workflow or build script.
2. Merge preparation into `main` and check CI. Create and push a new tag
   `vMAJOR.MINOR.PATCH` at that exact `main` commit. Lightweight and annotated tags
   work. A tag push alone does not publish anything.
3. In Actions, select **Windows release**, select branch **main**, enter the
   existing tag and explicitly enable **confirm**. The default is false. This
   is the only publication entry point; do not dispatch it for a build-only run.

The tag must already exist and resolve to the exact `GITHUB_SHA` captured for the
manual run on `main`. If `main` has moved since tagging, preflight rejects the
request instead of publishing a different commit. Never move a published tag.
The tag, installer version and version-specific notes must agree. Prerelease
suffixes, build metadata, leading zeros and a version less than or equal to the
latest stable release are rejected.

## Publication safety

The read-only preflight rejects existing releases, including drafts and
prereleases for the requested tag. Only an explicit HTTP 404 from an optional
release lookup counts as absence; permission, rate-limit, network and malformed
response errors abort. Missing tags always abort.

After preflight, the same read-only Windows build runs all tests and packages
the installer. The separate publish job depends on successful preflight and
build. Only this job receives `contents: write`. It downloads the artifact from
this run and attempt, verifies the expected filenames and SHA-256, and repeats
all version, commit, tag and release-existence checks immediately before calling
`gh release create` with `--verify-tag` and `--fail-on-no-commits`. Input values
are passed through environment variables and argument arrays, never interpolated
into executable shell text. No release edit, delete, asset overwrite or tag
creation operation is used.

The workflow-level `windows-release` concurrency group serializes stable
publication attempts across versions without cancelling an active run. Remote
state is checked again after the build, so a concurrent external publication
causes a safe failure. Repository tag-protection rules are still recommended:
workflow concurrency cannot prevent a maintainer or another integration from
moving a tag during the final API operation.

Use **Re-run all jobs**, rather than re-running only the publish job, to create a
fresh artifact for a new run attempt. Re-running a successfully published version
is rejected. A failed upload may leave a draft on GitHub; investigate it manually.
The workflow intentionally refuses automatic cleanup or overwriting.

## Regression checks and existing releases

Run the offline, standard-library guard tests with:

```sh
python -m unittest discover -s test -p 'test_windows_release.py' -v
```

These tests mock every GitHub and `gh` call. They cover non-release events, forks,
non-main refs, absent confirmation, invalid/mismatched versions, missing notes,
missing/moved tags, annotated tags, existing releases, API failures, numeric
version ordering, missing/tampered artifacts and publication arguments. They
also protect the workflow separation and original build commands.

This workflow change does not bump the Windows version, edit existing release
notes, move `v0.1.1`, change its published assets or publish a new release.
