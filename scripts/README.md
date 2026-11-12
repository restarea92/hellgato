# Repository tools

| Area | Commands |
| --- | --- |
| Node/Rust app | `stream-dog/`: staging, installer, adapter packaging and integration QA |
| Public SDK | `prepare-cora.mjs`, `build-npm.mjs`, `build-typescript.mjs`, `verify-npm.mjs`, `pack-npm.mjs`, `stage-windows-sdk.mjs` |
| Release operations | `release-*.mjs`, `version-*.mjs`, `draft-windows-release.mjs`, `npm-release.mjs`, `sync-release-metadata.mjs`, `update-downloads.mjs`, `windows_release.py`, `verify-artifact.py` |
| Previous stable Windows app | `build-windows.ps1`, `prepare-windows.py`, `stage-product-worker.py`, host preparation/patch/load scripts |
| Shared checks/assets | `check-*.mjs`, `check-python.py`, `build-icons.py`, `draw-stream-dog.py` |

Start with the [Node/Rust app instructions](../apps/stream-dog/rust/README.md).
Release tools remain used by package scripts and CI. Python/native build tools
remain necessary for the previous installer and shared host libraries; removing
Python from the beta runtime does not retire those build dependencies.
