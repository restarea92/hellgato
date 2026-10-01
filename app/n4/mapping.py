"""Translate normalized SDK input into CORA server commands."""

from enum import Enum


def enum_value(item):
    return item.value if isinstance(item, Enum) else item

def input_commands(event):
    command = input_command(event)
    if command is None:
        return []
    # N4 Pro knob clicks report a press pulse without a separate release.
    if enum_value(event.get('event_type')) == 'knob_press' and event.get('state') == 1:
        return [command, command.removesuffix('down') + 'up']
    return [command]


def input_command(event):
    kind = enum_value(event.get('event_type'))
    if kind == 'button':
        key = enum_value(event.get('key'))
        if type(key) is not int or not 1 <= key <= 10:
            return None
        if event.get('state') not in (0, 1):
            return None
        return f"key {key - 1} {'down' if event['state'] else 'up'}"
    if kind in ('knob_rotate', 'knob_press'):
        knobs = {'knob_1': 0, 'knob_2': 1, 'knob_3': 2, 'knob_4': 3}
        index = knobs.get(enum_value(event.get('knob_id')))
        if index is None:
            return None
        if kind == 'knob_rotate':
            ticks = {'left': -1, 'right': 1}.get(enum_value(event.get('direction')))
            return None if ticks is None else f'rotate {index} {ticks}'
        if event.get('state') not in (0, 1):
            return None
        return f"press {index} {'down' if event['state'] else 'up'}"
    # Physical touch coordinates need calibration before routing to the strip.
    return None
