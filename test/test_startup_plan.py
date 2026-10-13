import hashlib
import os
from pathlib import Path
import re
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/n4'))
import startup_plan
from compatibility import Executable, IncompatibleError, ORIGINAL, PATCHED, resolve
from feedback import build_feedback, resolve_feedback


class PlanReader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def take(self, count):
        if count < 0 or self.offset + count > len(self.data):
            raise AssertionError('Startup plan field exceeds the binary bounds')
        result = self.data[self.offset:self.offset + count]
        self.offset += count
        return result

    def unpack(self, format):
        return struct.unpack(format, self.take(struct.calcsize(format)))

    def relocations(self):
        count, = self.unpack('<I')
        return [self.unpack('<IIQ') for _ in range(count)]


class StartupRelocationTests(unittest.TestCase):
    def test_unaligned_absolute_operands_roundtrip_and_exclude_outside_addresses(self):
        image, allocation = startup_plan.IMAGE_SENTINEL, startup_plan.ALLOCATION_SENTINEL
        data = (b'\x90' + struct.pack('<Q', image + 0x123) + b'\x90\x90' +
                struct.pack('<Q', allocation + 0x2ff8) + struct.pack('<Q', image + 0x1000) +
                struct.pack('<Q', allocation + 0x3000))
        entries = startup_plan.relocations(data, 0x1000)
        self.assertEqual(entries, [(1, 1, 0x123), (11, 2, 0x2ff8)])
        encoded = PlanReader(startup_plan.encode_relocations(entries))
        self.assertEqual(encoded.relocations(), entries)
        self.assertEqual(encoded.offset, len(encoded.data))
        for base, allocated in ((0x7ff712340000, 0x23712340000), (0x180000000, 0x340000000)):
            relocated = startup_plan.relocate(data, entries, base, allocated)
            self.assertEqual(struct.unpack_from('<Q', relocated, 1)[0], base + 0x123)
            self.assertEqual(struct.unpack_from('<Q', relocated, 11)[0], allocated + 0x2ff8)
            self.assertEqual(relocated[19:], data[19:])

    def test_corrupt_relocation_kinds_and_write_bounds_are_rejected(self):
        for entry in ((-1, 1, 0), (1, 1, 0), (0, 0, 0), (0, 3, 0)):
            with self.subTest(entry=entry), self.assertRaisesRegex(IncompatibleError, 'Invalid startup relocation'):
                startup_plan.relocate(bytes(8), [entry], 0x100000, 0x200000)
        with self.assertRaisesRegex(IncompatibleError, 'Invalid startup relocation'):
            startup_plan.relocate(bytes(7), [(0, 1, 0)], 0x100000, 0x200000)

    def test_missing_absolute_operand_is_rejected_by_relocation_self_check(self):
        spec = {'tickImport': 0x100, 'wakeImport': 0x108, 'requests': [0x400, 0x500, 0x600, 0x700],
                'site': 0x800, 'resume': 0x80e, 'idle': 0x820}
        with patch.object(startup_plan, 'relocations', return_value=[]):
            with self.assertRaisesRegex(IncompatibleError, 'unresolved startup relocation'):
                startup_plan.feedback_plan(SimpleNamespace(image=bytes(0x2000)), spec)


class InstalledStartupPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Elgato/StreamDeck/StreamDeck.exe'
        if not path.is_file():
            raise unittest.SkipTest('Installed Stream Deck executable is unavailable')
        cls.data = path.read_bytes()
        cls.exe = Executable(cls.data)
        cls.spec = resolve(cls.data)
        cls.feedback = resolve_feedback(cls.exe)
        cls.plan, cls.report = startup_plan.build_plan(cls.data)

    def test_installed_binary_plan_is_bound_and_gates_before_model_publication(self):
        reader = PlanReader(self.plan)
        magic, schema, image_size, timestamp, entrypoint, constructor, gate, guard, digest, guard_count = \
            reader.unpack('<8s7I32sI')
        self.assertEqual((magic, schema), (b'HGSTART1', 1))
        self.assertEqual(image_size, self.exe.pe.OPTIONAL_HEADER.SizeOfImage)
        self.assertEqual(timestamp, self.exe.pe.FILE_HEADER.TimeDateStamp)
        self.assertEqual(entrypoint, self.exe.pe.OPTIONAL_HEADER.AddressOfEntryPoint)
        self.assertEqual(digest, hashlib.sha256(self.data).digest())
        self.assertEqual(constructor, self.spec['rva'])
        self.assertEqual(gate + 5, self.spec['startupBreakpoint']['rva'])
        self.assertEqual(self.exe.image[gate - 7:gate - 4], b'\x48\x8d\x0d')
        self.assertEqual(self.exe.image[gate], 0xe8)
        self.assertEqual(guard, gate + struct.unpack_from('<i', self.exe.image, gate - 4)[0])
        self.assertTrue(self.exe.section(guard, writable=True))
        footer = gate + 5 + struct.unpack_from('<i', self.exe.image, gate + 1)[0]
        start, end = self.exe.function(footer)
        self.assertEqual(start, footer)
        footer_code = self.exe.image[start:end]
        imports = {self.exe.imports.get(self.exe.rel(start + match.start() + 2))
                   for match in re.finditer(rb'\xff[\x15\x25]....', footer_code, re.DOTALL)}
        self.assertTrue({b'AcquireSRWLockExclusive', b'ReleaseSRWLockExclusive',
                         b'WakeAllConditionVariable'} <= imports)
        self.assertIn(b'\x89\x03', footer_code)
        guards = []
        for _ in range(guard_count):
            rva, length = reader.unpack('<II')
            original = reader.take(length)
            self.assertLessEqual(rva + length, image_size)
            self.assertEqual(original, self.exe.image[rva:rva + length])
            guards.append((rva, length))
        self.assertTrue(any(rva <= gate < rva + length for rva, length in guards))
        length, = reader.unpack('<I')
        code = reader.take(length)
        code_relocations = reader.relocations()
        count, = reader.unpack('<I')
        patches = []
        for _ in range(count):
            rva, length = reader.unpack('<II')
            original, desired = reader.take(length), reader.take(length)
            entries = reader.relocations()
            self.assertEqual(original, self.exe.image[rva:rva + length])
            patches.append((rva, original, desired, entries))
        self.assertEqual(reader.offset, len(self.plan))
        self.assertEqual(patches[0], (constructor, ORIGINAL, PATCHED, []))
        self.assertEqual(count, self.report['patchCount'])
        for blob, entries in [(code, code_relocations), *[(p[2], p[3]) for p in patches]]:
            occupied = set()
            for offset, kind, addend in entries:
                self.assertIn(kind, (1, 2))
                self.assertLessEqual(offset + 8, len(blob))
                self.assertLess(addend, image_size if kind == 1 else 0x3000)
                self.assertTrue(occupied.isdisjoint(range(offset, offset + 8)))
                occupied.update(range(offset, offset + 8))
        for base, allocation in ((0x7ff812340000, 0x24712340000), (0x180000000, 0x350000000)):
            expected_code, expected_patches, _ = build_feedback(base, allocation, self.feedback)
            self.assertEqual(startup_plan.relocate(code, code_relocations, base, allocation), expected_code)
            self.assertEqual(len(patches) - 1, len(expected_patches))
            for (rva, original, desired, entries), expected in zip(patches[1:], expected_patches):
                self.assertEqual((rva, original, startup_plan.relocate(desired, entries, base, allocation)), expected)

    def test_modified_publication_call_is_rejected_without_writing_executable(self):
        modified = bytearray(self.data)
        offset = self.exe.pe.get_offset_from_rva(self.report['modelGateRva'])
        modified[offset] = 0x90
        with self.assertRaises(IncompatibleError):
            startup_plan.build_plan(bytes(modified))


if __name__ == '__main__':
    unittest.main()
