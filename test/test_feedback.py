from pathlib import Path
import struct
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from compatibility import IncompatibleError
from feedback import ACTIVE_MS, PROLOGUE, NOTIFY_PROLOGUE, WAIT_BRANCH, build_feedback, resolve_feedback

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_RAX, UC_X86_REG_RBX, UC_X86_REG_RCX,
                              UC_X86_REG_RDX, UC_X86_REG_RSP, UC_X86_REG_RDI,
                              UC_X86_REG_R15, UC_X86_REG_R8, UC_X86_REG_R9,
                              UC_X86_REG_R10, UC_X86_REG_R11, UC_X86_REG_XMM0, UC_X86_REG_EFLAGS)


def feedback_fixture(shift=0):
    image = bytearray(0x6000 + shift)
    base, worker, table, wake = 0x140000000, 0x3000 + shift, 0x5000 + shift, 0x4808 + shift
    struct.pack_into('<Q', image, table + 88, base + worker)
    image[worker:worker + 6] = bytes.fromhex('41bf64000000')
    site = worker + 0x100
    image[site:site + 17] = WAIT_BRANCH + bytes.fromhex('4863d1')
    image[site + 30:site + 32] = b'\x33\xd2'
    functions = [(worker, worker + 0x200)]
    for delta in (-0xf00, -0xd00, -0xb00, -0x900):
        at = worker + delta
        body = PROLOGUE + bytes.fromhex('488b8b80000000488d4b28ff15')
        image[at:at + len(body)] = body
        struct.pack_into('<i', image, at + len(body), wake - at - len(body) - 4)
        functions.append((at, at + len(body) + 4))
    at = worker - 0x100
    body = NOTIFY_PROLOGUE + bytes.fromhex('488d4b28ff25')
    image[at:at + len(body)] = body
    struct.pack_into('<i', image, at + len(body), wake - at - len(body) - 4)
    functions.append((at, at + len(body) + 4))
    return SimpleNamespace(image=image, base=base, functions=functions,
        imports={wake - 8: b'GetTickCount64', wake: b'?wakeOne@QWaitCondition@@QEAAXXZ'},
        vtable=lambda name: table,
        function=lambda rva: next((a, b) for a, b in functions if a <= rva < b),
        rel=lambda rva: rva + 4 + struct.unpack_from('<i', image, rva)[0])


class FeedbackResolverTest(unittest.TestCase):
    def test_relocated_layout_resolves_without_fixed_addresses(self):
        original = resolve_feedback(feedback_fixture())
        moved = resolve_feedback(feedback_fixture(0x137))
        self.assertEqual(moved['site'], original['site'] + 0x137)
        self.assertEqual(moved['requests'], [rva + 0x137 for rva in original['requests']])
        self.assertEqual(len(moved['guards']), 6)

    def test_changed_wait_or_missing_notification_is_rejected(self):
        for target in (0x3100, 0x2f00):
            with self.subTest(target=target):
                exe = feedback_fixture()
                exe.image[target] ^= 1
                with self.assertRaises(IncompatibleError):
                    resolve_feedback(exe)


