"""Stop SDK producers promptly and report native shutdown failures."""

import importlib
import threading
import time


def install_interruptible_heartbeat(device, report=print, initial_delay=1, interval=10):
    if device.heartbeat_thread and device.heartbeat_thread.is_alive():
        raise RuntimeError('Heartbeat must be configured before opening the device')
    stop = threading.Event()
    device._hellgato_heartbeat_stop = stop

    def heartbeat_worker():
        if stop.wait(initial_delay):
            return
        while device.run_heartbeat_thread and not stop.is_set():
            try:
                device.transport.heartbeat()
            except Exception as error:
                report({'event': 'deviceError', 'detail': {
                    'operation': 'heartbeat', 'error': str(error)}})
            if stop.wait(interval):
                return

    device._heartbeat_worker = heartbeat_worker
    return stop


def close_device(device, notify=True, report=print, native=None):
    started = time.monotonic()
    errors = []
    notified = False
    disconnect_seconds = None
    transport = device.transport

    def failed(operation, error):
        detail = {'operation': operation, 'error': str(error)}
        errors.append(detail)
        report({'event': 'deviceShutdownError', 'detail': detail})

    def checked(operation):
        nonlocal native
        handle = transport._handle
        if not handle:
            return False
        if native is None:
            native = importlib.import_module(type(transport).__module__)._transport_lib
        result = getattr(native, operation)(handle)
        if result != 0:
            raise OSError(f'{operation} returned TransportResult {result}')
        return True

    def active_writers():
        return any(thread is not None and thread.is_alive() for thread in (
            device.heartbeat_thread, getattr(device.gif_controller, '_thread', None)))

    def disconnect():
        nonlocal notified, disconnect_seconds
        # The SDK destructor may call close again; notify the device only once.
        device._notify_on_close = False
        try:
            if active_writers():
                raise RuntimeError('SDK writer is still active; disconnect deferred')
            notified = checked('transport_disconnected')
            if notified:
                disconnect_seconds = round(time.monotonic() - started, 3)
                report({'event': 'deviceDisconnect', 'detail': {'ok': True, 'seconds': disconnect_seconds}})
        except Exception as error:
            failed('disconnect', error)

    def close_transport():
        if active_writers():
            failed('closeTransport', 'SDK writer is still active; transport close deferred')
            return
        try:
            checked('transport_destroy')
        except Exception as error:
            failed('closeTransport', error)
        else:
            # Retain ownership after native failure so a retry cannot mistake a
            # leaked handle for a successful close and release the shared mutex.
            transport._handle = None
            transport._is_open = False

    device.run_heartbeat_thread = False
    device.run_read_thread = False
    stop = getattr(device, '_hellgato_heartbeat_stop', None)
    if stop is not None:
        stop.set()
    try:
        device.gif_controller.stop_animation_loop()
        device.gif_controller.close()
    except Exception as error:
        failed('stopAnimation', error)
    if device.heartbeat_thread and device.heartbeat_thread.is_alive():
        device.heartbeat_thread.join(timeout=2)
    if notify and device._notify_on_close:
        disconnect()
    original_close = transport.close
    transport.close = close_transport
    try:
        # Reading may remain blocked until disconnect feedback arrives. Notify after
        # writers stop, then let the SDK join its reader before destroying the handle.
        device.close(notify=False)
        if transport._handle:
            failed('closeTransport', 'SDK transport remains open after shutdown')
        if device.heartbeat_thread and device.heartbeat_thread.is_alive():
            failed('heartbeat', 'SDK heartbeat thread is still active')
    except Exception as error:
        failed('closeDevice', error)
    finally:
        transport.close = original_close
    report({'event': 'deviceShutdown', 'detail': {
        'ok': not errors, 'seconds': round(time.monotonic() - started, 3),
        'notifyRequested': notify, 'notified': notified,
        'disconnectSeconds': disconnect_seconds, 'errors': errors}})
    return not errors
