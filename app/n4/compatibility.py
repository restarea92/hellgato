"""Resolve the supported Plus layout from structure, never from a version allowlist.

Relative code references, RTTI, unwind boundaries and Qt imports identify the
patch sites. A matching file is only a candidate: its live model is checked again
while the first lookup is paused. The fresh digest binds those two checks to the
same executable; it is not a list of approved releases.
"""

from bisect import bisect_right
import hashlib
import re
import struct


INCOMPATIBLE = 20
MESSAGE = 'This Stream Deck build has an unsupported internal structure.'
ORIGINAL = bytes.fromhex('ba0400000041b802000000')
PATCHED = bytes.fromhex('ba0500000041b802000000')
ORIGINAL_GRID = [(x, y) for y in (12, 172) for x in (13, 232, 451, 670)]
TARGET_GRID = [(13 + col * 164, y) for y in (12, 172) for col in range(5)]


class IncompatibleError(Exception):
    """The executable or live model does not match our understood layout."""


def require(condition, detail):
    if not condition:
        raise IncompatibleError(detail)


def unique(values, description):
    values = list(dict.fromkeys(values))
    require(len(values) == 1, f'{description}: expected one match, found {len(values)}')
    return values[0]


class Executable:
    def __init__(self, data):
        import pefile
        try:
            self.pe = pefile.PE(data=data, fast_load=True)
            require(self.pe.FILE_HEADER.Machine == 0x8664 and
                    self.pe.OPTIONAL_HEADER.Magic == 0x20b, 'Expected AMD64 PE32+')
            require(0 < self.pe.OPTIONAL_HEADER.SizeOfImage <= 256 * 1024 * 1024,
                    'Unexpected image size')
            self.pe.parse_data_directories(directories=[1, 3])
            self.image = self.pe.get_memory_mapped_image()
            self.base = self.pe.OPTIONAL_HEADER.ImageBase
            self.functions = sorted((e.struct.BeginAddress, e.struct.EndAddress)
                                    for e in self.pe.DIRECTORY_ENTRY_EXCEPTION)
            require(all(0 < a < b <= len(self.image) for a, b in self.functions),
                    'Invalid unwind function range')
            self.starts = [a for a, _ in self.functions]
            self.imports = {i.address - self.base: i.name for dll in self.pe.DIRECTORY_ENTRY_IMPORT
                            for i in dll.imports if i.name}
        except (pefile.PEFormatError, AttributeError, ValueError, struct.error) as error:
            raise IncompatibleError(f'Cannot read executable layout: {error}') from error

    def section(self, rva, executable=False, writable=False):
        for section in self.pe.sections:
            if section.VirtualAddress <= rva < section.VirtualAddress + section.Misc_VirtualSize:
                return bool(section.Characteristics & (0x20000000 if executable else
                                                       0x80000000 if writable else 0x40000000))
        return False

    def function(self, rva):
        index = bisect_right(self.starts, rva) - 1
        if index >= 0 and self.functions[index][0] <= rva < self.functions[index][1]:
            return self.functions[index]
        raise IncompatibleError(f'No unwind boundary for {rva:#x}')

    def rel(self, displacement):
        return displacement + 4 + struct.unpack_from('<i', self.image, displacement)[0]

    def locations(self, needle):
        position = self.image.find(needle)
        while position >= 0:
            yield position
            position = self.image.find(needle, position + 1)

    def vtable(self, name):
        descriptor = unique(self.locations(b'.?AV' + name + b'@@\0'), 'RTTI name') - 16
        locators = []
        for at in self.locations(struct.pack('<I', descriptor)):
            start = at - 12
            if start < 0 or start % 4 or start + 24 > len(self.image):
                continue
            signature, offset, cd, _, hierarchy, self_rva = struct.unpack_from('<6I', self.image, start)
            if (signature, offset, cd, self_rva) == (1, 0, 0, start) and self.section(hierarchy):
                locators.append(start)
        locator = unique(locators, 'Complete object locator')
        tables = []
        for at in self.locations(struct.pack('<Q', self.base + locator)):
            if at % 8 or not self.section(at) or at + 40 > len(self.image):
                continue
            targets = struct.unpack_from('<4Q', self.image, at + 8)
            if all(self.section(target - self.base, executable=True) for target in targets):
                tables.append(at + 8)
        return unique(tables, 'Plus vtable')


