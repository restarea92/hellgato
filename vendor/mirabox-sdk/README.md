# Pinned Mirabox SDK build inputs

`manifest.json` is the reviewed Windows runtime inventory of
MiraboxSpace/StreamDock-Device-SDK at commit
`87ff56674cb3772dc2a13376d174c33c37964607`.

The inventory was obtained from the complete, non-truncated Git tree at that
exact commit. It includes the same root license and Python/DLL files selected by
the previous build. Each entry binds its case-sensitive path, byte size and Git
blob SHA-1 (`SHA1("blob " + decimal byte length + NUL + file bytes)`). These are
Git object digests, not SHA-1 hashes of the file alone.

`python scripts/prepare-windows.py` downloads only missing files from the pinned
raw URLs, verifies all bytes before use, and refuses modified or unexpected
cached files. A fully populated SDK cache makes zero SDK HTTP requests. Runtime
provenance also records the SHA-256 of this inventory. There is no unauthenticated
Git tree API request at build time and no token requirement. Builds still fetch
the pinned Node executable and verify its official SHA-256 checksum separately.

To update: review an immutable upstream revision, obtain its complete tree,
review the selected source/DLL inventory and upstream licensing, update the
revision and file digests together, then run cache-miss, cache-hit and corruption
regression tests plus the Windows product build. Never regenerate this inventory
from a moving branch during CI or accept a digest returned by the download being
verified. Inventory changes require a Windows changeset. SDK unit tests and
installer migration tests do not need this download.

Inclusion does not assert that every upstream device model is supported by
Hellgato. Hardware and redistribution validation remain separate release gates.
