---
"@hellgato/windows-release": patch
---

Restore executable reads during physical-device connection so host admission no
longer fails with `readFileSync is not defined`.
