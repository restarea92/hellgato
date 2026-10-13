from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from compatibility import IncompatibleError, ORIGINAL, PATCHED, ORIGINAL_GRID, TARGET_GRID
from feedback import PROLOGUE, WAIT_BRANCH
from live_preparation import Patch, TransientPreparationError, commit_patches, inspect_layout, prepare_live, verify_live_code


class Process:
    def __init__(self):
        self.base, self.pid = 0x100000, 123
        self.executable = Path('StreamDeck.exe')
        self.memory, self.protections = {}, {}
        self.running, self.paused = True, set()
        self.operations, self.allocations = [], []
        self.next_allocation = 0x200000
        self.rips = {1: 0x9999, 2: 0x8888}
        self.fail_at = None
        self.exit_on_suspend = False

    def seed(self, address, payload):
        self.memory.update({address + index: value for index, value in enumerate(payload)})

    def read(self, address, size):
        return bytes(self.memory.get(address + index, 0) for index in range(size))

    def write(self, address, payload, code=False):
        self.operations.append(('write', address))
        if self.fail_at == address:
            self.fail_at = None
            self.seed(address, payload[:2])
            raise OSError('write failed after two bytes')
        self.seed(address, payload)

    def alive(self):
        return self.running

    def executable_readonly(self, address, size):
        return self.protections.get(address) == 0x20

    def allocate(self, size):
        self.assert_running_allocator()
        address = self.next_allocation
        self.next_allocation += 0x10000
        self.allocations.append(address)
        self.operations.append(('allocate', address))
        self.protections[address] = 4
        return address

    def allocate_heap(self, size, existing):
        self.assert_running_allocator()
        self.operations.append(('allocateHeap', existing))
        return self.allocate(size)

    def assert_running_allocator(self):
        if self.paused:
            raise AssertionError('Heap allocation while application threads are suspended')

    def protect(self, address, size, protection):
        self.protections[address] = protection

    def heap_chunks(self):
        yield 0x10000, self.read(0x10000, 0x5000)

    def thread_ids(self):
        return list(self.rips)

    def open_thread(self, tid):
        return tid

    def suspend_thread(self, thread):
        self.operations.append(('suspend', thread))
        self.paused.add(thread)
        if self.exit_on_suspend:
            self.running = False

    def instruction_pointer(self, thread):
        return self.rips[thread]

    def resume_thread(self, thread):
        self.operations.append(('resume', thread))
        self.paused.remove(thread)

    def close_thread(self, thread):
        self.operations.append(('closeThread', thread))


def fixture():
    process = Process()
    feedback = {'tickImport': 0x7100, 'wakeImport': 0x7108,
                'requests': [0x1000, 0x1100, 0x1200, 0x1300],
                'site': 0x2000, 'resume': 0x200e, 'idle': 0x2020}
    feedback['guards'] = [{'rva': rva, 'original': (PROLOGUE + b'\x90' * 20).hex()}
                          for rva in feedback['requests']]
    feedback['guards'].append({'rva': 0x2000, 'original': (WAIT_BRANCH + b'\x90' * 20).hex()})
    spec = {'rva': 0x410, 'original': ORIGINAL.hex(), 'patched': PATCHED.hex(),
            'vtables': {'CORA Plus': 0x7000, 'USB Plus': 0x7080},
            'guards': [{'rva': 0x400, 'original': (b'\x90' * 16 + ORIGINAL + b'\x90' * 5).hex()}],
            'feedback': feedback, 'sha256': 'fixture'}
    for guard in [*spec['guards'], *feedback['guards']]:
        process.seed(process.base + guard['rva'], bytes.fromhex(guard['original']))
    node = bytearray(80)
    node[16:25] = b'20GBD9901'
    struct.pack_into('<QQ', node, 32, 9, 15)
    struct.pack_into('<QQQ', node, 48, 0x11000, 0x110a0, 0x110a0)
    process.seed(0x10100, node)
    key = [1, 4, 2, 0, 0, 800, 480, 120, 120, 120, 120, 100, 100, 0]
    encoder = [2, 4, 1, 0, 3, 800, 100, *([0xffffffff] * 6), 0]
    process.seed(0x11000, struct.pack('<14I', *key) + struct.pack('<QQQ', 0x12000, 0x12040, 0x12040))
    process.seed(0x11050, struct.pack('<14I', *encoder) + struct.pack('<QQQ', 0x12100, 0x12108, 0x12108))
    process.seed(0x12000, b''.join(struct.pack('<ii', *point) for point in ORIGINAL_GRID))
    process.seed(0x12100, struct.pack('<ii', 0, 380))
    for address, name in [(0x13000, 'CORA Plus'), (0x14000, 'USB Plus')]:
        process.seed(address, struct.pack('<Q', process.base + spec['vtables'][name]))
        process.seed(address + 0x94, struct.pack('<8i', 4, 2, -1, -1, 0, 800, 480, 0))
        process.seed(address + 0xc4, struct.pack('<4i', 120, 120, 100, 100))
    return process, spec


