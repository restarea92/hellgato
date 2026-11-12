# Physical candidate gate

No connected hardware is assumed by CI. Device owners must fill a fresh result
for the exact candidate and compare the same workload with the prior product.
Do not run the two USB engines concurrently.

Record: source SHA; SDK/Windows versions; installer, worker and native digests;
OS/architecture; model/hardware revision; firmware; exact Stream Deck version
and executable digest; profile/identity; tester; date; recipe version (1).

Required checks:

- Ten keys and four dials; down/up ordering; dial pulse limitation declared.
- Key images, touch regions/full strip, LCD, screensaver, sleep/wake, both brightness controls.
- First pairing and previously paired restart, with unchanged virtual serial.
- 20 unplug/replug cycles; 20 host close/reopen cycles; worker/Node/GUI crash cleanup.
- One full workday continuous use; flood display while measuring input p50/p95/p99,
  frame throughput, queue depth, CPU/RSS, disk growth and stuck/lost input count.
- Windows sign-in/reboot and sleep/resume, duplicate app, port and USB contention.
- Malformed schema, incompatible protocol, wrong manifest/hash, wrong host readiness.
- Settings/device registry/profile preservation, backup, upgrade/remove and rollback.
- Unsupported boot-logo/firmware writes rejected. Existing unrelated version.dll preserved.
- Genuine Plus coexistence is unsupported; confirm the warning and shared-model risk.

Choose latency/resource acceptance thresholds from the baseline before testing
candidates. Preserve logs with physical serials, user profiles and image contents
redacted. A simulation or a video alone cannot mark every row passed.
