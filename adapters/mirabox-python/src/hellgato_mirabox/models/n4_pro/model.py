"""N4-specific geometry and input normalization, preserving existing physical numbering."""
from enum import Enum


def scalar(value):
    return value.value if isinstance(value, Enum) else value


def descriptor(device_id, firmware=''):
    return {'id': device_id, 'adapterId': 'mirabox-n4-pro', 'modelId': 'n4-pro', 'firmware': firmware,
            'capabilities': {'keys': 10, 'dials': 4, 'touch': {'width': 800, 'height': 100},
            'displays': [*({'id': f'key:{index}', 'width': 120, 'height': 120, 'format': 'jpeg', 'partial': False} for index in range(10)),
                         {'id': 'touch', 'width': 800, 'height': 100, 'format': 'jpeg', 'partial': True},
                         {'id': 'lcd', 'width': 800, 'height': 480, 'format': 'jpeg', 'partial': False}],
            'dialPress': 'pulse', 'dialRelease': 'unavailable',
            'features': {**{name: {'status': 'native', 'limitations': ['Mechanism implemented; candidate IPC physical parity unverified']} for name in ('brightness', 'clear', 'fill', 'sleep', 'wake')}, **{name: {'status': 'unverified', 'limitations': [limitation]} for name, limitation in (
                ('display', 'New IPC path requires N4 hardware parity verification'),
                ('touch', 'Existing 112-to-100 calibration; new worker unverified'),
                ('reconnect', 'Explicit close/open required; automatic display restoration not yet verified'),
                ('screensaver', 'Saved animation playback remains on legacy product engine'))}}}}


def input_event(event):
    kind = scalar(event.get('event_type'))
    if kind == 'button':
        key = scalar(event.get('key'))
        if type(key) is int and 1 <= key <= 10 and type(event.get('state')) is int and event['state'] in (0, 1):
            return {'type': 'key', 'index': key - 1, 'state': 'down' if event['state'] else 'up'}
    if kind in ('knob_rotate', 'knob_press'):
        index = {f'knob_{index + 1}': index for index in range(4)}.get(scalar(event.get('knob_id')))
        if index is None:
            return None
        if kind == 'knob_rotate':
            delta = {'left': -1, 'right': 1}.get(scalar(event.get('direction')))
            if delta is not None:
                return {'type': 'dial', 'kind': 'rotate', 'index': index, 'delta': delta}
        elif event.get('state') == 1:
            return {'type': 'dial', 'kind': 'pulse', 'index': index}
    return None


def touch_event(command):
    if command is None:
        return None
    parts = command.split()
    event = {'type': 'touch', 'gesture': parts[1], 'x': int(parts[2]), 'y': int(parts[3])}
    if parts[1] == 'swipe':
        event.update(endX=int(parts[4]), endY=int(parts[5]))
    return event
