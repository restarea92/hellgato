---
"@hellgato/windows-release": patch
---

Preserve the prepared Stream Deck connection when URI forwarding briefly starts a second process. Wait for an unambiguous new host before preparing it, and pause the device if the previously prepared host has exited.

Keep duplicate native screensaver announcements bounded when key redraws arrive during restart readiness, while preserving wake and control order. Include payload-free queue counts in overflow diagnostics.
