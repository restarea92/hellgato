"""Bounded IPC v1 and schema-backed contract checks, with no vendor dependencies."""
from __future__ import annotations

import base64
from collections.abc import Mapping
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Literal, NotRequired, TypeVar, TypedDict, cast


# Only the schema interpreter handles arbitrary JSON; validated public envelopes
# expose typed fields, and contract validation preserves the caller's value type.
JsonObject = dict[str, Any]
ContractValue = TypeVar("ContractValue")


class RpcError(TypedDict):
    code: int
    message: str
    data: NotRequired[object]


class RpcRequest(TypedDict):
    jsonrpc: Literal["2.0"]
    id: NotRequired[str]
    method: str
    # Envelope validation only establishes an object; method schemas validate
    # its keys and values separately. Direct Python callers may supply any keys.
    params: dict[object, object]


class RpcResult(TypedDict):
    jsonrpc: Literal["2.0"]
    id: str | None
    result: object


class RpcFailure(TypedDict):
    jsonrpc: Literal["2.0"]
    id: str | None
    error: RpcError


RpcMessage = RpcRequest | RpcResult | RpcFailure

VERSION = {'major': 1, 'minor': 0}
FEATURES = ['deadlines', 'generation', 'base64-frames']
MAX_MESSAGE_BYTES = 1_500_000
MAX_FRAME_BYTES = 1_048_576
MAX_PIXELS = 16_777_216
MAX_REQUESTS = 32
MAX_QUEUE_BYTES = 4_500_000
MAX_DEADLINE_MS = 60_000
METHODS = {'hello', 'discover', 'open', 'close', 'setOutput', 'setControl', 'getState'}
SCHEMA: JsonObject = json.loads((Path(__file__).parent / 'schemas/contracts.schema.json').read_text(encoding='utf-8'))


class ProtocolError(ValueError):
    def __init__(self, message: str, code: str = 'ERR_IPC_PROTOCOL', rpc_code: int = -32602) -> None:
        super().__init__(message)
        self.code, self.rpc_code = code, rpc_code


def require(condition: object, message: str, code: str = 'ERR_IPC_PROTOCOL', rpc_code: int = -32602) -> None:
    if not condition:
        raise ProtocolError(message, code, rpc_code)



def audit_schema(rule: JsonObject) -> None:
    supported = {'$ref', 'oneOf', 'anyOf', 'const', 'enum', 'type', 'minimum', 'maximum',
                 'minLength', 'maxLength', 'pattern', 'minItems', 'maxItems', 'items', 'required',
                 'additionalProperties', 'maxProperties', 'properties', 'title', 'description'}
    require(type(rule) is dict and set(rule) <= supported, 'Unsupported schema keyword', 'ERR_CONTRACT_SCHEMA')
    require(rule.get('type') in (None, 'null', 'boolean', 'integer', 'string', 'array', 'object'), 'Unsupported schema type', 'ERR_CONTRACT_SCHEMA')
    if '$ref' in rule:
        require(rule['$ref'].startswith('#/$defs/') and rule['$ref'][8:] in SCHEMA['$defs'], 'Unsupported schema reference', 'ERR_CONTRACT_SCHEMA')
    for child in rule.get('properties', {}).values():
        audit_schema(child)
    for name in ('items', 'additionalProperties'):
        if isinstance(rule.get(name), dict):
            audit_schema(rule[name])
    for name in ('oneOf', 'anyOf'):
        for child in rule.get(name, []):
            audit_schema(child)


for _definition in SCHEMA['$defs'].values():
    audit_schema(_definition)

def _equal(left: object, right: object) -> bool:
    # Python considers True == 1; JSON Schema does not.
    return type(left) is type(right) and left == right


