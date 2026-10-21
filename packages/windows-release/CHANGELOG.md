# @hellgato/windows-release

## 0.1.8

### Patch Changes

- 54445b4: Preserve the prepared Stream Deck connection when URI forwarding briefly starts a second process. Wait for an unambiguous new host before preparing it, and pause the device if the previously prepared host has exited. Keep duplicate native screensaver announcements bounded when key redraws arrive during restart readiness, while preserving wake and control order. Include payload-free queue counts in overflow diagnostics.

## 0.1.7

### Patch Changes

- d23b4e1: Bound connection and installer logs, rotate current diagnostics, and periodically prune inactive historical traces without changing settings, profiles or images.

## 0.1.6

### Patch Changes

- 48dff31: Update the selected screensaver without restarting the USB worker or Stream Deck network connection. Prepare replacement animations in the background and keep the previous display available until they are ready.
  
  Keep USB readiness independent of an interrupted initial network connection so automatic reconnection can continue.

## 0.1.5

### Patch Changes

- Bundle @hellgato/core and hellgato 0.2.1 in the Windows application.
  
  Included SDK changes:
  
  Recover incomplete Stream Deck child pairing by refreshing its capabilities, then resetting only the stalled dock connection if necessary. Limit retries, preserve regular unpairing after a stable session, and cancel recovery when the bridge stops.
- ab1a725: After initial pairing, retry the local Stream Deck connection while both apps and the USB device are available, without opening a pairing window or launching or closing either app. Automatic host rescan is verified for Stream Deck 7.6.0.23012. Keep USB and network connection status separate, stop retries after an explicit disconnect, and prevent repeated saved-screensaver announcements from overflowing the output queue.

## 0.1.4

### Patch Changes

- Bundle @hellgato/core and hellgato 0.2.0 in the Windows application.
  
  Included SDK changes:
  
  Expose device-neutral SDK contracts and their JSON schema from `@hellgato/core`, including device descriptors, capabilities, mapping profiles, validated input events and display/control output. Add `createDeviceBridge`, `createSession` and `createSidecarAdapter` for library consumers while preserving `createBridge` and `createN4ProProfile`.
  
  Add the `hellgato/adapters/mirabox-n4-pro` mapping-profile entry point and `hellgato/testing` simulation/conformance helpers. The bounded Python sidecar API remains experimental; simulator results do not establish hardware compatibility.
  
  Generate SDK JavaScript and declaration files from strictly checked TypeScript sources, including typed bridge output/connection listeners and session events.
  
  Fix SDK sidecar reuse across sessions by tracking worker-process generations. Retain device ownership until close succeeds so failed cleanup can be retried safely.
- 5308656: Fix streamed screensaver stutter by sending each image as one full-screen frame and clearing key overlays only when entering the screensaver.

## 0.1.3

Published Windows stable release on 2026-10-15. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.1.3) · [Full release notes](../../docs/releases/0.1.3.md).

- Allow other N4 Pro firmware versions to attempt connection while retaining model, capability, valid-read and firmware-consistency checks. Device firmware is not modified.
- Preserve bounded firmware errors, stack traces and operation details in local diagnostics; record discovered firmware and successful connections/reconnections.
- Keep the original connection error when cleanup also fails, and log cleanup failures separately.

Other firmware remains experimental. This patch received automated regression checks but no physical hardware validation; it does not establish a root cause or confirmed fix for issue #6.

## 0.1.2

Published Windows stable release on 2026-10-14. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.1.2) · [Full release notes](../../docs/releases/0.1.2.md).

- Run the desktop app through the separated Hellgato SDK and a dedicated Mirabox USB worker, bundling both runtimes in the installer.
- Fix black LCD keys on first connection and USB reconnection; retain the virtual network dock through USB reconnects and Stream Deck restarts.
- Return the device to its standalone screen when Stream Deck exits, and preserve button/touch images across profile changes and sleep/wake.
- Refresh application, taskbar, tray and shortcut icons while retaining the existing app and installer artwork.

Hardware checks covered N4 Pro E firmware `V4.N4 Pro E.02.010` with Stream Deck `7.6.0.23012`. Closing only Hellgato can leave the last image visible temporarily; restarting only Hellgato while Stream Deck stays open requires manual network pairing. Dial holds and press-and-turn remain unsupported.

## 0.1.1

Published Windows stable release on 2026-10-13. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.1.1) · [Full release notes](../../docs/releases/0.1.1.md).

- Fix setup failing on a leftover per-user installation registration after its program folder had already been removed. Retain settings, remembered devices and Stream Deck profiles.

## 0.1.0

Published Windows stable release on 2026-10-13. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.1.0) · [Full release notes](../../docs/releases/0.1.0.md).

