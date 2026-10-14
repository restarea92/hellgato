# Stream Dog Python product

`hellgato_stream_dog` is the canonical private desktop implementation. It owns the
existing GUI, tray/session lifecycle, translations, persistent product settings,
device registry and user-profile transfer. `stream-dog.py` is its entrypoint.
There is no new Python distribution or release/version owner.

`app/hellgato.py`, `app/ui.py`, `app/i18n.py` and the three product-state modules
under `app/n4/` are compatibility aliases. Both names resolve to the same module
object, so old imports and test patch targets remain valid. Assets, translations
and release metadata keep their existing packaged paths for this migration.

The canonical launcher uses `hellgato_host` for Windows host preparation/status.
It starts `ipc_bridge.py`, which owns the SDK process lifetime and translates
session events for the existing GUI. The SDK starts a separate Mirabox worker;
the desktop process does not import the USB SDK. Saved identity, native knob
controls, and profile transfer keep their existing product interfaces.

## Locked development/build environment

Use the existing Python 3.13 baseline (Windows CI pins 3.13.7) and uv 0.12.23:

```powershell
python -m pip install uv==0.12.23
uv sync --locked --no-install-workspace --python python
.venv/Scripts/python.exe -m unittest discover -s test -p 'test_*.py'
.venv/Scripts/python.exe apps/stream-dog/python/stream-dog.py --version
```

The root virtual project is tooling-only; source discovery and PyInstaller staging
supply each private runtime explicitly. No editable workspace package is installed
by the build command. `uv.lock` pins the complete cross-platform dependency graph,
while the six existing direct build/runtime versions are unchanged. The adapter
is a workspace member without becoming a separately published Python product.

`installer/requirements-build.txt` is a compatibility export, never an independent
set of version decisions. Regenerate it after an approved lock change:

```sh
uv export --locked --only-group build --no-emit-workspace --no-annotate --output-file installer/requirements-build.txt
```

The export retains hashes and platform markers. Normal builds use `uv sync
--locked`; changing dependency declarations without refreshing the lock fails.

See [uv locking](https://docs.astral.sh/uv/concepts/projects/sync/) and
[uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/).