def check(value: Any, rule: JsonObject, path: str = '$') -> None:
    supported = {'$ref', 'oneOf', 'anyOf', 'const', 'enum', 'type', 'minimum', 'maximum',
                 'minLength', 'maxLength', 'pattern', 'minItems', 'maxItems', 'items', 'required',
                 'additionalProperties', 'maxProperties', 'properties', 'title', 'description'}
    require(type(rule) is dict and set(rule) <= supported, 'Unsupported schema keyword', 'ERR_CONTRACT_SCHEMA')
    if '$ref' in rule:
        require(rule['$ref'].startswith('#/$defs/') and rule['$ref'][8:] in SCHEMA['$defs'], 'Unsupported schema reference', 'ERR_CONTRACT_SCHEMA')
        return check(value, SCHEMA['$defs'][rule['$ref'][8:]], path)
    variants = rule.get('oneOf', rule.get('anyOf'))
    if variants is not None:
        matches = 0
        for variant in variants:
            try:
                check(value, variant, path)
                matches += 1
            except ProtocolError:
                pass
        require(matches == 1 if 'oneOf' in rule else matches > 0, path + ': invalid payload', 'ERR_CONTRACT')
        return
    if 'const' in rule:
        require(_equal(value, rule['const']), path + ': invalid constant', 'ERR_CONTRACT')
    if 'enum' in rule:
        require(any(_equal(value, item) for item in rule['enum']), path + ': invalid enum', 'ERR_CONTRACT')
    kind = rule.get('type')
    types = {'null': type(None), 'boolean': bool, 'integer': int, 'string': str, 'array': list, 'object': dict}
    require(kind is None or kind in types, 'Unsupported schema type', 'ERR_CONTRACT_SCHEMA')
    if kind in types:
        require(type(value) is types[kind], path + ': invalid type', 'ERR_CONTRACT')
    if kind == 'integer':
        require(abs(value) <= 9007199254740991, path + ': unsafe integer', 'ERR_CONTRACT')
    if 'minimum' in rule:
        require(value >= rule['minimum'], path + ': below minimum', 'ERR_CONTRACT')
    if 'maximum' in rule:
        require(value <= rule['maximum'], path + ': above maximum', 'ERR_CONTRACT')
    if kind == 'string':
        # Match JS UTF-16 length, including astral characters.
        length = len(value.encode('utf-16-le', errors='surrogatepass')) // 2
        require(rule.get('minLength', 0) <= length <= rule.get('maxLength', math.inf), path + ': length bound', 'ERR_CONTRACT')
        if 'pattern' in rule:
            require(re.fullmatch(rule['pattern'], value) is not None, path + ': invalid format', 'ERR_CONTRACT')
    if kind == 'array':
        require(rule.get('minItems', 0) <= len(value) <= rule.get('maxItems', math.inf), path + ': array bound', 'ERR_CONTRACT')
        for index, item in enumerate(value):
            check(item, rule['items'], f'{path}[{index}]')
    if kind == 'object':
        require(len(value) <= rule.get('maxProperties', math.inf), path + ': property bound', 'ERR_CONTRACT')
        require(all(name in value for name in rule.get('required', [])), path + ': missing property', 'ERR_CONTRACT')
        properties = rule.get('properties', {})
        for name, item in value.items():
            if name in properties:
                check(item, properties[name], path + '.' + name)
            else:
                additional = rule.get('additionalProperties', True)
                require(additional is not False, path + ': unknown property', 'ERR_CONTRACT')
                if isinstance(additional, dict):
                    check(item, additional, path + '.' + name)


def jpeg_size(data: bytes) -> tuple[int, int]:
    require(data[:2] == b'\xff\xd8' and data[-2:] == b'\xff\xd9', 'Invalid JPEG signature', 'ERR_CONTRACT')
    offset = 2
    while offset < len(data):
        require(data[offset] == 0xff, 'Invalid JPEG marker', 'ERR_CONTRACT')
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        require(offset < len(data), 'Truncated JPEG marker', 'ERR_CONTRACT')
        marker = data[offset]
        offset += 1
        if marker in (0xd9, 0xda):
            break
        if marker == 0x01 or 0xd0 <= marker <= 0xd7:
            continue
        require(offset + 2 <= len(data), 'Truncated JPEG segment', 'ERR_CONTRACT')
        length = int.from_bytes(data[offset:offset + 2], 'big')
        require(length >= 2 and offset + length <= len(data), 'Invalid JPEG segment size', 'ERR_CONTRACT')
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            require(length >= 8 and 1 <= data[offset + 7] <= 4 and length == 8 + 3 * data[offset + 7], 'Invalid JPEG frame', 'ERR_CONTRACT')
            return int.from_bytes(data[offset + 5:offset + 7], 'big'), int.from_bytes(data[offset + 3:offset + 5], 'big')
        offset += length
    raise ProtocolError('JPEG has no frame dimensions', 'ERR_CONTRACT')


