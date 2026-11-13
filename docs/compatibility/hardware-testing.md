# Hardware beta checklist

Record the app/adapter version, exact model, VID/PID, firmware and Stream Deck version. State which results came from physical hardware rather than simulation.

1. Discover and connect; check the virtual identity, key layout, strip alignment and orientation.
2. Try every key, dial and touch gesture supported by the adapter. Check animated images and folder/profile changes.
3. Activate a full-screen screensaver, then wake with input. Verify the keys and continuous strip restore without residue. Check screen and knob brightness where supported.
4. Unplug/replug USB, restart Stream Deck, and quit/reopen Hellgato. Record whether manual network pairing is needed.
5. If two devices are available, check independent profiles, identities and disconnection. Do not claim multi-device hardware support from a single device or simulations.

Report failures with redacted `frame-events.jsonl` / `last-session.json`. Never share `control.json` or `native-ui.json` authentication tokens. No firmware flashing or persistent-background streaming is part of this checklist. See [current limitations](../guide.md#compatibility-and-limitations).
