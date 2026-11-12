# Mirabox Rust full-frame worker

An isolated Windows x64 worker for N4 Pro E firmware `V4.N4 Pro E.02.010`.
The worker calls the explicitly supplied Mirabox `transport.dll` through its C
ABI. It does not require Python. The vendor DLL and its runtime dependencies are
provided by the caller; this crate does not download or install them.

Build and verify without hardware:

```powershell
cargo test --manifest-path adapters/mirabox-rust/Cargo.toml
cargo build --release --manifest-path adapters/mirabox-rust/Cargo.toml
adapters/mirabox-rust/target/release/HellgatoMiraboxRustWorker.exe --describe
adapters/mirabox-rust/target/release/HellgatoMiraboxRustWorker.exe --fake
```

Physical mode requires `--dll C:\absolute\path\transport.dll`. Startup and
`describe` do not load the vendor DLL or access USB. `discover` acquires the same
`Local\Hellgato.N4Pro.Bridge` lifetime mutex as the existing product, loads the
runtime, and enumerates only `5548:1021`. Device IDs are path hashes; physical
paths and serial numbers are not exposed. `open` requires an explicitly
discovered ID and checks the exact firmware before any display controls.
Enumeration leaves `firmware` empty; only a successful open verifies firmware.

## JSONL protocol

Each stdin line is a request with a string or nonnegative safe-integer `id`, a
`method`, and an optional object `params`. Responses are `{id,result}` or
`{id,error:{code,message}}`. Stdout is reserved for JSONL. The worker first emits
`{event:"ready",...descriptor}`. The maximum request size is 1,500,000 bytes.

| Method | Parameters | Result |
| --- | --- | --- |
| `describe` | `{}` | Static layout and capabilities |
| `discover` | `{}` | `{devices:[{id,adapterId,modelId,displayName}]}` |
| `open` | `{deviceId,brightness?,generation?,sessionId?}` | Open session and exact firmware |
| `frame` | `{jpeg,width:800,height:480}` | Native `submitted` / `unconfirmed` result |
| `control` | `{control:"brightness",value:0..100}` or `{control:"sleep"}` / `{control:"wake"}` | Native submission |
| `close` | `{}` | Confirmed native handle release |

`jpeg` is canonical base64 containing one baseline RGB JPEG, at most 1 MiB.
The caller supplies the complete **already 180-degree rotated wire image**.
The worker validates its dimensions and fully decodes it before the native call.
It never rotates again, crops images, writes individual keys/panels, or writes
stored images. `open` clears key overlays once. Every frame is one volatile
`transport_set_background_frame_stream(...,800,480,0,0,0)` followed by refresh.
Native success describes submission, not hardware readback or displayed FPS.
Optional `deviceId`, `generation`, and `sessionId` on frame/control requests must
match the current open session. Fake responses use `applied` / `simulation`.

Inputs are emitted as `{event:"input",deviceId,generation,sessionId,sequence,...}`.
Keys use `{type:"key",index,state:"down"|"up"}`. Dials use
`{type:"dial",index,kind:"rotate",delta}` or `kind:"pulse"`; physical dial release
is unavailable. Touch gestures use `{type:"touch",gesture,x,y,endX?,endY?}` in
local 800x100 strip coordinates. Raw sensor Y is normalized from 112 rows; LCD
placement is not subtracted from input coordinates. Diagnostic `touchPoint`
events include normalized `x,y` and `rawY` before gesture completion.

`close` sends the volatile native disconnect notification and destroys the
transport. A failed native destroy retains the DLL and mutex until process
exit. EOF closes the device. Native calls cannot be forcibly cancelled within
the worker; the caller must own bounded process termination.

Stream Deck readiness and native extension attachment belong to
`host-integrations/streamdeck/windows/rust`. Device adapters do not inspect or
modify the host process. The registered adapter contract is documented in
`docs/adapters/frame-adapter-guide.md`.
