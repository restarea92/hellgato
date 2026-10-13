import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.n4.shutdown import close_device, install_interruptible_heartbeat


class Device:
    def __init__(self):
        self.transport = SimpleNamespace(_handle=42, _is_open=True, heartbeat=Mock(), close=Mock())
        self.heartbeat_thread = None
        self.read_thread = None
        self.run_heartbeat_thread = False
        self.run_read_thread = True
        self._notify_on_close = True
        self.gif_controller = SimpleNamespace(stop_animation_loop=Mock(), close=Mock())
        self.disconnected = Mock()

    def close(self, notify=True):
        self.gif_controller.close()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(2)
        if self.read_thread:
            self.read_thread.join(2)
        if notify and self._notify_on_close:
            self.disconnected()
        if not self.read_thread or not self.read_thread.is_alive():
            self.transport.close()


def native(disconnect=0, destroy=0):
    return SimpleNamespace(transport_disconnected=Mock(return_value=disconnect),
                           transport_destroy=Mock(return_value=destroy))


class ShutdownTests(unittest.TestCase):
    def test_shutdown_interrupts_ten_second_heartbeat_wait_before_disconnect(self):
        device = Device()
        first_heartbeat = threading.Event()
        device.transport.heartbeat.side_effect = first_heartbeat.set
        install_interruptible_heartbeat(device, initial_delay=0)
        device.run_heartbeat_thread = True
        device.heartbeat_thread = threading.Thread(target=device._heartbeat_worker)
        device.heartbeat_thread.start()
        self.assertTrue(first_heartbeat.wait(1))
        transport = native()

        def disconnected(handle):
            self.assertFalse(device.heartbeat_thread.is_alive())
            self.assertFalse(device.run_read_thread)
            device.gif_controller.close.assert_called_once()
            return 0

        transport.transport_disconnected.side_effect = disconnected
        started = time.monotonic()
        self.assertTrue(close_device(device, native=transport, report=lambda event: None))
        self.assertLess(time.monotonic() - started, .5)
        transport.transport_disconnected.assert_called_once_with(42)
        transport.transport_destroy.assert_called_once_with(42)
        self.assertIsNone(device.transport._handle)
        self.assertFalse(device._notify_on_close)

    def test_close_before_first_heartbeat_wakes_without_sending_a_packet(self):
        device = Device()
        stop = install_interruptible_heartbeat(device, initial_delay=10)
        device.run_heartbeat_thread = True
        device.heartbeat_thread = threading.Thread(target=device._heartbeat_worker)
        device.heartbeat_thread.start()
        close_device(device, native=native(), report=lambda event: None)
        self.assertTrue(stop.is_set())
        self.assertFalse(device.heartbeat_thread.is_alive())
        device.transport.heartbeat.assert_not_called()

    def test_physical_removal_skips_disconnect_but_releases_transport(self):
        device, transport, events = Device(), native(), []
        self.assertTrue(close_device(device, notify=False, native=transport, report=events.append))
        transport.transport_disconnected.assert_not_called()
        transport.transport_destroy.assert_called_once()
        self.assertFalse(events[-1]['detail']['notified'])

    def test_native_failures_are_reported_without_claiming_success(self):
        device, transport, events = Device(), native(disconnect=3, destroy=4), []
        self.assertFalse(close_device(device, native=transport, report=events.append))
        self.assertEqual([event['detail']['operation'] for event in events[:-1]],
                         ['disconnect', 'closeTransport'])
        self.assertIn('TransportResult 3', events[0]['detail']['error'])
        self.assertFalse(events[-1]['detail']['ok'])
        self.assertFalse(events[-1]['detail']['notified'])
        transport.transport_destroy.assert_called_once()

    def test_live_reader_does_not_destroy_transport_and_is_reported(self):
        device, transport, events = Device(), native(), []
        device.read_thread = Mock()
        device.read_thread.is_alive.return_value = True
        self.assertFalse(close_device(device, native=transport, report=events.append))
        transport.transport_destroy.assert_not_called()
        self.assertEqual(device.transport._handle, 42)
        self.assertIn('remains open', next(event['detail']['error'] for event in events
                                        if event['event'] == 'deviceShutdownError'))

    def test_disconnect_precedes_reader_join_and_handle_destruction_follows_it(self):
        device, transport, events, order = Device(), native(), [], []
        reader = Mock()
        device.read_thread = reader
        reader.is_alive.return_value = True

        def notify(handle):
            reader.join.assert_not_called()
            order.append('notify')
            return 0

        def joined(timeout):
            self.assertEqual(order, ['notify'])
            order.append('readerJoin')
            reader.is_alive.return_value = False

        def destroyed(handle):
            self.assertFalse(reader.is_alive())
            order.append('destroy')
            return 0

        transport.transport_disconnected.side_effect = notify
        transport.transport_destroy.side_effect = destroyed
        reader.join.side_effect = joined
        self.assertTrue(close_device(device, native=transport, report=events.append))
        self.assertEqual(order, ['notify', 'readerJoin', 'destroy'])
        self.assertEqual(events[0]['event'], 'deviceDisconnect')
        self.assertIsNotNone(events[-1]['detail']['disconnectSeconds'])

    def test_active_native_writer_defers_disconnect_and_handle_destruction(self):
        device, transport, events = Device(), native(), []
        device.heartbeat_thread = Mock()
        device.heartbeat_thread.is_alive.return_value = True
        self.assertFalse(close_device(device, native=transport, report=events.append))
        transport.transport_disconnected.assert_not_called()
        transport.transport_destroy.assert_not_called()
        self.assertEqual(device.transport._handle, 42)
        self.assertIn('writer is still active', events[0]['detail']['error'])

    def test_closed_device_is_not_notified_again(self):
        device, transport, events = Device(), native(), []
        close_device(device, native=transport, report=events.append)
        close_device(device, native=transport, report=events.append)
        transport.transport_disconnected.assert_called_once()
        transport.transport_destroy.assert_called_once()
        self.assertFalse(events[-1]['detail']['notified'])


if __name__ == '__main__':
    unittest.main()
