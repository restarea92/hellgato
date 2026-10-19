---
"@hellgato/windows-release": patch
---

After initial pairing, retry the local Stream Deck connection while both apps and the USB device are available, without opening a pairing window or launching or closing either app. Automatic host rescan is verified for Stream Deck 7.6.0.23012. Keep USB and network connection status separate, stop retries after an explicit disconnect, and prevent repeated saved-screensaver announcements from overflowing the output queue.
