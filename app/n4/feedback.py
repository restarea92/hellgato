"""Bound feedback polling to recent requests while preserving idle waits."""

import struct

from compatibility import require, unique

ACTIVE_MS = 2000
POLL_MS = 16
SLOT_COUNT = 256
PROLOGUE = bytes.fromhex('48895c240848896c24104889742418')
WAIT_BRANCH = bytes.fromhex('741c8b4b2003c9413bcf410f4ccf')
NOTIFY_PROLOGUE = bytes.fromhex('40534883ec20488b4110488bd9904883f801')


def resolve_feedback(exe):
    table = exe.vtable(b'CFeedbackComposer')
    worker = struct.unpack_from('<Q', exe.image, table + 11 * 8)[0] - exe.base
    start, end = exe.function(worker)
    require(start == worker, 'Feedback worker does not start at an unwind boundary')
    code = exe.image[start:end]
    require(code.count(bytes.fromhex('41bf64000000')) == 1, 'Feedback minimum wait changed')
    at = unique((start + i for i in range(len(code)) if code[i:i + len(WAIT_BRANCH)] == WAIT_BRANCH),
                'Feedback wait branch')
    require(exe.image[at + 14:at + 17] == bytes.fromhex('4863d1'), 'Feedback deadline argument changed')
    idle = at + 2 + exe.image[at + 1]
    require(exe.image[idle:idle + 2] == b'\x33\xd2', 'Feedback idle deadline changed')
    tick = unique((rva for rva, name in exe.imports.items() if name == b'GetTickCount64'), 'Monotonic clock import')
    wake = unique((rva for rva, name in exe.imports.items()
                   if name == b'?wakeOne@QWaitCondition@@QEAAXXZ'), 'Feedback wake import')
    requests = []
    for a, b in exe.functions:
        if not start - 0x1000 <= a < start:
            continue
        body = exe.image[a:b]
        if not body.startswith(PROLOGUE) or bytes.fromhex('488b8b80000000') not in body:
            continue
        calls = [a + i for i in range(len(body) - 5) if body[i:i + 2] == b'\xff\x15'
                 and exe.rel(a + i + 2) == wake]
        if len(calls) == 1 and exe.image[calls[0] - 4:calls[0]] == bytes.fromhex('488d4b28'):
            requests.append(a)
    require(len(requests) == 4, 'Expected four feedback request methods')
    notifications = []
    for a, b in exe.functions:
        if not start - 0x400 <= a < start:
            continue
        body = exe.image[a:b]
        if not body.startswith(NOTIFY_PROLOGUE):
            continue
        jumps = [a + i for i in range(len(body) - 5) if body[i:i + 2] == b'\xff\x25'
                 and exe.rel(a + i + 2) == wake]
        if len(jumps) == 1 and bytes.fromhex('488d4b28') in body:
            notifications.append(a)
    notification = unique(notifications, 'Feedback dirty notification')
    requests.append(notification)
    guards = [{'rva': a, 'original': exe.image[a:b].hex()} for a, b in
              [(start, end), *(exe.function(a) for a in requests)]]
    return {'worker': start, 'site': at, 'resume': at + len(WAIT_BRANCH), 'idle': idle,
            'requests': requests, 'prologues': {str(notification): NOTIFY_PROLOGUE.hex()},
            'tickImport': tick, 'wakeImport': wake, 'guards': guards}


class Code:
    def __init__(self):
        self.data = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, value):
        self.data.extend(bytes.fromhex(value) if isinstance(value, str) else value)

    def imm64(self, value):
        self.emit(struct.pack('<Q', value))

    def label(self, name):
        self.labels[name] = len(self.data)

    def branch(self, opcode, label):
        self.emit(opcode)
        self.fixups.append((len(self.data), label))
        self.emit(b'\0' * 4)

    def jump(self, address):
        self.emit('ff2500000000')
        self.imm64(address)

    def finish(self):
        for at, label in self.fixups:
            struct.pack_into('<i', self.data, at, self.labels[label] - at - 4)
        return bytes(self.data)


def preserve(code, entry=False, restore=False):
    # Entry hooks arrive eight bytes off alignment; the worker site is aligned.
    # Keep Windows x64 shadow space and all volatile XMM registers across calls.
    size = 0x88 if entry else 0x80
    if not restore:
        code.emit('9c5051524150415141524153')
        code.emit('4881ec' + struct.pack('<I', size).hex())
    for register in range(6):
        code.emit(bytes([0xf3, 0x0f, 0x6f if restore else 0x7f,
                         0x44 | (register << 3), 0x24, 32 + register * 16]))
    if restore:
        code.emit('4881c4' + struct.pack('<I', size).hex())
        code.emit('415b415a415941585a59589d')


def clock(code, slot):
    code.emit('48b8')
    code.imm64(slot)
    code.emit('ff10')