class FeedbackMachineTest(unittest.TestCase):
    def setUp(self):
        self.base, self.allocation = 0x100000, 0x200000
        self.object = 0x300000
        self.now, self.wakes = 10000, []
        self.spec = {'tickImport': 0x100, 'wakeImport': 0x108, 'requests': [0x400, 0x500, 0x600, 0x700],
                     'site': 0x800, 'resume': 0x80e, 'idle': 0x820}
        self.uc = Uc(UC_ARCH_X86, UC_MODE_64)
        for address, size in [(self.base, 0x1000), (self.allocation, 0x3000),
                              (self.object, 0x2000), (0x400000, 0x10000)]:
            self.uc.mem_map(address, size)
        self.uc.mem_write(self.base + 0x100, struct.pack('<QQ', self.base + 0x200, self.base + 0x210))
        self.uc.mem_write(self.base + 0x200, b'\xc3')
        self.uc.mem_write(self.base + 0x210, b'\xc3')
        self.image, self.patches, self.report = build_feedback(self.base, self.allocation, self.spec)
        self.uc.mem_write(self.allocation, self.image)
        self.uc.mem_write(self.object + 0x20, struct.pack('<I', 60))
        self.uc.hook_add(UC_HOOK_CODE, self.intercept)

    def intercept(self, uc, address, size, data):
        if address not in (self.base + 0x200, self.base + 0x210):
            return
        if address == self.base + 0x210:
            self.wakes.append(uc.reg_read(UC_X86_REG_RCX))
        for register in (UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9,
                         UC_X86_REG_R10, UC_X86_REG_R11):
            uc.reg_write(register, 0xdeadbeef)
        for register in range(UC_X86_REG_XMM0, UC_X86_REG_XMM0 + 6):
            uc.reg_write(register, (1 << 128) - 1)
        uc.reg_write(UC_X86_REG_RAX, self.now if address == self.base + 0x200 else 0)

    def run_request(self, index=0, target=None):
        request, _, patched = self.patches[index]
        trampoline = struct.unpack_from('<Q', patched, 6)[0]
        self.uc.reg_write(UC_X86_REG_RSP, 0x408008)
        arguments = {UC_X86_REG_RCX: target or self.object, UC_X86_REG_RDX: 123,
                     UC_X86_REG_R8: 456, UC_X86_REG_R9: 789, UC_X86_REG_R10: 987,
                     UC_X86_REG_R11: 654}
        for register, value in arguments.items(): self.uc.reg_write(register, value)
        for register in range(UC_X86_REG_XMM0, UC_X86_REG_XMM0 + 6):
            self.uc.reg_write(register, register * 17)
        self.uc.emu_start(trampoline, self.base + request + len(PROLOGUE), count=10000)
        for register, value in arguments.items(): self.assertEqual(self.uc.reg_read(register), value)
        for register in range(UC_X86_REG_XMM0, UC_X86_REG_XMM0 + 6):
            self.assertEqual(self.uc.reg_read(register), register * 17)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_RSP), 0x408008)

    def run_worker(self, rendered, target=None):
        trampoline = struct.unpack_from('<Q', self.patches[-1][2], 6)[0]
        self.uc.reg_write(UC_X86_REG_RSP, 0x408000)
        self.uc.reg_write(UC_X86_REG_RBX, target or self.object)
        self.uc.reg_write(UC_X86_REG_RDI, int(rendered))
        self.uc.reg_write(UC_X86_REG_R15, 100)
        self.destination = None
        def stop(uc, address, size, data):
            if address in (self.base + self.spec['resume'], self.base + self.spec['idle']):
                self.destination = address
                uc.emu_stop()
        hook = self.uc.hook_add(UC_HOOK_CODE, stop)
        try:
            self.uc.emu_start(trampoline, 0, count=10000)
        finally:
            self.uc.hook_del(hook)
        self.assertIsNotNone(self.destination)
        return self.uc.reg_read(UC_X86_REG_RCX)

    def test_active_window_extends_from_requests_not_output(self):
        self.run_request()
        self.assertEqual(self.wakes, [self.object + 0x28])
        self.now += ACTIVE_MS - 1
        self.assertEqual(self.run_worker(True), 16)
        self.run_request(1)
        self.assertEqual(len(self.wakes), 1)
        self.now += ACTIVE_MS - 1
        self.assertEqual(self.run_worker(True), 16)
        self.now += 1
        self.assertEqual(self.run_worker(True), 120)
        self.run_request(2)
        self.assertEqual(len(self.wakes), 2)

    def test_dirty_notification_replays_stack_and_comparison(self):
        self.spec['requests'].append(0x900)
        self.spec['prologues'] = {'2304': NOTIFY_PROLOGUE.hex()}
        image, patches, _ = build_feedback(self.base, self.allocation, self.spec)
        self.uc.mem_write(self.allocation, image)
        request, original, patched = patches[-2]
        self.uc.mem_write(self.object + 0x10, struct.pack('<Q', 1))
        self.uc.reg_write(UC_X86_REG_RSP, 0x408008)
        self.uc.reg_write(UC_X86_REG_RBX, 1234)
        self.uc.reg_write(UC_X86_REG_RCX, self.object)
        trampoline = struct.unpack_from('<Q', patched, 6)[0]
        self.uc.emu_start(trampoline, self.base + request + len(original), count=10000)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_RSP), 0x407fe0)
        self.assertEqual(struct.unpack('<Q', self.uc.mem_read(0x408000, 8))[0], 1234)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_RBX), self.object)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_RCX), self.object)
        self.assertEqual(self.uc.reg_read(UC_X86_REG_RAX), 1)
        self.assertTrue(self.uc.reg_read(UC_X86_REG_EFLAGS) & 0x40)
        self.assertEqual(self.wakes, [self.object + 0x28])

    def test_idle_worker_preserves_forever_wait_and_reactivates(self):
        self.run_worker(False)
        self.run_request(3)
        self.assertEqual(self.run_worker(True), 16)
        self.assertEqual(self.run_worker(False), 16)
        self.assertEqual(self.destination, self.base + self.spec['resume'])
        self.now += ACTIVE_MS
        self.run_worker(False)
        self.assertEqual(self.destination, self.base + self.spec['idle'])
        self.run_request()
        self.assertEqual(self.run_worker(True), 16)

    def test_activity_is_per_composer_and_clock_is_64_bit(self):
        self.now = (1 << 32) + 10000
        self.run_request()
        self.assertEqual(self.run_worker(True), 16)
        self.assertEqual(self.run_worker(True, self.object + 0x100), 100)

    def test_full_activity_table_keeps_unregistered_composers_idle(self):
        for index in range(256): self.run_request(target=self.object + index * 16)
        self.run_request(target=self.object + 0x1000)
        self.assertEqual(self.run_worker(True, self.object + 0x1000), 100)


if __name__ == '__main__':
    unittest.main()