class LivePreparationTests(unittest.TestCase):
    def test_clean_process_stages_allocations_then_commits_coherent_cora_layout(self):
        process, spec = fixture()
        report = prepare_live(process, spec)
        self.assertTrue(report['ready'])
        self.assertEqual(report['mode'], 'attach')
        self.assertEqual(struct.unpack('<I', process.read(0x13094, 4))[0], 5)
        self.assertEqual(struct.unpack('<I', process.read(0x14094, 4))[0], 4)
        layout = inspect_layout(process, spec)
        self.assertEqual(layout['plusModelControllers'][0]['coordinatePairs'], TARGET_GRID)
        allocation = next(index for index, event in enumerate(process.operations) if event[0] == 'allocateHeap')
        suspend = next(index for index, event in enumerate(process.operations) if event[0] == 'suspend')
        self.assertLess(allocation, suspend)
        self.assertFalse(process.paused)

    def test_already_prepared_process_is_verified_without_state_or_duplicate_hooks(self):
        process, spec = fixture()
        first = prepare_live(process, spec)
        allocations = list(process.allocations)
        process.operations.clear()
        again = prepare_live(process, spec)
        self.assertEqual(again['changes'], 0)
        self.assertEqual(again['feedback'], first['feedback'])
        self.assertEqual(process.allocations, allocations)
        self.assertEqual(process.operations, [])

    def test_changed_code_or_existing_trampoline_is_rejected_before_any_live_patch(self):
        for changed in ('guard', 'trampoline', 'hook', 'protection'):
            with self.subTest(changed=changed):
                process, spec = fixture()
                if changed != 'guard':
                    report = prepare_live(process, spec)
                    allocation = int(report['feedback']['allocation'], 16)
                if changed == 'guard':
                    process.seed(process.base + 0x403, b'\xcc')
                elif changed == 'trampoline':
                    process.seed(allocation + 20, b'\xcc')
                elif changed == 'hook':
                    process.seed(process.base + 0x1100, b'\xcc')
                else:
                    process.protections[allocation] = 4
                process.operations.clear()
                with self.assertRaises(IncompatibleError):
                    prepare_live(process, spec)
                self.assertEqual(process.operations, [])

    def test_threads_inside_changed_span_are_resumed_without_committing(self):
        process, spec = fixture()
        process.rips[1] = process.base + spec['rva'] + 1
        before = process.read(process.base + spec['rva'], len(ORIGINAL))
        with self.assertRaisesRegex(TransientPreparationError, 'executing a changed span'):
            prepare_live(process, spec)
        self.assertEqual(process.read(process.base + spec['rva'], len(ORIGINAL)), before)
        self.assertFalse(process.paused)
        self.assertIn(('resume', 1), process.operations)

    def test_partial_write_failure_rolls_back_code_and_data_before_resuming(self):
        process, spec = fixture()
        process.fail_at = 0x11038
        original_descriptor = process.read(0x11000, 80)
        with self.assertRaises(OSError):
            prepare_live(process, spec)
        self.assertEqual(process.read(0x11000, 80), original_descriptor)
        self.assertEqual(process.read(process.base + spec['rva'], len(ORIGINAL)), ORIGINAL)
        verify_live_code(process, spec)
        self.assertFalse(process.paused)
        self.assertEqual(len([event for event in process.operations if event[0] == 'resume']), 2)

    def test_exited_pid_race_resumes_every_suspended_thread_without_writes(self):
        process, spec = fixture()
        process.exit_on_suspend = True
        before = process.read(process.base + spec['rva'], len(ORIGINAL))
        with self.assertRaisesRegex(TransientPreparationError, 'exited'):
            prepare_live(process, spec)
        self.assertFalse(process.paused)
        self.assertEqual(process.read(process.base + spec['rva'], len(ORIGINAL)), before)

    def test_precommit_guard_failure_always_resumes_threads(self):
        process, _ = fixture()
        process.seed(0x15000, b'abcd')

        def changed():
            raise IncompatibleError('changed')

        with self.assertRaises(IncompatibleError):
            commit_patches(process, [Patch(0x15000, b'abcd', b'efgh')], changed)
        self.assertEqual(process.read(0x15000, 4), b'abcd')
        self.assertFalse(process.paused)

    def test_uninitialized_model_is_retryable_without_allocations(self):
        process, spec = fixture()
        process.seed(0x10110, b'not-ready')
        with self.assertRaises(TransientPreparationError):
            prepare_live(process, spec)
        self.assertEqual(process.operations, [])

    def test_cancellation_before_staging_or_commit_never_leaves_threads_suspended(self):
        process, spec = fixture()
        with self.assertRaisesRegex(TransientPreparationError, 'cancelled'):
            prepare_live(process, spec, stop=lambda: True)
        self.assertEqual(process.operations, [])
        process.seed(0x15000, b'abcd')

        def cancel_during_verify():
            cancelled[0] = True

        cancelled = [False]
        with self.assertRaisesRegex(TransientPreparationError, 'cancelled'):
            commit_patches(process, [Patch(0x15000, b'abcd', b'efgh')], cancel_during_verify,
                           stop=lambda: cancelled[0])
        self.assertEqual(process.read(0x15000, 4), b'abcd')
        self.assertFalse(process.paused)

    def test_new_thread_after_validation_aborts_before_writing(self):
        process, _ = fixture()
        process.seed(0x15000, b'abcd')

        def new_thread():
            process.rips[3] = 0x7777

        with self.assertRaisesRegex(TransientPreparationError, 'new Stream Deck thread'):
            commit_patches(process, [Patch(0x15000, b'abcd', b'efgh')], new_thread)
        self.assertEqual(process.read(0x15000, 4), b'abcd')
        self.assertFalse(process.paused)


if __name__ == '__main__':
    unittest.main()
