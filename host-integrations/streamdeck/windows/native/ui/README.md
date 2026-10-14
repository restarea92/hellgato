# Native device controls

`HellgatoNativeUiV2.dll` adds knob brightness to the existing Stream Deck device preferences. It copies the native brightness row's label alignment, sun-button images, slider handle/rail stylesheet, font, palette, size limits, and layout spacers through public Qt APIs. It identifies the selected virtual device by its exact `HGMOCK` serial. No Stream Deck executable addresses or on-disk files are patched by this extension.

The loader attaches only to the expected already-running `StreamDeck.exe`, queues initialization on its GUI thread, and never launches, stops, or restarts it. Repeated initialization refreshes the control endpoint token without adding another row. Controls remain disabled when the authenticated local backend or physical device is unavailable. Firmware update controls are collapsed and their user input is suppressed only for the configured virtual device; the CORA firmware policy independently rejects unsupported firmware writes.

The object names `brightnessRow`, `lb_SNValue`, `clb_updateFW`, and `lb_fwVerText` are discovered from the live Qt object tree. These names and the device preferences layout remain host integration dependencies. If the expected serial and layout cannot be found, the DLL leaves the UI unchanged. A new Stream Deck release still needs compatibility verification.

The extension leaves native Network Dock discovery and pairing under Stream Deck's control. It does not trigger the Add Network Device action, enter pairing addresses, confirm pairing dialogs, or close them. Native confirmation behavior remains visible to the user.

Build with Visual Studio C++ Build Tools and the pinned Qt development kit:

```powershell
python host-integrations/streamdeck/windows/native/ui/prepare.py
python host-integrations/streamdeck/windows/native/ui/build.py
python host-integrations/streamdeck/windows/native/ui/build.py --test-host
```

Dragging streams the latest value at a maximum of approximately 30 updates per second, with one request in flight and one pending value. Drag previews use `persist: false`; release, keyboard, and sun-button changes save the latest value with `persist: true`. Backend state does not overwrite the slider while a drag or final save is pending. There are no additional percentage or linking controls in the native row.

The output is `work/build-native-ui/HellgatoNativeUiV2.dll`. Package that file as `native/HellgatoNativeUiV2.dll` and include `scripts/load-native-ui.py`. The Qt development kit is build-only. The extension uses Qt and the Microsoft C++ runtime already supplied by the installed Stream Deck; it does not ship another Qt runtime.

Run the isolated test host with a Qt `bin` directory on `PATH` and its platform plugin directory available. The test host listens on an ephemeral loopback port, serves an in-memory device, and verifies native row placement, authenticated controls, selection isolation, firmware controls, and same-process token refresh. It never opens a device.

```powershell
$env:PATH = (Resolve-Path work/toolchain/qt-6.5.3/6.5.3/msvc2019_64/bin).Path + ';' + $env:PATH
$env:QT_PLUGIN_PATH = (Resolve-Path work/toolchain/qt-6.5.3/6.5.3/msvc2019_64/plugins).Path
./work/build-native-ui/HellgatoNativeUiTest.exe -platform offscreen
python -m unittest discover -s test -p test_native_ui.py
```

The DLL is built against Qt 6.5.3 public APIs and was tested with Qt 6.5.3 and 6.11.1. Object discovery was verified against Stream Deck 7.6.0.23012. Existing Qt libraries remain subject to their original licenses; the extension's source is available under the repository license.
