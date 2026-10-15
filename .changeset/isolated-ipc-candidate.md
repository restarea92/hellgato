---
"@hellgato/core": minor
"hellgato": minor
---

Generate SDK JavaScript and declaration files from strictly checked TypeScript sources, including typed bridge output/connection listeners and session events.

Fix SDK sidecar reuse across sessions by tracking worker-process generations. Retain device ownership until close succeeds so failed cleanup can be retried safely.
