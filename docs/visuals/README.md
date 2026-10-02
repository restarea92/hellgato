# Architecture visuals

- [hellgato-network.gif](hellgato-network.gif): white-background, 800×800 infinite loop for the project README; 80 packets, 12 frames, averaging 24 fps.
- [hellgato-network.png](hellgato-network.png): static preview.
- [hellgato-network.html](hellgato-network.html): self-contained browser view with playback, flow, label, and data controls. Download the file and open it in a WebGL-capable browser; no network connection is required.
- [3d](3d/README.md): individual GLB models, transparent PNG previews, and rendering settings.

The repeated packet layout loops every 500 ms while each packet's complete route takes 20 seconds. GIF delays use 10 ms units, so frames alternate between 40 and 50 ms to average 24 fps. Capture times follow those exact delays to maintain a constant travel speed. These illustrative packets do not measure throughput or latency.

To regenerate the GIF, use Node.js with Playwright and Microsoft Edge, plus Python with Pillow:

```sh
node docs/visuals/render-network.cjs
python docs/visuals/encode-network.py
```

The capture script stores temporary frames under `work/hellgato-network-frames`. `PLAYWRIGHT_MODULE` can point to an existing Playwright installation. The encoder uses a shared palette, preserves a pure-white background, and checks the decoded frames.

The standalone view includes Three.js 0.170.0 and Floating UI. Their MIT notices are in [licenses](licenses/).
