# Repository tools

| Task | Entry |
| --- | --- |
| Build/run the Windows app | [Rust app instructions](../apps/stream-dog/rust/README.md) |
| Stage a bundle, build an installer, package an adapter, run focused QA | `stream-dog/` |
| Build/check the reusable SDK | `build-npm.mjs`, `build-typescript.mjs`, `verify-npm.mjs`, `stage-windows-sdk.mjs` |
| Prepare versions and generated metadata | `version-packages.mjs`, `sync-release-metadata.mjs`; [versioning guide](../.changeset/README.md) |
| Prepare reviewed native build inputs | `prepare-windows.py` and the host integration's native build scripts |
| Build the website | `node apps/site/build.mjs` |

Python remains a build dependency for native components, not a shipped runtime. Older release coordinators and Python packaging scripts are retained for legacy fixtures; they are not the current desktop release path. Use [Windows release operations](../docs/windows-releases.md), rather than running historical publication scripts directly.
