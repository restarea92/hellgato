# Architecture candidate evidence

A successful build is not a physical-device test. The current branch's ordinary
Windows app uses the SDK session and separated USB worker. A physical compatibility
claim still requires source-bound evidence from a device owner.
Historical N4 Pro E / V4.N4 Pro E.02.010 / Windows x64 / Stream Deck 7.6.0.23012
results apply only to the earlier product, not automatically to the sidecar.

The physical worker's exact admission policy permits only that historical firmware
string. Unknown firmware fails closed. Admission does not turn the historical row
into a new-engine pass: source/artifact hashes and fresh physical results are still
required. The [isolated trial procedure](ipc-trial.md) is optional development
testing; normal-app checks use the installed GUI and its build manifest.

For the installed `bb51866` build, the device owner confirmed initial key icons,
USB reconnect restoring icons and automatic pairing, Stream Deck exit promptly
returning to native standby, and automatic reconnect after Stream Deck restart.
These four checks do not establish complete parity. Core actions, profile changes,
brightness, sleep/wake and Hellgato restart checks remain pending.

Evidence categories:

| Category | Required evidence | Meaning |
| --- | --- | --- |
| Unit/contracts | validator, mapping and lifecycle tests | implementation behavior |
| Simulated IPC | real Node child / Python worker with fake backend | framing/lifecycle only |
| Packed consumer | empty-cache offline tarball installation | package independence |
| Native fixtures | isolated startup and Qt fixture result files | synthetic host behavior |
| Windows installer | exact SHA build, install/upgrade/remove | product packaging |
| Physical device | hardware recipe and candidate hashes | named combination only |
| Public release | approved publish and downloaded bytes | delivery only |

The Windows default engine has changed, but the complete checklist in
`tests/hardware/README.md` remains incomplete. Unknown or untested support is
experimental; the four installed checks do not broaden device/firmware support or
establish a public release.
