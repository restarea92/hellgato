# Tests

All automated suites live in this directory. `helpers/` contains shared fixtures;
`typechecks/` contains compile-time consumers, including intentionally invalid
inputs. They are not alternate application implementations.

- Node/Rust beta: `frame-*.test.mjs`, `full-frame-*.test.mjs`, Rust crate tests and
  the [integration commands](../apps/stream-dog/rust/README.md#verification).
- Public SDK: `architecture-*.test.mjs`, protocol/transport/firmware suites and
  `sdk-types.test.mts`.
- Previous stable app: Python `test_*.py`, `candidate-*.test.mjs` and product IPC
  regressions. Despite their historical names, the Node candidate modules still
  run the previous stable installer; these tests are not orphaned experiments.
- Packaging/releases: `release-*.test.mjs`, `version-*.test.mjs`, Python release
  tests and `test_installer_migration.ps1`.

Run `pnpm test` for Node and `.venv/Scripts/python.exe -m unittest discover -s test`
for Python. The [physical test recipe](../docs/compatibility/hardware-testing.md)
is documentation, separate from automated pass results.
