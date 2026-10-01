"""Windows launcher for the N4 Pro beta bridge."""

import ctypes
import json
import logging
import os
from pathlib import Path
import queue
import runpy
import subprocess
import sys
import threading
import time


ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
STATE = Path(os.environ.get('HELLGATO_STATE_DIR', Path(os.environ['LOCALAPPDATA']) / 'Hellgato'))
VERSION = '0.0.5-beta'
os.environ['HELLGATO_STATE_DIR'] = str(STATE)
sys.path.insert(0, str(ROOT / 'app/n4'))
from compatibility import INCOMPATIBLE
from i18n import Message, Translator


def command(script, *arguments):
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('HellgatoWorker.exe')), '--run-script', str(ROOT / script), *arguments]
    return [sys.executable, str(ROOT / script), *arguments]


def worker():
    allowed = {'bridge.py', 'start-streamdeck.py', 'patch-device-geometry.py', 'patch-background-grid.py'}
    script = Path(sys.argv[2]).resolve()
    if not script.is_relative_to(ROOT.resolve()) or script.name not in allowed:
        raise SystemExit('Unsupported worker')
    sys.argv = [str(script), *sys.argv[3:]]
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(script), run_name='__main__')


def process_running(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


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
        self.boot = None
        self.connected = threading.Event()
        self.listening = threading.Event()
        self.device_ready = threading.Event()
        self.phase = 'idle'
        self.detail = Message('detail.idle')
        self.stop_file = STATE / 'bridge.stop'
        self.ready_file = STATE / 'app.ready'

    def status(self, key, phase=None, **values):
        message = Message(key, values)
        if phase is not None:
            self.phase = phase
        self.detail = message
        self.events.put(message)

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def request_stop(self):
        if self.running:
            self.stop.set()
            self.status('detail.stopping', 'stopping')

    def start(self):
        if self.running:
            return
        self.stop.clear()
        self.connected.clear()
        self.device_ready.clear()
        self.listening.clear()
        self.status('detail.checking', 'checking')
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def log_bridge(self, stream, log):
        for line in stream:
            log.write(line)
            log.flush()
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('event') == 'deviceReady':
                self.device_ready.set()
            elif event.get('event') == 'started':
                self.listening.set()
            elif event.get('event') == 'child.clientConnected':
                self.connected.set()
                self.status('detail.connected', 'connected')
            elif event.get('event') == 'child.clientDisconnected':
                self.connected.clear()
                self.status('detail.pairing', 'pairing')

    def run(self):
        from settings import load_settings
        try:
            settings = load_settings()
            self.status('detail.device', 'starting')
            node = ROOT / 'runtime/node.exe'
            environment = {**os.environ, 'HELLGATO_STOP_FILE': str(self.stop_file),
                           'HELLGATO_READY_FILE': str(self.ready_file), 'PYTHONUNBUFFERED': '1'}
            if node.exists():
                environment['HELLGATO_NODE'] = str(node)
            while not self.stop.is_set():
                self.status('detail.checking', 'checking')
                with (STATE / 'bridge.log').open('a', encoding='utf-8') as log:
                    checked = subprocess.run(command('scripts/start-streamdeck.py', '--check-only'),
                                             cwd=ROOT, env=environment, stdout=log, stderr=log,
                                             timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                if checked.returncode:
                    self.startup_failed(checked.returncode)
                    return
                if self.stop.is_set():
                    break
                self.status('detail.device', 'starting')
                self.stop_file.unlink(missing_ok=True)
                self.ready_file.unlink(missing_ok=True)
                self.listening.clear()
                self.connected.clear()
                self.device_ready.clear()
                with (STATE / 'bridge.log').open('a', encoding='utf-8') as log:
                    self.bridge = subprocess.Popen(command('app/n4/bridge.py', '--init'),
                                                   cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                                   stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                                   creationflags=subprocess.CREATE_NO_WINDOW)
                    reader = threading.Thread(target=self.log_bridge, args=(self.bridge.stdout, log), daemon=True)
                    reader.start()
                    while not self.device_ready.wait(.2):
                        if self.stop.is_set() or self.bridge.poll() is not None:
                            break
                    if self.stop.is_set():
                        self.shutdown_bridge()
                        reader.join(timeout=3)
                        break
                    if self.bridge.poll() is not None:
                        reader.join(timeout=3)
                        if self.bridge.returncode not in (10, 12):
                            self.status('error.device', 'error')
                            return
                        self.status('detail.waiting', 'waiting')
                        self.stop.wait(3)
                        continue
                    self.status('detail.starting', 'starting')
                    self.boot = subprocess.Popen(command('scripts/start-streamdeck.py', '--restart'), cwd=ROOT,
                                                 env=environment, stdout=log, stderr=log,
                                                 creationflags=subprocess.CREATE_NO_WINDOW)
                    while self.boot.poll() is None:
                        # Let startup finish its short breakpoint cleanup before stopping.
                        time.sleep(.2)
                    if self.boot.returncode:
                        self.shutdown_bridge()
                        reader.join(timeout=3)
                        self.startup_failed(self.boot.returncode)
                        return
                    app_pid = json.loads((STATE / 'results/startup-ready.json').read_text())['pid']
                    self.status('detail.pairing', 'pairing')
                    self.ready_file.touch()
                    started = time.monotonic()
                    notified = False
                    while self.bridge.poll() is None and not self.stop.wait(.5):
                        if not process_running(app_pid):
                            self.status('detail.reconnecting', 'starting')
                            break
                        if not notified and not self.connected.is_set() and time.monotonic() - started > 15:
                            self.status('detail.first_pairing', 'pairing')
                            notified = True
                    self.shutdown_bridge()
                    reader.join(timeout=3)
                if not self.stop.is_set():
                    self.connected.clear()
                    self.status('detail.disconnected', 'waiting')
                    self.stop.wait(3)
        except Exception as error:
            self.shutdown_bridge()
            logging.exception('Connection failed')
            self.status('error.runtime', 'error')
        finally:
            self.connected.clear()
            self.listening.clear()
            self.device_ready.clear()
            if self.stop.is_set():
                self.status('detail.stopped', 'idle')

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
    from profile_transfer import ProfileError, export_profiles, read_profiles, install_profiles
    from settings import load_settings
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
    from ui import LauncherView
    window = tk.Tk()
    if '--background' in sys.argv:
        window.iconify()
    try:
        settings = load_settings()
        serial = f"HGMOCK{settings['identity']:06d}"
    except (OSError, ValueError) as error:
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

    def refresh():
        view.render_connection(session.phase, session.detail, session.running,
                               session.stop.is_set(), transfer_busy, lifecycle.closing)

    def toggle_connection():
        if transfer_busy or lifecycle.closing:
            return
        if session.running:
            session.request_stop()
        else:
            session.start()
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
                    result = subprocess.run(command('scripts/start-streamdeck.py', '--restart', '--stop-only'),
                                            cwd=ROOT, capture_output=True, text=True, timeout=45,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode:
                        raise ProfileError('error.stop_app')
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
        except Exception as error:
            callbacks.put(show)
            logging.exception('Tray unavailable')
            callbacks.put(lambda: messagebox.showerror('Hellgato', translator.text('error.tray'), parent=window))

    tray = pystray.Icon('Hellgato.N4Pro', Image.open(ROOT / 'app/assets/icons/hellgato-front-64.png'),
                        'Hellgato · N4 Pro', menu=pystray.Menu(
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


if __name__ == '__main__':
    if sys.argv[1:] == ['--stop']:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / 'launcher.stop').touch()
    elif len(sys.argv) > 2 and sys.argv[1] == '--run-script':
        worker()
    else:
        main()
