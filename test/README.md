# Tests

Choose checks for the changed boundary; ordinary PR CI only checks whitespace and changed JavaScript syntax.

| Boundary | Focused checks |
| --- | --- |
| Current frame runtime | `node --test test/frame-*.test.mjs test/full-frame-*.test.mjs` |
| Rust GUI, device or host worker | `cargo test --manifest-path <crate>/Cargo.toml` for the affected crate |
| Public SDK | `pnpm test:npm`, `pnpm typecheck` |
| Version metadata | `pnpm test:version`, `pnpm release:metadata:check` |
| Installer migration | `powershell -NoProfile -File test/test_installer_migration.ps1` |

Runtime/fleet simulation and rendered UI commands are in the [Rust app README](../apps/stream-dog/rust/README.md#verification). They do not prove physical USB behavior; use the [hardware checklist](../docs/compatibility/hardware-testing.md).

Python `test_*.py`, historical `candidate-*` suites and old release coordinator tests cover legacy interfaces. They are retained for changes to those interfaces, not required as a blanket migration gate. `helpers/`, `fixtures/` and `typechecks/` hold test inputs and consumers, not alternate apps.
