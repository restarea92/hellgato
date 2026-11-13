# Windows Rust beta releases

Version changes remain grouped through Changesets. The version workflow refreshes
one draft metadata PR; ordinary source changes run whitespace and JavaScript
syntax checks. Heavy legacy Python checks are manual only.

The **Windows Rust build** workflow is manual or reusable. It builds the Node SDK,
Rust GUI, Rust device worker and Rust host helper. Python is used only to fetch
reviewed build inputs and compile the native Qt/startup components; no Python
runtime or worker is staged into the product.

The build extracts the reviewed official Stream Deck MSI into an isolated
reference directory, checks its signature and executable digest, and generates
the executable-bound layout plan there. It does not install or launch Stream
Deck on the runner. That reference identifies tested native layout support;
untested user installations remain connectable with compatibility warnings.

To prepare a GitHub draft, dispatch **Windows Rust beta draft** on main with the
full merged source SHA. The workflow verifies the source is on main, builds the
Rust installer, verifies its checksum and creates a prerelease draft. It never
publishes automatically. The installer, SHA-256 sidecar and build provenance
manifest are the only release assets.

For a reviewed local build, use the same staging and installer scripts, record
the exact source SHA and artifact digests, and upload those files to a beta draft.
Verify the version, source, runtime inventory, checksum and release notes before
publication. A local build is not a successful GitHub Actions build.

Public beta releases use GitHub's prerelease flag even when the version is
`0.2.1`. They do not replace the latest stable release or activate automatic
updates. Publish the beta explicitly, verify anonymous downloads and checksums,
then make the versioned download prominent in the main README. Preserve older
releases for rollback. Do not publish npm packages as part of desktop releases.
