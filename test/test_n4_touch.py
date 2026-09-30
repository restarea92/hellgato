import unittest
from app.n4.touch import TouchGesture


class TouchTests(unittest.TestCase):
    def test_recorded_leftward_swipe_uses_coordinates(self):
        touch = TouchGesture()
        touch.point(633, 90, 0)
        touch.point(470, 72, 0.1)
        touch.point(9, 106, 0.24)
        self.assertEqual(touch.finish(0.26, 'right'), 'touch swipe 633 80 9 94')
        self.assertIsNone(touch.finish(0.27))

    def test_single_sample_swipe_uses_observed_direction(self):
        touch = TouchGesture()
        touch.point(594, 80, 0)
        self.assertEqual(touch.finish(0.02, 'right'), 'touch swipe 700 50 100 50')

    def test_tap_hold_and_stale_release(self):
        touch = TouchGesture()
        touch.point(166, 78, 0)
        self.assertEqual(touch.finish(0.1), 'touch tap 166 69')
        for i in range(7):
            touch.point(285, 64, 1 + i * 0.1)
        self.assertEqual(touch.finish(1.65), 'touch hold 285 57')
        touch.point(200, 50, 2)
        self.assertIsNone(touch.finish(3))
