---
"@hellgato/core": patch
"hellgato": patch
---

Recover incomplete Stream Deck child pairing by refreshing its capabilities, then resetting only the stalled dock connection if necessary. Limit retries, preserve regular unpairing after a stable session, and cancel recovery when the bridge stops.
