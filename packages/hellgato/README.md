# hellgato

**Unofficial hardware. Official software.**

The Node.js network bridge behind [Hellgato](https://github.com/restarea92/hellgato).
This package re-exports `@hellgato/core`; both use the same implementation.
ESM, Node 24. Experimental API in `0.0.1-beta`.

```sh
npm install hellgato@beta
```

```js
import { createBridge, createN4ProProfile } from 'hellgato';

const bridge = await createBridge(createN4ProProfile(42));
bridge.on('image', ({ keyIndex, data }) => console.log(keyIndex, data.length));
await bridge.start();
process.once('SIGINT', () => { void bridge.stop(); });
```

See the [core API and input examples](https://github.com/restarea92/hellgato/tree/main/packages/core).
Your hardware adapter handles USB and renders received images.

This is a developer library, not the Windows installer. For the ready-to-use N4 Pro
application, [download Hellgato for Windows](https://github.com/restarea92/hellgato/releases).
The library does not include the desktop application's Stream Deck compatibility
or ten-key geometry adjustments. Other devices and operating systems are unvalidated.

MIT. The core includes adapted MIT-licensed DeckBridge code by Lukas with its notices.
Independent of Elgato and Mirabox.