- Introduce Hellgato Stream Dog branding and the pixel-dog app icon.
- Prepare all ten keys at Stream Deck startup, including launches without connected USB hardware; preserve the virtual device across USB removal and restore the hardware connection automatically.
- Respect manual Stream Deck shutdown and reconnect when the user opens it again.
- Add native knob-brightness controls with independent persistence and sleep/wake restoration.
- List remembered devices, assigned Stream Deck serials and live USB status, retaining identity bindings across client restarts.
- Migrate verified per-user installations to `%ProgramFiles%\Hellgato`, close running apps during setup and retain settings, identities and profiles in user data. Offer separate launch-now and Windows sign-in startup options.

One registered N4 Pro can be active at a time. Serial-less devices use the USB path, so moving ports may change recognition. See the full notes for hardware coverage, installer migration checks and remaining manual-validation limits.

## 0.0.7-beta

Published Windows prerelease on 2026-10-12. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.0.7-beta) · [Full release notes](../../docs/releases/0.0.7-beta.md).

- Play saved static and animated Stream Deck screensavers through the N4 Pro SDK, preserving animation timing without separate per-key uploads.
- Turn the screen off during sleep when no screensaver is configured; otherwise dim the display and turn off knob lighting. Restore action images before fading the display and knobs back up.
- Add full-LCD images, whole-screen and individual-key color fills, logo display and per-device idle-timeout persistence.
- Ask for confirmation before writing exported profile bundles ([change](https://github.com/restarea92/hellgato/commit/9879063420cb0cd139b13b5c53788615ad817ac0)).

The Advanced standby-image setting remains unsupported. Stock firmware does not report dial releases, and some manual Sleep sequences wait for a zero-brightness command before switching the screen off.

## 0.0.6-beta

Published Windows prerelease on 2026-10-02. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.0.6-beta) · [Full release notes](../../docs/releases/0.0.6-beta.md).

- Refresh README and in-app artwork with the angled 3D controller; use its matching front view for Windows and tray icons.
- Add an animated input/display-path illustration, an offline browser view and individual 3D assets.
- Clarify profile-import behavior, dial/display limitations and outstanding hardware validation.

## 0.0.5-beta

Published Windows prerelease on 2026-10-01. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.0.5-beta) · [Full release notes](../../docs/releases/0.0.5-beta.md).

- Replace the app logo and Windows icons with flaming pixel artwork.

## 0.0.4-beta

Published Windows prerelease on 2026-10-01. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.0.4-beta) · [Full release notes](../../docs/releases/0.0.4-beta.md).

- Replace custom File, Settings and Help popup windows with standard menus, retaining commands, shortcuts, translations and disabled states.
- Position menus beside their buttons on all monitors, including negative desktop coordinates; dismiss them when the window moves and reopen at the new position.

## 0.0.3-beta

Published Windows prerelease on 2026-10-01. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.0.3-beta) · [Full release notes](../../docs/releases/0.0.3-beta.md).

- Adapt touch-strip feedback scheduling to display activity: check every 16 ms while requests arrive, return to the original idle wait after two seconds, and wake on the next request.
- Apply the adjustment only to the running Stream Deck process, with internal compatibility checks and native register/stack preservation tests.

Four 30 fps test animations achieved about 30 fps per strip region on the tested N4 Pro/Stream Deck configuration. This is a measured result, not a 60 fps output guarantee.

## 0.0.2-beta

Published Windows prerelease on 2026-10-01. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.0.2-beta) · [Full release notes](../../docs/releases/0.0.2-beta.md).

- Fix repeated dial clicks getting stuck pressed by forwarding each hardware click as a press followed by a release, allowing successive-click toggles to work.

This models a click; it does not add dial-hold support or change touch-strip animation performance.

## 0.0.1-beta

Published Windows prerelease on 2026-09-30. [GitHub release](https://github.com/restarea92/hellgato/releases/tag/v0.0.1-beta) · [Full release notes](../../docs/releases/0.0.1-beta.md).

- Introduce the MIT-licensed Windows bridge for one stock Mirabox N4 Pro and the official Stream Deck application: ten LCD keys, four dial rotations, app-rendered images and touch page switching.
- Add structural compatibility checks, persistent identity, USB reconnect handling and connection-aware profile progress/status.
- Provide a dark desktop UI, keyboard navigation, a separate preferences window and eleven translated interfaces.
- Support profile import/export, tray operation, optional Windows sign-in startup and a per-user installer with bundled runtimes.

Pair the local network device once in Stream Deck; Stream Deck and plugins are installed separately. The installer is unsigned. The original beta was validated with Stream Deck `7.6.0.23012`; dial holds/releases, broader touch alignment, sustained animation, reboot recovery and simultaneous genuine Stream Deck + use had remaining validation limits.
