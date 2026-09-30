import copy
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
from compatibility import (Executable, IncompatibleError, ORIGINAL, ORIGINAL_GRID,
                           TARGET_GRID, resolve, validate_controllers)


def fixture(shift=0):
    """Independent, relocatable x64 layout; contains no vendor executable data."""
    exe = object.__new__(Executable)
    image = bytearray(0x6000 + shift)
    exe.base = 0x140000000
    exe.pe = SimpleNamespace(sections=[SimpleNamespace(VirtualAddress=a + shift,
        Misc_VirtualSize=0x1000, Characteristics=flags)
        for a, flags in [(0x1000, 0x60000000), (0x3000, 0x40000000), (0x4000, 0xc0000000)]])
    def put(at, data):
        image[at:at + len(data)] = data
    def rel(at, target, extra=0):
        put(at, struct.pack('<i', target - at - 4 - extra))
    tables = []
    for index, name in enumerate((b'ESDHW20GBD9901CoraImp', b'ESDHW20GBD9901Imp')):
        td, col, vt = [v + shift + index * 0x100 for v in (0x3000, 0x3060, 0x3090)]
        put(td + 16, b'.?AV' + name + b'@@\0')
        put(col, struct.pack('<6I', 1, 0, 0, td, 0x3300 + shift, col))
        put(vt - 8, struct.pack('<5Q', exe.base + col, *([exe.base + 0x1800 + shift] * 4)))
        tables.append(vt)
    qsize = 0x3380 + shift
    exe.imports = {qsize: b'??0QSize@@QEAA@HH@Z', qsize + 8: b'AcquireSRWLockExclusive',
                   qsize + 16: b'ReleaseSRWLockExclusive', qsize + 24: b'WakeAllConditionVariable'}
    ctor = 0x1100 + shift
    prefix = bytes.fromhex('48895c2408574883ec40488bd9b9e8010000e800000000488bf84889442458488bd3488bc8e800000000488d0500000000488907c787b80100000e00000066c787900000000001')
    put(ctor, prefix)
    rel(ctor + 45, tables[0])
    cursor = ctor + len(prefix)
    for constants, field in [(ORIGINAL, 0x94), (bytes.fromhex('ba2003000041b8e0010000'), 0xa8),
                              (bytes.fromhex('ba78000000448bc2'), 0xc4), (bytes.fromhex('ba64000000448bc2'), 0xcc)]:
        code = constants + bytes.fromhex('488d4c2458ff15')
        put(cursor, code)
        cursor += len(code)
        rel(cursor, qsize)
        store = bytes.fromhex('488b0848898f') + struct.pack('<I', field)
        put(cursor + 4, store)
        cursor += 4 + len(store)
    put(cursor, bytes.fromhex('c687c000000001c3'))
    ctor_end = cursor + 8
    lookup, resume, header, tail = [v + shift for v in (0x1400, 0x1440, 0x1460, 0x1580)]
    guard, footer = 0x4000 + shift, 0x1800 + shift
    put(resume - 8, bytes.fromhex('3905000000007f20'))
    rel(resume - 6, guard)
    put(header, bytes.fromhex('488d0d00000000e800000000833d00000000ff0f8500000000'))
    rel(header + 3, guard)
    rel(header + 14, guard, 1)
    rel(header + 21, resume)
    for index, name in enumerate((b'20GAA9901', b'20GAA9902', b'20GBA9901', b'20GAI9901')):
        at, string = lookup + 0x80 + index * 16, 0x3400 + shift + index * 16
        put(string, name + b'\0')
        if index == 0:
            put(at, bytes.fromhex('488d1500000000'))
            rel(at + 3, string)
        else:
            put(at, bytes.fromhex('f20f100500000000'))
            rel(at + 4, string)
    put(tail, bytes.fromhex('488d0d00000000e80000000090e900000000cc'))
    rel(tail + 3, guard)
    rel(tail + 8, footer)
    rel(tail + 14, resume)
    for index in range(3):
        at = footer + index * 6
        put(at, bytes.fromhex('ff1500000000'))
        rel(at + 2, qsize + 8 + index * 8)
    put(footer + 18, bytes.fromhex('ffc08905000000008903c3'))
    exe.functions = [(ctor, ctor_end), (lookup, tail + 19), (footer, footer + 29)]
    exe.starts = [a for a, _ in exe.functions]
    exe.image = bytes(image)
    return exe, ctor + len(prefix), tail + 12


