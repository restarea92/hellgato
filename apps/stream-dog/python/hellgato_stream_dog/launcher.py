"""Hellgato Stream Dog desktop client."""

import ctypes
import json
import logging
import os
from pathlib import Path
import queue
import re
import runpy
import subprocess
import sys
import threading
import time


from .paths import ROOT
STATE = Path(os.environ.get('HELLGATO_STATE_DIR', Path(os.environ['LOCALAPPDATA']) / 'Hellgato'))
def read_release_metadata(root):
    """Use the same packaged metadata in source and PyInstaller execution."""
    metadata = json.loads((root / 'app/release.json').read_text(encoding='utf-8'))
    if (not isinstance(metadata, dict) or set(metadata) != {'version', 'channel'}
            or metadata['channel'] not in ('stable', 'beta')
            or not isinstance(metadata['version'], str)):
        raise ValueError('Invalid packaged Hellgato release metadata')
    match = re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-beta\.(0|[1-9][0-9]*))?', metadata['version'])
    if (match is None or any(len(value) > 5 or int(value) > 65535 for value in match.groups()[:3])
            or metadata['channel'] != ('beta' if match[4] is not None else 'stable')
            or match[4] is not None and (len(match[4]) > 5 or int(match[4]) > 65533)):
        raise ValueError('Invalid packaged Hellgato release metadata')
    return metadata


VERSION = read_release_metadata(ROOT)['version']
if sys.argv[1:] == ['--version']:
    print(VERSION)
    raise SystemExit(0)
os.environ['HELLGATO_STATE_DIR'] = str(STATE)
# Host setup and native preferences remain product-owned.
sys.path.insert(0, str(ROOT / 'app/n4'))
sys.path.insert(0, str(ROOT / 'host-integrations/streamdeck/windows/python'))
from hellgato_host.compatibility import INCOMPATIBLE
from .i18n import Message, Translator
from hellgato_host.presence import SystemPresence
from hellgato_host.startup_install import default_executable, installed, startup_dll
from hellgato_host.startup_status import read_status


def command(script, *arguments):
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('HellgatoWorker.exe')), '--run-script', str(ROOT / script), *arguments]
    return [sys.executable, str(ROOT / script), *arguments]


def worker():
    allowed = {'bridge.py', 'ipc_bridge.py', 'start-streamdeck.py', 'patch-device-geometry.py', 'patch-background-grid.py',
               'load-native-ui.py', 'setup-startup.py'}
    script = Path(sys.argv[2]).resolve()
    if not script.is_relative_to(ROOT.resolve()) or script.name not in allowed:
        raise SystemExit('Unsupported worker')
    sys.argv = [str(script), *sys.argv[3:]]
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(script), run_name='__main__')


class WindowLifecycle:
    """Keep window visibility independent of bridge shutdown."""

    def __init__(self, window, session, tray, tray_ready, transfer_active, status):
        self.window, self.session, self.tray = window, session, tray
        self.tray_ready, self.transfer_active, self.status = tray_ready, transfer_active, status
        self.closing = False

    def show(self):
        if not self.closing:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()

    def hide(self):
        if not self.closing:
            if self.tray_ready.is_set():
                self.window.withdraw()
            else:
                self.window.iconify()

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.session.request_stop()
        self.session.stop.set()
        self.finish()

    def finish(self):
        if (self.session.thread and self.session.thread.is_alive()) or self.transfer_active():
            self.window.after(200, self.finish)
        else:
            self.tray.stop()
            self.window.destroy()


