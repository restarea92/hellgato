"""Build an executable-bound plan for the native pre-initialization loader."""

import hashlib
import struct

from .compatibility import Executable, require, resolve
from .feedback import build_feedback, resolve_feedback


MAGIC = b'HGSTART1'
SCHEMA = 1
IMAGE_SENTINEL = 0x111100000000
ALLOCATION_SENTINEL = 0x222200000000


def relocations(data, image_size):
    result = []
    offset = 0
    while offset + 8 <= len(data):
        value = struct.unpack_from('<Q', data, offset)[0]
        if IMAGE_SENTINEL <= value < IMAGE_SENTINEL + image_size:
            result.append((offset, 1, value - IMAGE_SENTINEL))
        elif ALLOCATION_SENTINEL <= value < ALLOCATION_SENTINEL + 0x3000:
            result.append((offset, 2, value - ALLOCATION_SENTINEL))
        else:
            offset += 1
            continue
        offset += 8
    return result


def relocate(data, entries, image_base, allocation):
    result = bytearray(data)
    for offset, kind, addend in entries:
        require(kind in (1, 2) and 0 <= offset <= len(result) - 8, 'Invalid startup relocation')
        struct.pack_into('<Q', result, offset, (image_base if kind == 1 else allocation) + addend)
    return bytes(result)


def encode_relocations(entries):
    return struct.pack('<I', len(entries)) + b''.join(struct.pack('<IIQ', *entry) for entry in entries)


def feedback_plan(exe, spec):
    size = len(exe.image)
    code, patches, _ = build_feedback(IMAGE_SENTINEL, ALLOCATION_SENTINEL, spec)
    code_relocations = relocations(code, size)
    patch_relocations = [relocations(desired, size) for _, _, desired in patches]
    # The machine-code generator owns absolute operands. Check extraction against
    # a separately generated image so no address can accidentally remain fixed.
    base, allocation = 0x7FF612340000, 0x23712340000
    rebuilt, relocated_patches, _ = build_feedback(base, allocation, spec)
    require(relocate(code, code_relocations, base, allocation) == rebuilt,
            'Feedback code has an unresolved startup relocation')
    for (_, _, desired), entries, (_, _, rebuilt_patch) in zip(patches, patch_relocations, relocated_patches):
        require(relocate(desired, entries, base, allocation) == rebuilt_patch,
                'Feedback hook has an unresolved startup relocation')
    return code, code_relocations, [(*patch, entries) for patch, entries in zip(patches, patch_relocations)]


def build_plan(data):
    exe = Executable(data)
    spec = resolve(data)
    feedback = resolve_feedback(exe)
    gate = spec['startupBreakpoint']['rva'] - 5
    require(exe.image[gate] == 0xE8 and exe.image[gate - 7:gate - 4] == b'\x48\x8d\x0d',
            'Model publication gate changed')
    model_guard = exe.rel(gate - 4)
    require(exe.section(model_guard, writable=True), 'Model initialization guard is not writable')
    code, code_relocations, patches = feedback_plan(exe, feedback)
    patches.insert(0, (spec['rva'], bytes.fromhex(spec['original']), bytes.fromhex(spec['patched']), []))
    guards = spec['guards'] + feedback['guards']
    header = struct.pack('<8s7I32sI', MAGIC, SCHEMA, exe.pe.OPTIONAL_HEADER.SizeOfImage,
                         exe.pe.FILE_HEADER.TimeDateStamp, exe.pe.OPTIONAL_HEADER.AddressOfEntryPoint,
                         spec['rva'], gate, model_guard, hashlib.sha256(data).digest(), len(guards))
    output = bytearray(header)
    for guard in guards:
        original = bytes.fromhex(guard['original'])
        output.extend(struct.pack('<II', guard['rva'], len(original)) + original)
    output.extend(struct.pack('<I', len(code)) + code + encode_relocations(code_relocations))
    output.extend(struct.pack('<I', len(patches)))
    for rva, original, desired, entries in patches:
        require(len(original) == len(desired), 'Startup patch length changed')
        output.extend(struct.pack('<II', rva, len(original)) + original + desired + encode_relocations(entries))
    return bytes(output), {'schema': SCHEMA, 'sha256': spec['sha256'], 'modelGateRva': gate,
                           'modelGuardRva': model_guard, 'patchCount': len(patches), 'bytes': len(output)}