def mark_code(slots, stats, tick, wake):
    code = Code()
    code.emit('53564883ec28488bd9')
    clock(code, tick)
    code.emit('488bf049bb')
    code.imm64(slots)
    code.emit('b9' + struct.pack('<I', SLOT_COUNT).hex())
    code.label('search')
    code.emit('498b03483bc3')
    code.branch('0f84', 'found')
    code.emit('4885c0')
    code.branch('0f85', 'next')
    code.emit('f0490fb11b')
    code.branch('0f84', 'found')
    code.label('next')
    code.emit('4983c310ffc9')
    code.branch('0f85', 'search')
    code.branch('e9', 'return')
    code.label('found')
    # Atomically publish the request time. Output checks never extend activity.
    code.emit('488bd649875308482bf249ba')
    code.imm64(stats)
    code.emit('f049ff024881fe' + struct.pack('<I', ACTIVE_MS).hex())
    code.branch('0f82', 'return')
    code.emit('f049ff4218488d4b2848b8')
    code.imm64(wake)
    code.emit('ff10')
    code.label('return')
    code.emit('4883c4285e5bc3')
    return code.finish()


def active_code(slots, stats, tick):
    code = Code()
    code.emit('534883ec20488bd9')
    clock(code, tick)
    code.emit('488bd049bb')
    code.imm64(slots)
    code.emit('b9' + struct.pack('<I', SLOT_COUNT).hex())
    code.label('search')
    code.emit('498b03483bc3')
    code.branch('0f84', 'found')
    code.emit('4885c0')
    code.branch('0f84', 'inactive')
    code.emit('4983c310ffc9')
    code.branch('0f85', 'search')
    code.branch('e9', 'inactive')
    code.label('found')
    code.emit('492b53084881fa' + struct.pack('<I', ACTIVE_MS).hex())
    code.branch('0f83', 'inactive')
    code.emit('48b8')
    code.imm64(stats + 8)
    code.emit('f048ff00b801000000')
    code.branch('e9', 'return')
    code.label('inactive')
    code.emit('48b8')
    code.imm64(stats + 16)
    code.emit('f048ff0033c0')
    code.label('return')
    code.emit('4883c4205bc3')
    return code.finish()


def build_feedback(base, allocation, spec):
    # Code occupies one RX page; composer timestamps and counters stay on RW pages.
    slots, stats = allocation + 0x1000, allocation + 0x2000
    mark = mark_code(slots, stats, base + spec['tickImport'], base + spec['wakeImport'])
    active_address = allocation + len(mark)
    active = active_code(slots, stats, base + spec['tickImport'])
    image = bytearray(mark + active)
    patches = []
    for request in spec['requests']:
        original = bytes.fromhex(spec.get('prologues', {}).get(str(request), PROLOGUE.hex()))
        trampoline = allocation + len(image)
        code = Code()
        preserve(code, entry=True)
        code.emit('48b8')
        code.imm64(allocation)
        code.emit('ffd0')
        preserve(code, entry=True, restore=True)
        code.emit(original)
        code.jump(base + request + len(original))
        image.extend(code.finish())
        jump = Code()
        jump.jump(trampoline)
        patches.append((request, original, jump.finish() + b'\x90' * (len(original) - 14)))
    trampoline = allocation + len(image)
    code = Code()
    preserve(code)
    code.emit('488bcb48b8')
    code.imm64(active_address)
    code.emit('ffd085c0')
    code.branch('0f84', 'inactive')
    preserve(code, restore=True)
    code.emit('b9' + struct.pack('<I', POLL_MS).hex())
    code.jump(base + spec['resume'])
    code.label('inactive')
    preserve(code, restore=True)
    code.emit('85ff')
    code.branch('0f85', 'rendered')
    code.jump(base + spec['idle'])
    code.label('rendered')
    code.emit(WAIT_BRANCH[2:])
    code.jump(base + spec['resume'])
    image.extend(code.finish())
    jump = Code()
    jump.jump(trampoline)
    patches.append((spec['site'], WAIT_BRANCH, jump.finish()))
    require(len(image) < 0x1000, 'Feedback code exceeded its executable page')
    return bytes(image), patches, {'allocation': hex(allocation), 'slots': hex(slots), 'stats': hex(stats),
                                  'activeMs': ACTIVE_MS, 'pollMs': POLL_MS, 'slotCount': SLOT_COUNT}


def install_feedback(kernel, process, base, spec, read, write):
    import ctypes as c
    for guard in spec['guards']:
        expected = bytes.fromhex(guard['original'])
        require(read(base + guard['rva'], len(expected)) == expected, 'Loaded feedback code changed')
    allocation = kernel.VirtualAllocEx(process, None, 0x3000, 0x3000, 0x04)
    if not allocation:
        raise c.WinError(c.get_last_error())
    image, patches, report = build_feedback(base, allocation, spec)
    write(allocation, image)
    protection = c.c_ulong()
    if not kernel.VirtualProtectEx(process, allocation, 0x1000, 0x20, c.byref(protection)):
        raise c.WinError(c.get_last_error())
    for rva, original, patched in patches:
        require(read(base + rva, len(original)) == original, 'Feedback patch site changed')
        write(base + rva, patched)
    return report
