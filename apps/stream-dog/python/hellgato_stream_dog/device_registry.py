"""Remember physical devices without changing their assigned CORA identity."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


MODEL = 'StreamDockN4Pro'
NAME = 'Mirabox N4 Pro'


class DeviceIdentityConflict(ValueError):
    def __init__(self, expected):
        self.expected = dict(expected)
        super().__init__(f"{expected['virtualSerial']} is assigned to a different registered device")


def _identity(value):
    return type(value) is int and 1 <= value <= 999999


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class DeviceRegistry:
    def __init__(self, state_directory, identity, dock_identity=1):
        if not _identity(identity) or not _identity(dock_identity):
            raise ValueError('Invalid assigned device identity')
        self.path = Path(state_directory) / 'devices.json'
        self.identity = identity
        self.dock_identity = dock_identity
        self._devices = []
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('version') != 1
                    or not isinstance(data.get('devices'), list)):
                raise ValueError('Unsupported device registry; the file was preserved')
            identities, identifiers = set(), set()
            for record in data['devices']:
                self._validate(record)
                if record['id'] in identifiers or record['identity'] in identities:
                    raise ValueError('Duplicate device registry identity; the file was preserved')
                identifiers.add(record['id'])
                identities.add(record['identity'])
                self._devices.append(dict(record))

    @staticmethod
    def _validate(record):
        if not isinstance(record, dict):
            raise ValueError('Invalid device registry entry; the file was preserved')
        text_fields = ('id', 'name', 'model', 'firstSeen', 'lastSeen')
        if (any(not isinstance(record.get(key), str) or not record[key] for key in text_fields)
                or not _identity(record.get('identity'))
                or not _identity(record.get('dockIdentity'))
                or record.get('virtualSerial') != f"HGMOCK{record['identity']:06d}"
                or record.get('identitySource') not in ('serial', 'usb-path', 'sdk')
                or (record.get('identitySource') != 'sdk' and
                    any(type(record.get(key)) is not int or not 0 <= record[key] <= 65535
                        for key in ('vendorId', 'productId')))):
            raise ValueError('Invalid device registry entry; the file was preserved')
        serial = record.get('physicalSerial')
        if ((record['identitySource'] == 'serial' and (not isinstance(serial, str) or not serial))
                or (record['identitySource'] in ('usb-path', 'sdk') and serial is not None)
                or (record['identitySource'] == 'sdk' and
                    (not re.fullmatch(r'n4-[a-f0-9]{64}', record['id']) or
                     any(key in record for key in ('vendorId', 'productId'))))
                or 'connected' in record):
            raise ValueError('Invalid physical device identity; the file was preserved')

    def devices(self, connected_device_id=None):
        return [{**record, 'connected': record['id'] == connected_device_id}
                for record in self._devices]

    def prepare(self, device):
        serial = device.serial_number
        if serial is not None and not isinstance(serial, str):
            raise ValueError('The physical device serial must be text')
        serial = serial or None
        source = 'serial' if serial else 'usb-path'
        if serial:
            physical = serial
        else:
            path = device.path
            if not isinstance(path, (str, bytes)) or not path:
                raise ValueError('The device has neither a physical serial nor a USB path')
            physical = (path.encode('utf-8') if isinstance(path, str) else path).hex()
        fingerprint = json.dumps([MODEL, device.vendor_id, device.product_id, source, physical],
                                 ensure_ascii=True, separators=(',', ':'))
        identifier = 'n4-' + hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()
        for record in self._devices:
            if record['id'] == identifier:
                if record['identity'] != self.identity or record['dockIdentity'] != self.dock_identity:
                    raise DeviceIdentityConflict(record)
                return dict(record)
            if record['identity'] == self.identity:
                raise DeviceIdentityConflict(record)
        now = _timestamp()
        record = {'id': identifier, 'name': NAME, 'model': MODEL,
                  'physicalSerial': serial, 'identitySource': source,
                  'vendorId': device.vendor_id, 'productId': device.product_id,
                  'identity': self.identity, 'dockIdentity': self.dock_identity,
                  'virtualSerial': f'HGMOCK{self.identity:06d}',
                  'firstSeen': now, 'lastSeen': now}
        self._validate(record)
        return record

    def prepare_descriptor(self, descriptor):
        """Bind worker discovery to the existing virtual identity without USB access."""
        if (not isinstance(descriptor, dict) or descriptor.get('modelId') != 'n4-pro'
                or not isinstance(descriptor.get('id'), str)
                or not re.fullmatch(r'n4-[a-f0-9]{64}', descriptor['id'])):
            raise ValueError('Unsupported discovered device')
        identifier = descriptor['id']
        for record in self._devices:
            if record['id'] == identifier:
                if record['identity'] != self.identity or record['dockIdentity'] != self.dock_identity:
                    raise DeviceIdentityConflict(record)
                return dict(record)
            if record['identity'] == self.identity:
                raise DeviceIdentityConflict(record)
        now = _timestamp()
        record = {'id': identifier, 'name': NAME, 'model': MODEL, 'physicalSerial': None,
                  'identitySource': 'sdk', 'identity': self.identity, 'dockIdentity': self.dock_identity,
                  'virtualSerial': f'HGMOCK{self.identity:06d}', 'firstSeen': now, 'lastSeen': now}
        self._validate(record)
        return record

    def remember(self, record):
        record = {**record, 'lastSeen': _timestamp()}
        self._validate(record)
        if record['identity'] != self.identity or record['dockIdentity'] != self.dock_identity:
            raise ValueError('The device does not match the active bridge identity')
        updated = []
        replaced = False
        for previous in self._devices:
            if previous['id'] == record['id']:
                if previous['identity'] != record['identity']:
                    raise DeviceIdentityConflict(previous)
                updated.append(record)
                replaced = True
            elif previous['identity'] == record['identity']:
                raise DeviceIdentityConflict(previous)
            else:
                updated.append(previous)
        if not replaced:
            updated.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='devices-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'version': 1, 'devices': updated}, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._devices = updated
        return dict(record)
