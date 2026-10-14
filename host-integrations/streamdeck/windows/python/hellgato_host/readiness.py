"""Inspect an already prepared Windows Stream Deck without modifying the host."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys

from .compatibility import IncompatibleError
from .inspection import loaded_modules, validate_live_target
from .presence import streamdeck_processes
from .startup_install import DLL, MANIFEST, OWNER, PLAN, validate_dll
from .startup_plan import build_plan
from .startup_status import read_status


TARGET_ID = 'streamdeck-plus-ten-key'
PROFILE_ID = 'mirabox-n4-pro-ten-key'
MAX_REQUEST_BYTES = 16 * 1024


class InspectionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def digest(data):
    return hashlib.sha256(data).hexdigest()


def is_supported_platform():
    return os.name == 'nt' and ctypes.sizeof(ctypes.c_void_p) == 8


def require(condition, code, message):
    if not condition:
        raise InspectionError(code, message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object member')
        result[key] = value
    return result


def _decode_json(data):
    def reject_constant(value):
        raise ValueError('Non-finite JSON constant')
    return json.loads(data, object_pairs_hook=_unique_object, parse_constant=reject_constant)


def _one_host():
    hosts = streamdeck_processes()
    require(bool(hosts), 'HOST_NOT_RUNNING', 'Open the prepared Stream Deck installation yourself')
    require(len(hosts) == 1, 'HOST_AMBIGUOUS', 'More than one Stream Deck process is running')
    return hosts[0]


def inspect_readiness(expected_executable, expected_startup_dll, *,
                      target_id=TARGET_ID, profile_id=PROFILE_ID, request_id=None):
    """Return fresh SDK readiness evidence; never install, launch, inject, or repair.

    The two absolute paths are product-owned inputs, not values supplied by the
    SDK request. The reference DLL must come from the verified candidate bundle.
    Failure always returns ready=False and an empty preparation list.
    """
    result = {'schemaVersion': 1, 'requestId': request_id, 'ready': False,
              'targetId': target_id, 'profileId': profile_id, 'preparation': []}
    try:
        require(target_id == TARGET_ID and profile_id == PROFILE_ID,
                'UNSUPPORTED_TARGET', 'This inspector only checks the N4 Pro ten-key target and profile')
        require(is_supported_platform(), 'UNSUPPORTED_PLATFORM', 'Inspection requires 64-bit Windows Python')
        executable, reference = Path(expected_executable), Path(expected_startup_dll)
        require(executable.is_absolute() and reference.is_absolute(), 'INVALID_PATH',
                'Expected executable and startup DLL paths must be absolute')
        executable, reference = executable.resolve(strict=True), reference.resolve(strict=True)
        require(executable.name.casefold() == 'streamdeck.exe', 'WRONG_EXECUTABLE',
                'Expected executable must be the installed StreamDeck.exe')
        host = _one_host()
        require(type(host.pid) is int and host.pid > 0 and type(host.created) is int and host.created > 0,
                'INVALID_HOST_IDENTITY', 'Cannot establish the current host process identity')
        require(host.executable.resolve(strict=True) == executable, 'WRONG_EXECUTABLE',
                'Stream Deck is running from a different installation')
        executable_bytes = executable.read_bytes()
        executable_digest = digest(executable_bytes)
        result['host'] = {'pid': host.pid, 'creationTime': str(host.created),
                          'executableDigest': executable_digest, 'executablePath': str(executable)}
        # Structural resolution is fresh, not an executable version allowlist.
        exact_plan, metadata = build_plan(executable_bytes)
        require(metadata['sha256'] == executable_digest, 'INCOMPATIBLE_HOST',
                'Structural plan does not identify the running executable')
        reference_bytes = reference.read_bytes()
        validate_dll(reference_bytes)
        paths = {name: executable.parent / name for name in (DLL, PLAN, MANIFEST)}
        require(all(path.is_file() for path in paths.values()), 'STARTUP_NOT_INSTALLED',
                'Expected startup shim, plan, or ownership manifest is missing')
        installed_bytes = {name: path.read_bytes() for name, path in paths.items()}
        try:
            record = _decode_json(installed_bytes[MANIFEST])
        except (ValueError, UnicodeError, RecursionError) as error:
            raise InspectionError('INVALID_STARTUP_MANIFEST', 'Startup ownership manifest is invalid') from error
        require(isinstance(record, dict) and record.get('owner') == OWNER and
                isinstance(record.get('files'), dict) and set(record['files']) == {DLL, PLAN},
                'INVALID_STARTUP_MANIFEST', 'Startup files do not have the exact Hellgato ownership record')
        require(record.get('executableSha256') == executable_digest,
                'STARTUP_EXECUTABLE_MISMATCH', 'Installed startup preparation belongs to another executable')
        require(all(record['files'][name] == digest(installed_bytes[name]) for name in (DLL, PLAN)),
                'STARTUP_FILES_CHANGED', 'An installed startup file differs from its ownership manifest')
        require(installed_bytes[DLL] == reference_bytes, 'STARTUP_DLL_MISMATCH',
                'Installed startup shim differs from the candidate bundle')
        require(installed_bytes[PLAN] == exact_plan, 'STARTUP_PLAN_MISMATCH',
                'Installed startup plan does not match this executable and ten-key implementation')
        status = read_status(host)
        require(status is not None, 'HOST_NOT_PREPARED', 'Reopen Stream Deck yourself after startup setup')
        result['nativeStartup'] = {'state': status.state, 'error': status.error}
        require(status.state not in (3, 4), 'INCOMPATIBLE_HOST', 'Native startup rejected this host')
        require(status.ready and status.error == 0, 'HOST_NOT_PREPARED', 'Native startup is not ready')
        modules = loaded_modules(host)
        images = [module for module in modules if module.name.casefold() == 'streamdeck.exe']
        require(len(images) == 1 and images[0].path.resolve(strict=True) == executable and
                images[0].address == status.image,
                'HOST_IMAGE_MISMATCH', 'Readiness image is not the currently loaded Stream Deck executable')
        shims = [module for module in modules if module.path.resolve() == paths[DLL].resolve()]
        require(len(shims) == 1, 'STARTUP_NOT_LOADED', 'The exact installed startup shim is not loaded')
        validate_live_target(host, status)
        require(read_status(host) == status, 'HOST_CHANGED', 'Startup readiness changed during inspection')
        require(executable.read_bytes() == executable_bytes and reference.read_bytes() == reference_bytes and
                all(path.read_bytes() == installed_bytes[name] for name, path in paths.items()),
                'HOST_CHANGED', 'Host or startup files changed during inspection')
        current = _one_host()
        require(current.pid == host.pid and current.created == host.created and
                current.executable.resolve(strict=True) == executable,
                'HOST_CHANGED', 'Stream Deck process identity changed during inspection')
        result.update(ready=True, preparation=[TARGET_ID])
    except InspectionError as error:
        result['error'] = {'code': error.code, 'message': str(error)}
    except IncompatibleError as error:
        result['error'] = {'code': 'INCOMPATIBLE_HOST', 'message': str(error)}
    except Exception as error:
        # A parser/library/Win32 failure is never permission to proceed. Preserve
        # the JSON protocol even for errors such as pefile.PEFormatError.
        result['error'] = {'code': 'INSPECTION_FAILED', 'message': str(error)[:512]}
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InspectionError('INVALID_ARGUMENTS', message)


def main(argv=None, *, stdin=None, stdout=None):
    """One bounded stdin JSON request, one stdout JSON response. No daemon/cache."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    request = {}
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument('operation', choices=['inspect'])
        parser.add_argument('--json', action='store_true', required=True)
        parser.add_argument('--expected-executable', required=True)
        parser.add_argument('--expected-startup-dll', required=True)
        args = parser.parse_args(argv)
        raw = getattr(stdin, 'buffer', stdin).read(MAX_REQUEST_BYTES + 1)
        require(len(raw if isinstance(raw, bytes) else raw.encode('utf-8')) <= MAX_REQUEST_BYTES, 'INVALID_REQUEST', 'Readiness request exceeds the frame limit')
        try:
            request = _decode_json(raw)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise InspectionError('INVALID_REQUEST', 'Expected one valid JSON readiness request') from error
        require(isinstance(request, dict) and set(request) == {'schemaVersion', 'requestId', 'targetId', 'profileId'},
                'INVALID_REQUEST', 'Unexpected readiness request fields')
        require(type(request['schemaVersion']) is int and request['schemaVersion'] == 1,
                'INVALID_REQUEST', 'Unsupported readiness request schema')
        require(all(isinstance(request[key], str) and 0 < len(request[key]) <= 128
                    and not any(ord(char) < 32 for char in request[key])
                    for key in ('requestId', 'targetId', 'profileId')),
                'INVALID_REQUEST', 'Readiness request identifiers must be bounded strings')
        result = inspect_readiness(args.expected_executable, args.expected_startup_dll,
                                   target_id=request['targetId'], profile_id=request['profileId'],
                                   request_id=request['requestId'])
        code = 0
    except InspectionError as error:
        safe = ({key: value for key, value in request.items()
                 if isinstance(value, str) and len(value) <= 128}
                if isinstance(request, dict) else {})
        result = {'schemaVersion': 1, 'requestId': safe.get('requestId') if isinstance(safe.get('requestId'), str) else None,
                  'ready': False, 'targetId': safe.get('targetId') if isinstance(safe.get('targetId'), str) else None,
                  'profileId': safe.get('profileId') if isinstance(safe.get('profileId'), str) else None,
                  'preparation': [], 'error': {'code': error.code, 'message': str(error)}}
        code = 2
    stdout.write(json.dumps(result, separators=(',', ':'), ensure_ascii=True) + '\n')
    stdout.flush()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