def controllers(final=False):
    grid = TARGET_GRID if final else ORIGINAL_GRID
    width = 5 if final else 4
    return [dict(address='0x1000', controller=1, width=width, height=2,
                 fields=[1, width, 2, 0, 0, 800, 480, 120, 120, 120, 120, 100, 100],
                 coordinateVector=[0x2000, 0x2000 + len(grid) * 8, 0x2000 + len(grid) * 8],
                 coordinatePairs=grid),
            dict(address='0x1050', controller=2, width=4, height=1,
                 fields=[2, 4, 1, 0, 3, 800, 100, *([0xffffffff] * 6)],
                 coordinateVector=[0x3000, 0x3008, 0x3008], coordinatePairs=[(0, 380)])]


class CompatibilityTest(unittest.TestCase):
    def resolve_fixture(self, exe):
        with patch('compatibility.Executable', return_value=exe):
            return resolve(exe.image)

    def test_relocated_layout_and_new_digest_are_accepted(self):
        previous = None
        for shift in (0, 0x1000):
            exe, constructor, breakpoint = fixture(shift)
            spec = self.resolve_fixture(exe)
            self.assertEqual(spec['rva'], constructor)
            self.assertEqual(spec['startupBreakpoint']['rva'], breakpoint)
            self.assertNotEqual(previous, spec['sha256'])
            previous = spec['sha256']

    def test_unrelated_file_changes_do_not_gate_compatibility(self):
        exe, _, _ = fixture()
        first = self.resolve_fixture(exe)
        exe.image += b'new version metadata'
        second = self.resolve_fixture(exe)
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual(first['rva'], second['rva'])

    def test_changed_fields_and_wrong_qsize_target_are_rejected(self):
        for delta in (22, 36):
            exe, constructor, _ = fixture()
            image = bytearray(exe.image)
            image[constructor + delta] ^= 1
            exe.image = bytes(image)
            with self.subTest(delta=delta), self.assertRaises(IncompatibleError):
                self.resolve_fixture(exe)

    def test_changed_guard_and_early_breakpoint_are_rejected(self):
        for location in (0x1460 + 14, 0x1580 + 14):
            exe, _, _ = fixture()
            image = bytearray(exe.image)
            image[location] ^= 1
            exe.image = bytes(image)
            with self.subTest(location=location), self.assertRaises(IncompatibleError):
                self.resolve_fixture(exe)

    def test_ambiguous_rtti_is_rejected(self):
        exe, _, _ = fixture()
        exe.image += b'.?AVESDHW20GBD9901CoraImp@@\0'
        with self.assertRaises(IncompatibleError):
            self.resolve_fixture(exe)

    def test_invalid_pe_is_incompatible(self):
        with self.assertRaises(IncompatibleError):
            resolve(b'not an executable')

    def test_live_layout_before_and_after(self):
        validate_controllers(controllers())
        validate_controllers(controllers(True), final=True)
        with self.assertRaises(IncompatibleError):
            validate_controllers(controllers(), final=True)

    def test_live_field_stride_vector_and_coordinate_changes_are_rejected(self):
        changes = [lambda d: d[0]['fields'].__setitem__(7, 121),
                   lambda d: d[1].__setitem__('address', '0x1060'),
                   lambda d: d[0]['coordinateVector'].__setitem__(2, 0x2080),
                   lambda d: d[0]['coordinatePairs'].__setitem__(0, (14, 12)),
                   lambda d: d.append(copy.deepcopy(d[0]))]
        for change in changes:
            data = copy.deepcopy(controllers())
            change(data)
            with self.assertRaises(IncompatibleError):
                validate_controllers(data)


if __name__ == '__main__':
    unittest.main()
