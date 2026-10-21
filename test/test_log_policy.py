import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/stream-dog/python'))
from hellgato_stream_dog.log_policy import BoundedLog, cleanup_logs, run_logged


class LogPolicyTests(unittest.TestCase):
    def test_rotation_threads_unicode_and_existing_oversized_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bridge.log'
            path.write_bytes(b'x' * 5000)
            log = BoundedLog(path, 128)
            workers = [threading.Thread(target=lambda: [log.write('한글 event\n') for _ in range(100)]) for _ in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            log.write('x' * 5000)
            for item in Path(folder).iterdir():
                self.assertLessEqual(item.stat().st_size, 128)
                item.read_text(encoding='utf-8')
            self.assertIn('oversized', path.read_text(encoding='utf-8'))

    def test_retention_budget_and_user_data_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            traces = root / 'traces'
            traces.mkdir()
            now = time.time()
            def create(path, size, age):
                path.write_bytes(b'x' * size)
                os.utime(path, (now - age, now - age))
            create(traces / 'target-1.jsonl', 70, 1000)
            create(traces / 'target-2.jsonl', 70, 800)
            create(traces / 'target-3.jsonl', 70, 0)
            create(traces / 'target-4.jsonl', 101, 1000)
            create(traces / 'target-5.jsonl', 1, 8 * 86400)
            for name in ('settings.json', 'profiles', 'images', 'unknown.jsonl'):
                create(root / name, 12, 8 * 86400)
            create(root / 'setup.log.1', 10, 8 * 86400)
            create(traces / 'unknown.jsonl', 1000, 8 * 86400)
            with patch('hellgato_stream_dog.log_policy.TRACE_FILE_BYTES', 100), patch('hellgato_stream_dog.log_policy.TRACE_TOTAL_BYTES', 140):
                cleanup_logs(root, now)
            self.assertEqual(sorted(p.name for p in traces.iterdir()), ['target-2.jsonl', 'target-3.jsonl', 'unknown.jsonl'])
            self.assertFalse((root / 'setup.log.1').exists())
            for name in ('settings.json', 'profiles', 'images', 'unknown.jsonl'):
                self.assertTrue((root / name).exists())

    def test_locked_rotation_drops_write_and_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bridge.log'
            path.write_text('x' * 100)
            log = BoundedLog(path, 100)
            with patch.object(Path, 'replace', side_effect=PermissionError):
                log.write('new\n')
            self.assertEqual(path.stat().st_size, 100)
            log.write('new\n')
            self.assertEqual(path.read_text(), 'new\n')

    def test_helper_output_is_streamed_into_bounded_log(self):
        with tempfile.TemporaryDirectory() as folder:
            log = BoundedLog(Path(folder) / 'startup-setup.log', 32768)
            result = run_logged([sys.executable, '-c', "import sys; sys.stdout.write('x'*200000); sys.stderr.write('error')"], log, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertLessEqual(sum(p.stat().st_size for p in Path(folder).iterdir()), 65536)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction behavior')
    def test_linked_trace_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, outside = root / 'state', root / 'outside'
            state.mkdir()
            outside.mkdir()
            target = outside / 'target-1.jsonl'
            target.write_text('keep')
            os.utime(target, (0, 0))
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(state / 'traces'), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0)
            try:
                cleanup_logs(state)
                self.assertEqual(target.read_text(), 'keep')
                BoundedLog(state / 'traces' / 'bridge.log').write('skip')
                self.assertFalse((outside / 'bridge.log').exists())
            finally:
                (state / 'traces').rmdir()

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing behavior')
    def test_locked_historical_trace_is_preserved_until_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / 'traces').mkdir()
            path = state / 'traces/target-1.jsonl'
            path.write_text('keep')
            os.utime(path, (0, 0))
            with path.open('rb'):
                cleanup_logs(state)
                self.assertTrue(path.exists())
            cleanup_logs(state)
            self.assertFalse(path.exists())
