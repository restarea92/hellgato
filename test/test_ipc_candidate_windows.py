"""Real Windows process-tree/ownership tests, with no hardware or host mutation."""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/stream-dog/python'))
from hellgato_candidate.windows import ProcessTree, LauncherOwnership, kernel


@unittest.skipUnless(sys.platform == 'win32', 'Windows kernel fixture only')
class CandidateWindowsTests(unittest.TestCase):
    def test_launcher_rejects_duplicate_and_releases_after_owner_exit(self):
        with LauncherOwnership():
            with self.assertRaisesRegex(RuntimeError, 'Exit Hellgato'):
                with LauncherOwnership():
                    self.fail('Duplicate owner acquired launcher')
        with LauncherOwnership():
            pass

    def test_kill_on_close_reaps_node_style_parent_and_worker_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            record = Path(temp) / 'pids.json'
            code = """
import json, os, subprocess, sys, time
if sys.stdin.readline().strip() != 'START': raise SystemExit(4)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
with open(sys.argv[1], 'w') as file: json.dump([os.getpid(), child.pid], file)
time.sleep(60)
"""
            parent = None
            handles = []
            api = kernel()
            api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            api.OpenProcess.restype = w.HANDLE
            api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            api.WaitForSingleObject.restype = w.DWORD
            try:
                with ProcessTree() as job:
                    parent = subprocess.Popen([sys.executable, '-c', code, str(record)], stdin=subprocess.PIPE,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                              creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                    job.assign(parent)
                    parent.stdin.write(b'START\n')
                    parent.stdin.flush()
                    deadline = time.monotonic() + 10
                    while not record.exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue(record.exists(), 'Child did not start inside job')
                    pids = json.loads(record.read_text())
                    for pid in pids:
                        handle = api.OpenProcess(0x00100000, False, pid)
                        self.assertTrue(handle)
                        handles.append(handle)
                self.assertIsNotNone(parent.wait(timeout=10))
                for handle in handles:
                    self.assertEqual(api.WaitForSingleObject(handle, 10000), 0, 'Job left a live descendant')
            finally:
                for handle in handles:
                    api.CloseHandle(handle)
                if parent is not None:
                    if parent.poll() is None:
                        parent.kill()
                        parent.wait(timeout=10)
                    parent.stdin.close()


if __name__ == '__main__':
    unittest.main()
