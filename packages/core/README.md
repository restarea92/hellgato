# @hellgato/core

The Node.js network bridge behind [Hellgato](https://github.com/restarea92/hellgato).
Connect hardware inputs and display output to the official Stream Deck application.
ESM, Node 24, no npm dependencies. Experimental API in `0.0.1-beta`.

```sh
npm install @hellgato/core@beta
```

## Example

```js
import { createBridge, createN4ProProfile } from '@hellgato/core';

const bridge = await createBridge(createN4ProProfile(42));
bridge.on('image', ({ keyIndex, data, format }) => {
  // Send these image bytes to your hardware's display driver.
  console.log(keyIndex, format, data.length);
});
bridge.on('touchImage', ({ data, region }) => console.log(region, data.length));
bridge.on('clientConnected', ({ role }) => console.log(`${role} connected`));
await bridge.start();

// Pair Stream Deck's Network connection with 127.0.0.1:5343 on first use.
// Call these from your device's input callbacks once the child is connected.
bridge.sendKey(0, 'down');
bridge.sendKey(0, 'up');
bridge.sendDial({ kind: 'rotate', index: 0, delta: 1 });
bridge.sendDial({ kind: 'press', index: 0, state: 'down' });
bridge.sendDial({ kind: 'press', index: 0, state: 'up' });
bridge.sendTouch({ type: 'tap', x: 100, y: 50 });

process.once('SIGINT', () => { void bridge.stop(); });
```

## API

- `createBridge(options?)`: asynchronously creates an idle bridge. Importing or creating one does not open ports or write files.
- `createN4ProProfile(identity?, dockIdentity?)`: returns the existing 10-key, four-dial N4 Pro geometry and stable serial configuration. Default identity: `2`.
- `options.geometry`, `options.config`: override profile fields. Use a stable, distinct identity for each device.
- `options.port`, `options.childPort`: default to `5343` and `5344`. Use `0` for automatically assigned test ports.
- `bridge.start()`, `bridge.stop()`: start and stop both servers. Repeated calls are safe; failed startup releases acquired ports.
- `bridge.ports`: actual `{ primary, child }` listening ports, or `null` values while stopped.
- `bridge.primaryConnected`, `bridge.childConnected`: connection state.
- `bridge.sendKey(index, state)`: zero-based key index, `down` or `up`.
- `bridge.sendDial(event)`: zero-based dial index; `rotate` with integer `delta` from -128 to 127, or `press` with `state`.
- `bridge.sendTouch(event)`: `tap`, `hold` or `swipe` with `x`, `y`, and for swipes `endX`, `endY`.

Input methods return `false` when no child client is connected, otherwise `true`.
Invalid input throws. Do not send a release before its matching press has been delivered.

Events: `image`, `touchImage`, `brightness`, `clientConnected`, `clientDisconnected`, `log`, `comm`.
Connection, log and communication events provide `{ role, detail }`, where `role` is
`primary` or `child` and `detail` contains the upstream event arguments.
Image and brightness events retain their DeckBridge payloads. Add an event listener
before starting the bridge. Store or forward images in your adapter as needed.

## Scope

This package provides local network transport. It does not include a USB driver,
the desktop GUI, or Stream Deck process-memory adjustments. Servers bind only to
`127.0.0.1`; pairing is manual and mDNS is disabled. It has no installation scripts.

The complete Windows application has been validated with one Mirabox N4 Pro and
Stream Deck 7.6.0.23012. N4 Pro ten-key support also needs the desktop application's
compatibility and geometry adjustments; this package alone does not provide them.
Other hardware and operating systems are not validated by this release.

## License

MIT. Includes adapted DeckBridge code, copyright 2026 Lukas, under MIT.
See `THIRD-PARTY-NOTICES.txt` and `dist/vendor/LICENSE`.
Independent of Elgato and Mirabox.
