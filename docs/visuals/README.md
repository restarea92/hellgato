# Architecture visual

[Open the interactive diagram](https://hellgato-streamdog.vercel.app/architecture.html) or download [hellgato-network.html](hellgato-network.html) and open it locally. The scene has five objects: physical N4 Pro, input processing, virtual device bridge, Stream Deck app and a separate retained compositor. Input rises toward the app; display updates return through the virtual device and converge into the canvas, then complete frames follow one USB output path. The Rust management app supervises this system outside the data path.

Moving dots show direction, not measured traffic or latency. The exported GIF is a 24 fps illustration, not device performance. See [runtime ownership](../architecture/0002-full-frame-runtime.md) for the implementation.

To regenerate the PNG/GIF after editing the HTML:

```sh
node docs/visuals/render-network.cjs
python docs/visuals/encode-network.py
```

This asset-only toolchain requires Playwright/Edge and Pillow; it is not part of the shipped app. `PLAYWRIGHT_MODULE` can point to an existing Playwright installation. Captures go to `work/hellgato-network-frames`. [3D assets](3d/README.md) use illustration dimensions. Embedded Three.js and Floating UI retain their [MIT notices](licenses/).
