"""Experimental firmware admission, never a claim of verified hardware support."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Literal, TypedDict, cast

from ...protocol import ProtocolError

POLICY_PATH = Path(__file__).with_name('firmware-policy.json')
MAX_POLICY_BYTES = 16 * 1024


class FirmwarePolicy(TypedDict):
    schemaVersion: Literal[1]
    policyId: str
    adapterId: Literal['mirabox-n4-pro']
    modelId: Literal['n4-pro']
    supportStatus: Literal['experimental']
    physicalHardwareVerified: Literal[False]
    allowedFirmware: list[str]
    allowUnverifiedFirmware: Literal[True]
    evidenceKind: Literal['historical-product-baseline-only']
    sourceEvidence: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate policy field')
        result[key] = value
    return result


def firmware_policy() -> FirmwarePolicy:
    """Read the packaged, manifest-bound resource. No external override exists."""
    try:
        with POLICY_PATH.open('rb') as stream:
            data = stream.read(MAX_POLICY_BYTES + 1)
        if len(data) > MAX_POLICY_BYTES:
            raise ValueError('Policy size bound')
        value: object = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object)
        fields = {'schemaVersion', 'policyId', 'adapterId', 'modelId', 'supportStatus',
                  'physicalHardwareVerified', 'allowedFirmware', 'allowUnverifiedFirmware', 'evidenceKind', 'sourceEvidence'}
        if (not isinstance(value, dict) or set(value) != fields
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['policyId'] != 'mirabox-n4-pro-experimental-v1'
                or value['adapterId'] != 'mirabox-n4-pro' or value['modelId'] != 'n4-pro'
                or value['supportStatus'] != 'experimental' or value['physicalHardwareVerified'] is not False
                or value['allowUnverifiedFirmware'] is not True
                or value['evidenceKind'] != 'historical-product-baseline-only'
                or value['sourceEvidence'] != 'docs/releases/0.1.0.md'):
            raise ValueError('Unexpected firmware policy identity or evidence')
        versions = value['allowedFirmware']
        if (not isinstance(versions, list) or not 1 <= len(versions) <= 32
                or any(not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}', item)
                       for item in versions)
                or len(set(versions)) != len(versions)):
            raise ValueError('Expected distinct exact firmware strings')
        return cast(FirmwarePolicy, value)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ProtocolError('The bundled experimental firmware policy is missing or invalid',
                            'ERR_FIRMWARE_POLICY') from error


def require_candidate_firmware(value: object) -> str:
    firmware_policy()
    if (not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.strip()
            or re.search(r'[\x00-\x1f\x7f]', value)):
        raise ProtocolError('The device did not return a valid firmware version', 'ERR_FIRMWARE_UNVERIFIED')
    return value