class Session:
    def __init__(self, events):
        self.events = events
        self.stop = threading.Event()
        self.thread = None
        self.bridge = None
        self.bridge_command_lock = threading.Lock()
        self.connected = threading.Event()
        self.listening = threading.Event()
        self.device_ready = threading.Event()
        self.active_device_id = None
        self.bridge_ready = threading.Event()
        self.phase = 'idle'
        self.detail = Message('detail.idle')
        self.stop_file = STATE / 'bridge.stop'

    def status(self, key, phase=None, **values):
        message = Message(key, values)
        if self.detail == message and (phase is None or self.phase == phase):
            return
        if phase is not None:
            self.phase = phase
        self.detail = message
        self.events.put(message)

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def request_stop(self):
        if self.running:
            logging.info('Connection stop requested: user-stop')
            self.stop.set()
            self.stop_file.touch()
            self.status('detail.stopping', 'stopping')

    def start(self, prepare=False):
        if self.running:
            return
        self.stop.clear()
        self.connected.clear()
        self.device_ready.clear()
        self.active_device_id = None
        self.bridge_ready.clear()
        self.listening.clear()
        self.status('detail.checking', 'checking')
        self.thread = threading.Thread(target=self.run, kwargs={'prepare': prepare}, daemon=True)
        self.thread.start()

    def log_bridge(self, stream, log):
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                log.write(line)
                log.flush()
                continue
            if not isinstance(event, dict) or not isinstance(event.get('detail', {}), dict):
                continue
            safe = {**event, 'detail': {key: value for key, value in event.get('detail', {}).items() if key != 'deviceId'}}
            log.write(json.dumps(safe) + '\n')
            log.flush()
            if event.get('event') == 'bridgeReady':
                self.bridge_ready.set()
            elif event.get('event') in ('deviceReady', 'deviceReconnected'):
                self.active_device_id = event.get('detail', {}).get('deviceId')
                self.device_ready.set()
                if self.connected.is_set():
                    self.status('detail.connected', 'connected')
                else:
                    self.status('detail.pairing', 'pairing')
            elif event.get('event') == 'deviceDisconnected':
                self.device_ready.clear()
                self.active_device_id = None
                self.status('detail.disconnected', 'waiting')
            elif (event.get('event') == 'deviceWaiting' and
                  event.get('detail', {}).get('reason') == 'registered-device-mismatch'):
                self.device_ready.clear()
                self.active_device_id = None
                self.status('detail.device_mismatch', 'waiting')
            elif event.get('event') == 'started':
                self.listening.set()
            elif event.get('event') == 'child.clientConnected':
                self.connected.set()
                if self.device_ready.is_set():
                    self.status('detail.connected', 'connected')
                else:
                    self.status('detail.waiting', 'waiting')
            elif event.get('event') == 'child.clientDisconnected':
                self.connected.clear()
                self.status('detail.pairing', 'pairing')
            elif event.get('event') == 'ipcFailed':
                logging.error('SDK product runtime failed: %s', event.get('detail', {}).get('code'))
                self.status('error.device', 'error')

    def run(self, prepare=False):
        from .settings import load_settings
        try:
            load_settings()
            STATE.mkdir(parents=True, exist_ok=True)
            node = ROOT / 'runtime/node.exe'
            environment = {**os.environ, 'HELLGATO_STOP_FILE': str(self.stop_file),
                           'PYTHONUNBUFFERED': '1'}
            environment.pop('HELLGATO_READY_FILE', None)
            if node.exists():
                environment['HELLGATO_NODE'] = str(node)
            if prepare:
                with (STATE / 'bridge.log').open('a', encoding='utf-8') as log:
                    result = subprocess.run(command('scripts/setup-startup.py', '--install', '--elevate'),
                                            cwd=ROOT, env=environment, stdout=log, stderr=log,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:
                    self.status('error.startup_setup', 'setup')
                    return
            if not installed(default_executable(), startup_dll(ROOT)):
                self.status('detail.startup_setup', 'setup')
                return
            presence = SystemPresence()
            while not self.stop.is_set():
                host = presence.host()
                if host is None:
                    self.status('detail.host_waiting', 'pairing')
                    self.stop.wait(.5)
                    continue
                if host.executable.resolve() != default_executable().resolve():
                    raise RuntimeError('Stream Deck is running from a different installation')
                readiness = read_status(host)
                if readiness is None:
                    self.status('detail.startup_reopen', 'pairing')
                elif readiness.state in (3, 4):
                    logging.error('Native startup rejected host pid=%s state=%s error=%s',
                                  host.pid, readiness.state, readiness.error)
                    self.startup_failed(INCOMPATIBLE)
                    return
                elif readiness.ready:
                    if not self.run_bridge(host, presence, environment):
                        return
                else:
                    self.status('detail.starting', 'starting')
                self.stop.wait(.5)
        except Exception:
            self.shutdown_bridge()
            logging.exception('Connection failed')
            self.status('error.runtime', 'error')
        finally:
            self.shutdown_bridge()
            self.connected.clear()
            self.listening.clear()
            self.device_ready.clear()
            if self.stop.is_set():
                self.status('detail.stopped', 'idle')

    def run_bridge(self, host, presence, environment):
        """Keep the saved dock listening while prepared host lifetimes change."""
        self.stop_file.unlink(missing_ok=True)
        for signal in (self.listening, self.connected, self.device_ready, self.bridge_ready):
            signal.clear()
        self.status('detail.device', 'starting')
        with (STATE / 'bridge.log').open('a', encoding='utf-8') as log:
            logging.info('Starting bridge for prepared host pid=%s created=%s', host.pid, host.created)
            self.bridge = subprocess.Popen(command('apps/stream-dog/python/hellgato_stream_dog/ipc_bridge.py', '--init'),
                                           cwd=STATE, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                           creationflags=subprocess.CREATE_NO_WINDOW)
            reader = threading.Thread(target=self.log_bridge, args=(self.bridge.stdout, log), daemon=True)
            reader.start()
            started, next_ui = time.monotonic(), 0
            notified, native_ui_ready, attempts = False, False, 0
            observed_host, prepared_host = host, host
            reason = 'worker-exited'
            try:
                while self.bridge.poll() is None:
                    if self.stop.wait(.5):
                        reason = 'user-stop'
                        break
                    try:
                        current = presence.host()
                    except OSError:
                        # A denied or transient snapshot is not evidence of exit.
                        logging.warning('Host observation failed; preserving the current bridge', exc_info=True)
                        continue
                    now = time.monotonic()
                    if current != observed_host:
                        if observed_host is not None:
                            self.send_bridge_command('hostUnavailable')
                        observed_host, prepared_host = current, None
                        native_ui_ready, attempts, next_ui = False, 0, 0
                        notified, started = False, now
                    if current is None:
                        self.connected.clear()
                        self.status('detail.host_waiting', 'pairing')
                        continue
                    if current.executable.resolve() != default_executable().resolve():
                        raise RuntimeError('Stream Deck is running from a different installation')
                    if prepared_host != current:
                        readiness = read_status(current)
                        if readiness is None:
                            self.status('detail.startup_reopen', 'pairing')
                            continue
                        if readiness.state in (3, 4):
                            self.startup_failed(INCOMPATIBLE)
                            reason = 'host-incompatible'
                            return False
                        if not readiness.ready:
                            self.status('detail.starting', 'starting')
                            continue
                        prepared_host = current
                        self.send_bridge_command('hostAvailable')
                    if self.bridge_ready.is_set() and not native_ui_ready and attempts < 3 and now >= next_ui:
                        attempts += 1
                        native_ui_ready = self.prepare_native_ui(current.pid, environment, log)
                        next_ui = time.monotonic() + 3
                    if not notified and not self.connected.is_set() and now - started > 15:
                        self.status('detail.first_pairing', 'pairing')
                        notified = True
            finally:
                logging.info('Stopping bridge: %s (host pid=%s)', reason, host.pid)
                self.shutdown_bridge()
                reader.join(timeout=3)
                self.connected.clear()
                self.device_ready.clear()
            if reason == 'worker-exited':
                self.status('error.device', 'error')
                return False
        return True

    def send_bridge_command(self, name):
        if name not in ('hostUnavailable', 'hostAvailable'):
            raise ValueError('Unsupported product lifecycle command')
        with self.bridge_command_lock:
            if self.bridge is None or self.bridge.poll() is not None:
                raise OSError('The product runtime is disconnected')
            self.bridge.stdin.write(json.dumps({'command': name}, separators=(',', ':')) + '\n')
            self.bridge.stdin.flush()

    def prepare_native_ui(self, app_pid, environment, log):
        try:
            result = subprocess.run(
                command('scripts/load-native-ui.py', '--pid', str(app_pid),
                        '--config', str(STATE / 'native-ui.json')),
                cwd=ROOT, env=environment, stdout=log, stderr=log,
                timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                logging.warning('Native device controls were not prepared (code %s)', result.returncode)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            logging.exception('Native device controls are unavailable')
            return False

    def shutdown_bridge(self):
        if self.bridge and self.bridge.poll() is None:
            self.stop_file.touch()
            try:
                self.bridge.wait(timeout=12)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill', '/PID', str(self.bridge.pid), '/T', '/F'],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
                self.bridge.wait(timeout=5)

    def startup_failed(self, code):
        key = 'error.incompatible' if code == INCOMPATIBLE else 'error.startup'
        self.status(key, 'error')
        self.events.put(('error', Message(key)))


def main():
    import tkinter as tk
    from tkinter import messagebox, filedialog
    from .profile_transfer import ProfileError, export_profiles, read_profiles, install_profiles
    from .settings import load_settings
    from .device_registry import DeviceRegistry
    import pystray
    from PIL import Image
    STATE.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=STATE / 'bridge.log', encoding='utf-8', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    translator = Translator(STATE)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, 'Local\\Hellgato.N4Pro.Launcher')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    show_request = STATE / 'launcher.show'
    if ctypes.get_last_error() == 183:
        if '--background' not in sys.argv:
            show_request.touch()
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)
        return
    stop_request = STATE / 'launcher.stop'
    stop_request.unlink(missing_ok=True)
    show_request.unlink(missing_ok=True)
    shell = ctypes.WinDLL('shell32')
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
    shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    shell.SetCurrentProcessExplicitAppUserModelID('Hellgato.N4Pro')
    from .ui import LauncherView
    window = tk.Tk()
    if '--background' in sys.argv:
        window.iconify()
    try:
        settings = load_settings()
        serial = f"HGMOCK{settings['identity']:06d}"
    except (OSError, ValueError):
        logging.exception('Cannot load device settings')
        messagebox.showerror('Hellgato', translator.text('error.settings'), parent=window)
        window.destroy()
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)
        return
    status = tk.StringVar()
    events = queue.Queue()
    session = Session(events)
    callbacks = queue.Queue()
    transfer_thread = None
    transfer_busy = False
    tray_ready = threading.Event()
    profiles_root = Path(os.environ['APPDATA']) / 'Elgato/StreamDeck/ProfilesV3'
    device_rows = []
    registry_stamp = None

    def refresh():
        nonlocal device_rows, registry_stamp
        try:
            registry_path = STATE / 'devices.json'
            stamp = registry_path.stat().st_mtime_ns if registry_path.exists() else 0
            if stamp != registry_stamp:
                registry_stamp = stamp
                device_rows = DeviceRegistry(STATE, settings['identity'], settings['dockIdentity']).devices()
        except (OSError, ValueError):
            logging.exception('Cannot read remembered devices')
        active_id = session.active_device_id if session.device_ready.is_set() and session.running else None
        view.render_devices([{**item, 'connected': item['id'] == active_id} for item in device_rows])
        view.render_connection(session.phase, session.detail, session.running,
                               session.stop.is_set(), transfer_busy, lifecycle.closing)

    def toggle_connection():
        if transfer_busy or lifecycle.closing:
            return
        if session.running:
            session.request_stop()
        else:
            session.start(prepare=session.phase == 'setup')
        refresh()

    def change_language(language):
        try:
            translator.select(language, persist=True)
        except (OSError, ValueError):
            logging.exception('Cannot save language preference')
            messagebox.showerror('Hellgato', translator.text('error.language'), parent=window)
        view.retranslate()
        tray.update_menu()
        refresh()

    def finish_transfer(message, failed, resume):
        nonlocal transfer_busy
        transfer_busy = False
        view.render_transfer(message, failed=failed)
        if resume and not lifecycle.closing:
            session.start()
        refresh()

    def transfer(importing):
        nonlocal transfer_thread, transfer_busy
        if transfer_busy or lifecycle.closing or (session.running and session.stop.is_set()):
            return
        if importing:
            chosen = filedialog.askopenfilenames(parent=window, title=translator.text('dialog.import'),
                filetypes=[(translator.text('dialog.profile_files'), '*.streamDeckProfile *.hellgatoProfiles'), (translator.text('dialog.all_files'), '*.*')])
        else:
            chosen = filedialog.asksaveasfilename(parent=window, title=translator.text('dialog.export'),
                initialfile='Hellgato-profiles.hellgatoProfiles', defaultextension='.hellgatoProfiles',
                filetypes=[(translator.text('dialog.bundle'), '*.hellgatoProfiles')])
        if not chosen or lifecycle.closing:
            return
        if not importing:
            confirmed = messagebox.askokcancel(
                translator.text('dialog.export_confirm_title'),
                translator.text('dialog.export_confirm_message'),
                parent=window, icon=messagebox.WARNING, default=messagebox.CANCEL)
            if not confirmed or lifecycle.closing:
                return
        transfer_busy = True
        was_running = session.running
        view.render_transfer(Message('transfer.importing' if importing else 'transfer.exporting'), busy=True)
        refresh()

        def run_transfer():
            stopped = False
            failed = False
            try:
                identity = load_settings()['identity']
                if importing:
                    profiles = read_profiles(chosen)
                    session.request_stop()
                    stopped = True
                    if session.thread:
                        session.thread.join(timeout=120)
                        if session.thread.is_alive():
                            raise ProfileError('error.stop_timeout')
                    result = subprocess.run(command('scripts/start-streamdeck.py', '--require-closed'),
                                            cwd=ROOT, capture_output=True, text=True, timeout=45,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode:
                        raise ProfileError('error.app_running')
                    count, _ = install_profiles(profiles, profiles_root, identity, STATE)
                    message = Message('transfer.imported', {'count': count})
                else:
                    count = export_profiles(chosen, profiles_root, identity)
                    message = Message('transfer.exported', {'count': count, 'filename': Path(chosen).name})
            except Exception as error:
                failed = True
                logging.exception('Profile transfer failed')
                reason = error.key if isinstance(error, ProfileError) else 'error.file'
                message = Message('transfer.failed', {'reason': Message(reason)})
            callbacks.put(lambda: finish_transfer(message, failed, stopped and was_running))
        transfer_thread = threading.Thread(target=run_transfer, daemon=True)
        transfer_thread.start()

    view = LauncherView(window, ROOT, VERSION, serial, toggle_connection,
                        lambda: transfer(False), lambda: transfer(True), lambda: os.startfile(STATE),
                        translator, change_language, lambda: hide(), lambda: close())

    def poll():
        if stop_request.exists():
            stop_request.unlink(missing_ok=True)
            close()
            return
        if show_request.exists():
            show_request.unlink(missing_ok=True)
            show()
        while not events.empty():
            event = events.get_nowait()
            if isinstance(event, tuple) and event[0] == 'error' and not lifecycle.closing:
                show()
                messagebox.showerror('Hellgato', translator.text(event[1]), parent=window)
        while not callbacks.empty():
            callbacks.get_nowait()()
        if not lifecycle.closing:
            refresh()
            window.after(200, poll)

    def tray_setup(icon):
        try:
            icon.visible = True
            tray_ready.set()
            if '--background' in sys.argv:
                callbacks.put(hide)
        except Exception:
            callbacks.put(show)
            logging.exception('Tray unavailable')
            callbacks.put(lambda: messagebox.showerror('Hellgato', translator.text('error.tray'), parent=window))

    tray = pystray.Icon('Hellgato.N4Pro', Image.open(ROOT / 'app/assets/icons/stream-dog-64.png'),
                        'Hellgato Stream Dog', menu=pystray.Menu(
                            pystray.MenuItem(lambda item: translator.text('tray.open'), lambda: callbacks.put(show), default=True),
                            pystray.Menu.SEPARATOR,
                            pystray.MenuItem(lambda item: translator.text('tray.exit'), lambda: callbacks.put(close))))
    lifecycle = WindowLifecycle(window, session, tray, tray_ready,
                                lambda: bool(transfer_thread and transfer_thread.is_alive()), status)
    show, hide = lifecycle.show, lifecycle.hide

    def close():
        view.render_connection(session.phase, Message('detail.stopping'), session.running,
                               busy=transfer_busy, closing=True)
        lifecycle.close()
    tray.run_detached(tray_setup)
    window.protocol('WM_DELETE_WINDOW', hide)
    refresh()
    window.after(200, poll)
    window.after(300, session.start)
    try:
        window.mainloop()
    finally:
        tray.stop()
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)


def run():
    """Run the same GUI/worker dispatch for canonical and compatibility entrypoints."""
    if sys.argv[1:] == ['--stop']:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / 'launcher.stop').touch()
    elif len(sys.argv) > 2 and sys.argv[1] == '--run-script':
        worker()
    else:
        main()


if __name__ == '__main__':
    run()