def resolve(data):
    exe = Executable(data)
    image = exe.image
    cora = exe.vtable(b'ESDHW20GBD9901CoraImp')
    usb = exe.vtable(b'ESDHW20GBD9901Imp')
    qsize = unique((rva for rva, name in exe.imports.items() if name == b'??0QSize@@QEAA@HH@Z'),
                   'QSize constructor import')
    constructors = []
    for at in exe.locations(ORIGINAL):
        if not exe.section(at, executable=True):
            continue
        start, end = exe.function(at)
        prefix = image[start:at]
        # Object allocation, base constructor, class identity, controller kind and
        # field offsets all have to agree, not just the common 4x2 constants.
        pattern = (rb'\x48\x89\x5c\x24\x08\x57\x48\x83\xec\x40\x48\x8b\xd9'
                   rb'\xb9\xe8\x01\x00\x00\xe8....\x48\x8b\xf8\x48\x89\x44\x24\x58'
                   rb'\x48\x8b\xd3\x48\x8b\xc8\xe8....\x48\x8d\x05....\x48\x89\x07'
                   rb'\xc7\x87\xb8\x01\x00\x00\x0e\x00\x00\x00'
                   rb'\x66\xc7\x87\x90\x00\x00\x00\x00\x01')
        if not re.fullmatch(pattern, prefix, re.DOTALL) or exe.rel(start + 45) != cora:
            continue
        cursor = at
        valid = True
        for constants, field in [(ORIGINAL, 0x94), (bytes.fromhex('ba2003000041b8e0010000'), 0xa8),
                                  (bytes.fromhex('ba78000000448bc2'), 0xc4),
                                  (bytes.fromhex('ba64000000448bc2'), 0xcc)]:
            body = constants + bytes.fromhex('488d4c2458ff15')
            if image[cursor:cursor + len(body)] != body:
                valid = False
                break
            call = cursor + len(body)
            store = bytes.fromhex('488b0848898f') + struct.pack('<I', field)
            if exe.rel(call) != qsize or image[call + 4:call + 4 + len(store)] != store:
                valid = False
                break
            cursor = call + 4 + len(store)
        if valid and image[cursor:cursor + 7] == bytes.fromhex('c687c000000001'):
            constructors.append((at, start, end))
    constructor, constructor_start, constructor_end = unique(constructors, 'CORA Plus constructor')

    # Locate the model lookup by its default model reference, then establish the
    # once-only initialization guard and the exact return edge after its footer.
    default = unique(exe.locations(b'20GAA9901\0'), 'Default model identifier')
    lookup_candidates = set()
    for match in re.finditer(rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....', image, re.DOTALL):
        if exe.rel(match.start() + 3) == default and exe.section(match.start(), executable=True):
            lookup_candidates.add(exe.function(match.start()))
    initializers = []
    required_models = [unique(exe.locations(name + b'\0'), name.decode())
                       for name in (b'20GAA9902', b'20GBA9901', b'20GAI9901')]
    for start, end in lookup_candidates:
        code = image[start:end]
        refs = {exe.rel(start + m.start() + 3) for m in re.finditer(
            rb'[\x48\x4c][\x8b\x8d][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....', code, re.DOTALL)}
        refs.update(exe.rel(start + m.start() + 4) for m in re.finditer(
            rb'\xf2\x0f\x10[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....', code, re.DOTALL))
        if not all(model in refs for model in required_models):
            continue
        for tail in re.finditer(rb'\x48\x8d\x0d....\xe8....\x90\xe9....\xcc', code, re.DOTALL):
            at = start + tail.start()
            guard = exe.rel(at + 3)
            footer = exe.rel(at + 8)
            resume = exe.rel(at + 14)
            if not (exe.section(guard, writable=True) and start < resume < at and at + 19 == end):
                continue
            # Before initialization: cmp [guard], eax; jg header; resume lookup.
            if image[resume - 8:resume - 6] != b'\x39\x05' or image[resume - 2] != 0x7f:
                continue
            if exe.rel(resume - 6) != guard:
                continue
            header = resume + struct.unpack_from('<b', image, resume - 1)[0]
            header_bytes = image[header:header + 25]
            if not re.fullmatch(rb'\x48\x8d\x0d....\xe8....\x83\x3d....\xff\x0f\x85....', header_bytes, re.DOTALL):
                continue
            # cmp has an immediate after its displacement; its RIP is one byte later.
            if exe.rel(header + 3) != guard or exe.rel(header + 14) + 1 != guard or exe.rel(header + 21) != resume:
                continue
            fa, fb = exe.function(footer)
            footer_code = image[fa:fb]
            imports = {exe.imports.get(exe.rel(fa + m.start() + 2))
                       for m in re.finditer(rb'\xff[\x15\x25]....', footer_code, re.DOTALL)}
            if fa != footer or not {b'AcquireSRWLockExclusive', b'ReleaseSRWLockExclusive',
                                    b'WakeAllConditionVariable'} <= imports:
                continue
            if b'\xff\xc0\x89\x05' not in footer_code or b'\x89\x03' not in footer_code:
                continue
            initializers.append((at + 12, start, end, fa, fb))
    breakpoint, lookup_start, lookup_end, footer_start, footer_end = unique(initializers, 'Model initialization boundary')
    guards = [{'rva': start, 'original': image[start:end].hex()} for start, end in
              ((constructor_start, constructor_end), (lookup_start, lookup_end), (footer_start, footer_end))]
    return {'schema': 1, 'compatibility': 'structural', 'sha256': hashlib.sha256(data).hexdigest(),
            'rva': constructor, 'original': ORIGINAL.hex(), 'patched': PATCHED.hex(),
            'startupBreakpoint': {'rva': breakpoint, 'original': image[breakpoint:breakpoint + 6].hex()},
            'vtables': {'CORA Plus': cora, 'USB Plus': usb}, 'guards': guards}


def validate_controllers(descriptors, *, final=False):
    require(len(descriptors) == 2, 'Expected exactly two Plus controllers')
    keys = [d for d in descriptors if d['controller'] == 1]
    encoders = [d for d in descriptors if d['controller'] == 2]
    require(len(keys) == len(encoders) == 1, 'Unexpected Plus controller kinds')
    key, encoder = keys[0], encoders[0]
    require(int(encoder['address'], 16) - int(key['address'], 16) == 80, 'Controller stride changed')
    width = key['width']
    require(width in ((5,) if final else (4, 5)), 'Unexpected keypad width')
    require(key['fields'][:13] == [1, width, 2, 0, 0, 800, 480, 120, 120, 120, 120, 100, 100],
            'Keypad descriptor layout changed')
    require(encoder['fields'][:13] == [2, 4, 1, 0, 3, 800, 100, *([0xffffffff] * 6)],
            'Encoder descriptor layout changed')
    for descriptor in descriptors:
        vector = descriptor.get('coordinateVector', [])
        require(len(vector) == 3, 'Missing coordinate vector bounds')
        begin, end, capacity = vector
        pairs = [tuple(pair) for pair in descriptor.get('coordinatePairs', [])]
        require(begin > 0 and begin % 8 == 0 and end == capacity and end - begin == len(pairs) * 8,
                'Coordinate vector bounds changed')
        expected = [(0, 380)] if descriptor is encoder else (TARGET_GRID if final else ORIGINAL_GRID)
        require(pairs == expected, 'Coordinate layout changed')
    return key