def validate_contract(name: str, value: ContractValue) -> ContractValue:
    require(name in SCHEMA['$defs'], 'Unknown contract', 'ERR_CONTRACT')
    check(value, SCHEMA['$defs'][name])
    # These refinements apply only to object contracts already checked above.
    payload = cast(JsonObject, value)
    if name in ('DeviceDescriptor', 'DeviceCapabilities'):
        capabilities = payload['capabilities'] if name == 'DeviceDescriptor' else payload
        surfaces = capabilities['displays']
        require(len({item['id'] for item in surfaces}) == len(surfaces), 'Duplicate display ID', 'ERR_CONTRACT')
        require(all(item['width'] * item['height'] <= MAX_PIXELS for item in surfaces), 'Surface pixel bound', 'ERR_CONTRACT')
        require(not (capabilities['dials'] == 0 and (capabilities['dialPress'] in ('native', 'pulse') or capabilities['dialRelease'] == 'native')),
                'Dial capability requires dials', 'ERR_CONTRACT')
        require(capabilities['dialPress'] != 'pulse' or capabilities['dialRelease'] == 'unavailable', 'Pulse has no physical release', 'ERR_CONTRACT')
    if name == 'MappingProfile':
        for kind, limit in (('keys', payload['target']['geometry']['keyCount']), ('dials', payload['target']['geometry']['encoderCount'])):
            indices = [index for index in payload['controls'][kind] if index is not None]
            require(len(set(indices)) == len(indices) and all(index < limit for index in indices), 'Invalid control mapping', 'ERR_CONTRACT')
    if name == 'DisplayFrame':
        require(payload['width'] * payload['height'] <= MAX_PIXELS, 'Surface pixel bound', 'ERR_CONTRACT')
        region = payload.get('region', {'x': 0, 'y': 0, 'width': payload['width'], 'height': payload['height']})
        require(region['x'] + region['width'] <= payload['width'] and region['y'] + region['height'] <= payload['height'], 'Region exceeds surface', 'ERR_CONTRACT')
        try:
            decoded = base64.b64decode(payload['data'], validate=True)
        except (ValueError, TypeError) as exc:
            raise ProtocolError('Invalid base64', 'ERR_CONTRACT') from exc
        require(0 < len(decoded) <= MAX_FRAME_BYTES and base64.b64encode(decoded).decode('ascii') == payload['data'], 'Frame byte bound or noncanonical base64', 'ERR_CONTRACT')
        if payload['format'] == 'rgb888':
            require(len(decoded) == region['width'] * region['height'] * 3, 'RGB byte size mismatch', 'ERR_CONTRACT')
        else:
            require(jpeg_size(decoded) == (region['width'], region['height']), 'JPEG dimensions mismatch', 'ERR_CONTRACT')
    return value


def validate_rpc_message(message: object) -> RpcMessage:
    require(type(message) is dict and message.get('jsonrpc') == '2.0', 'Expected JSON-RPC 2.0 object')
    envelope = cast(JsonObject, message)
    if 'method' in envelope:
        require(isinstance(envelope['method'], str) and 0 < len(envelope['method']) <= 64 and type(envelope.get('params')) is dict, 'Invalid RPC method or params')
        if 'id' in envelope:
            require(isinstance(envelope['id'], str) and 0 < len(envelope['id']) <= 128, 'Invalid request ID')
        require('result' not in envelope and 'error' not in envelope, 'Request contains response fields')
    else:
        require('id' in envelope and (envelope['id'] is None or isinstance(envelope['id'], str) and 0 < len(envelope['id']) <= 128), 'Invalid response ID')
        require(('result' in envelope) != ('error' in envelope), 'Response must have result or error')
        if 'error' in envelope:
            item = envelope['error']
            require(type(item) is dict and type(item.get('code')) is int and isinstance(item.get('message'), str) and len(item['message']) <= 1024, 'Invalid RPC error')
    return cast(RpcMessage, envelope)


def check_deadline(params: Mapping[str, object]) -> None:
    deadline = cast(int, params.get('deadlineAt'))
    now = int(time.time() * 1000)
    require(type(deadline) is int and deadline <= now + MAX_DEADLINE_MS, 'Invalid request deadline')
    require(deadline > now, 'Request deadline exceeded before device write', 'ERR_REQUEST_TIMEOUT', -32002)
