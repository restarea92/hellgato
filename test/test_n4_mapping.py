import unittest
from enum import Enum, IntEnum
from app.n4.mapping import input_command


class EventType(Enum):
    BUTTON = 'button'
    KNOB_ROTATE = 'knob_rotate'


class KnobId(Enum):
    KNOB_1 = 'knob_1'


class Direction(Enum):
    LEFT = 'left'


class ButtonKey(IntEnum):
    KEY_10 = 10

class MappingTests(unittest.TestCase):
    def test_main_keys_are_independent_and_secondary_keys_are_excluded(self):
        self.assertEqual([input_command({'event_type': 'button', 'key': i, 'state': 1}) for i in range(1, 11)],
                         [f'key {i} down' for i in range(10)])
        for key in (0, 11, 12, 15, None, '1'):
            self.assertIsNone(input_command({'event_type': 'button', 'key': key, 'state': 1}))

    def test_encoder_direction_and_release(self):
        for i in range(4):
            event = {'event_type': 'knob_rotate', 'knob_id': f'knob_{i + 1}'}
            self.assertEqual(input_command({**event, 'direction': 'left'}), f'rotate {i} -1')
            self.assertEqual(input_command({**event, 'direction': 'right'}), f'rotate {i} 1')
            self.assertEqual(input_command({**event, 'event_type': 'knob_press', 'state': 0}), f'press {i} up')

    def test_unvalidated_input_is_not_forwarded(self):
        for event in ({'event_type': 'touch_point', 'x': 300, 'y': 200},
                      {'event_type': 'knob_rotate', 'knob_id': 'knob_5', 'direction': 'left'},
                      {'event_type': 'button', 'key': 1, 'state': 2}):
            self.assertIsNone(input_command(event))

    def test_sdk_enum_values_are_forwarded(self):
        self.assertEqual(input_command({'event_type': EventType.BUTTON, 'key': ButtonKey.KEY_10, 'state': 1}),
                         'key 9 down')
        self.assertEqual(input_command({'event_type': EventType.KNOB_ROTATE,
                                        'knob_id': KnobId.KNOB_1, 'direction': Direction.LEFT}),
                         'rotate 0 -1')

if __name__ == '__main__':
    unittest.main()
