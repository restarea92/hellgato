# Architecture candidate evidence

A successful build is not a physical-device test. The new architecture has no
new physical compatibility row until a device owner supplies candidate evidence.
Historical N4 Pro E / V4.N4 Pro E.02.010 / Windows x64 / Stream Deck 7.6.0.23012
results apply only to the earlier product, not automatically to the sidecar.

The new physical worker's exact experimental admission policy permits only that
historical firmware string for an explicit trial. Unknown firmware fails closed.
Admission does not turn the historical row into a candidate pass: new candidate
source/artifact hashes, the [trial procedure](ipc-trial.md) and fresh physical
results are still required.

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

The candidate must not replace the existing Windows engine until the checklist in
`tests/hardware/README.md` is completed. Unknown or untested support is experimental.
