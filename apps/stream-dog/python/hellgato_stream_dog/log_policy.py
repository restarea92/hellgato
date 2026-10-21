"""Size-limited product logs and narrowly scoped historical trace retention."""

import logging
from pathlib import Path
import re
import stat
import subprocess
import threading
import time

LOG_BYTES = 2 * 1024 * 1024
TRACE_FILE_BYTES = 4 * 1024 * 1024
TRACE_TOTAL_BYTES = 16 * 1024 * 1024
RETENTION_SECONDS = 7 * 24 * 3600
TRACE_NAME = re.compile(r'[A-Za-z0-9_-]+-\d+\.jsonl(?:\.1)?\Z')


def regular(path):
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def linked_parent(path):
    return any(parent.is_symlink() or getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400
               for parent in (path, *path.parents))


class BoundedLog:
    """Reopen on each write so rotation never leaves a writer on an old file."""

    def __init__(self, path, max_bytes=LOG_BYTES):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.lock = threading.RLock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def flush(self):
        pass

    def write(self, text):
        data = text.encode('utf-8', errors='replace')
        if len(data) > self.max_bytes:
            data = b'[oversized log record omitted]\n'
        with self.lock:
            try:
                if self.path.is_symlink() or linked_parent(self.path.parent):
                    return len(text)
                if self.path.exists():
                    if not regular(self.path):
                        return len(text)
                    if self.path.stat().st_size + len(data) > self.max_bytes:
                        backup = self.path.with_name(self.path.name + '.1')
                        if backup.is_symlink() or backup.exists() and not regular(backup):
                            return len(text)
                        if self.path.stat().st_size > self.max_bytes:
                            self.path.unlink()
                        else:
                            self.path.replace(backup)
                with self.path.open('ab') as stream:
                    stream.write(data)
            except OSError:
                # A locked/full diagnostic destination must not stop device control.
                pass
        return len(text)


def configure_logging(state, stream=None):
    stream = stream or BoundedLog(state / 'bridge.log')
    handler = logging.StreamHandler(stream)
    logging.basicConfig(handlers=[handler], level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    return stream


def run_logged(command, log, *, timeout=None, **kwargs):
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs) as process:
        def drain():
            while data := process.stdout.read(8192):
                log.write(data.decode('utf-8', errors='replace'))
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        finally:
            reader.join(timeout=3)
        return subprocess.CompletedProcess(command, process.returncode)


def cleanup_logs(state, now=None):
    """Never traverse user data, links or unknown files; locked files are retried."""
    now = time.time() if now is None else now
    try:
        if linked_parent(state):
            return
        for base, limit in (('bridge.log', LOG_BYTES), ('startup-setup.log', LOG_BYTES),
                            ('setup.log', LOG_BYTES), ('ipc-diagnostics.jsonl', 256 * 1024)):
            for name in (base, base + '.1'):
                if name in ('bridge.log', 'ipc-diagnostics.jsonl'):
                    continue
                path = state / name
                try:
                    info = path.lstat()
                    if regular(path) and now - info.st_mtime > 600 and (
                            info.st_size > limit or now - info.st_mtime > RETENTION_SECONDS):
                        path.unlink()
                except OSError:
                    pass
        root = state / 'traces'
        if root.is_symlink() or getattr(root.lstat(), 'st_file_attributes', 0) & 0x400:
            return
        entries = []
        for path in root.iterdir():
            try:
                if TRACE_NAME.fullmatch(path.name) and regular(path):
                    info = path.stat()
                    entries.append((info.st_mtime, path, info.st_size))
            except OSError:
                pass
        total = sum(size for _, _, size in entries)
        for modified, path, size in sorted(entries):
            # Recently modified files may belong to an older running bridge.
            if now - modified < 600:
                continue
            if size > TRACE_FILE_BYTES or now - modified > RETENTION_SECONDS or total > TRACE_TOTAL_BYTES:
                try:
                    path.unlink()
                    total -= size
                except OSError:
                    pass
    except OSError:
        pass
