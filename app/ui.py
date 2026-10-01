"""Desktop presentation for connection and profile transfer state."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import sys
import tkinter as tk
from tkinter import ttk

from i18n import LANGUAGES


BACKGROUND = '#101722'
SURFACE = '#182435'
BORDER = '#29394e'
TEXT = '#edf4fc'
MUTED = '#a1b1c6'
ACCENT = '#54d9da'
WARNING = '#f2c277'
ERROR = '#ff9d9d'
FONT = ('Segoe UI', 10)


def dark_caption(window):
    """Keep native window controls; unsupported DWM attributes use the OS default."""
    if sys.platform != 'win32':
        return
    user = ctypes.WinDLL('user32')
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    dwm = ctypes.WinDLL('dwmapi')
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmSetWindowAttribute.restype = ctypes.c_long
    handle = user.GetAncestor(window.winfo_id(), 2)
    for attribute, value in ((20, 1), (35, 0x221710), (36, 0xFCF4ED), (34, 0x4E3929)):
        color = wintypes.DWORD(value)
        dwm.DwmSetWindowAttribute(handle, attribute, ctypes.byref(color), ctypes.sizeof(color))


@dataclass(frozen=True)
class ConnectionPresentation:
    title: str
    action: str
    color: str
    enabled: bool


def connection_presentation(phase, running, stopping=False, busy=False, closing=False):
    if closing or (running and stopping):
        return ConnectionPresentation('connection.stopping', 'connection.stopping_action', MUTED, False)
    if not running:
        if phase == 'error':
            return ConnectionPresentation('connection.error', 'connection.retry', ERROR, not busy)
        return ConnectionPresentation('connection.idle', 'connection.start', MUTED, not busy)
    if phase == 'connected':
        return ConnectionPresentation('connection.connected', 'connection.stop', ACCENT, not busy)
    title = {'checking': 'connection.checking', 'waiting': 'connection.waiting',
             'starting': 'connection.starting', 'pairing': 'connection.pairing'}.get(phase, 'connection.starting')
    return ConnectionPresentation(title, 'connection.cancel', WARNING, not busy)


class Card(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BACKGROUND)
        canvas = tk.Canvas(self, bg=BACKGROUND, highlightthickness=0, bd=0)
        canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=SURFACE)
        self.body.pack(fill='both', expand=True, padx=22, pady=20)

        def resize(event):
            width, height, radius = event.width - 1, event.height - 1, 16
            canvas.delete('surface')
            canvas.create_polygon(radius, 0, width-radius, 0, width, 0, width, radius,
                                  width, height-radius, width, height, width-radius, height,
                                  radius, height, 0, height, 0, height-radius, 0, radius, 0, 0,
                                  smooth=True, fill=SURFACE, outline=BORDER, tags='surface')
        canvas.bind('<Configure>', resize)


class LauncherView:
    def __init__(self, window, root, version, serial, connect, export, import_profiles,
                 open_logs, translator, change_language, hide, quit_app):
        self.window, self.translator = window, translator
        self.version, self.change_language = version, change_language
        self._labels = []
        self._last_connection = None
        self._transfer_busy = False
        self._transfer = None
        self._closing = False
        self._popup = self._dialog = None
        self._menu_key = None
        self._menu_items = {}
        self._dialog_labels = []
        self.commands = {'ui.export': export, 'ui.import': import_profiles,
                         'menu.hide': hide, 'tray.exit': quit_app,
                         'menu.preferences': self.open_preferences, 'ui.logs': open_logs,
                         'menu.about': self.open_about}
        self.command_states = {key: True for key in self.commands}
        self.menus = {'menu.file': ('ui.export', 'ui.import', None, 'menu.hide', 'tray.exit'),
                      'menu.settings': ('menu.preferences',),
                      'menu.help': ('ui.logs', 'menu.about')}
        self.shortcuts = {'ui.export': 'Ctrl+E', 'ui.import': 'Ctrl+I'}
        window.title('Hellgato · N4 Pro')
        window.iconbitmap(default=str(root / 'app/assets/icons/hellgato-front.ico'))
        window.configure(bg=BACKGROUND)
        window.geometry('740x600')
        window.minsize(740, 600)
        window.option_add('*Font', FONT)
        window.bind('<Map>', lambda event: dark_caption(window) if event.widget is window else None, add='+')
        window.bind('<Unmap>', self._window_hidden, add='+')
        style = ttk.Style(window)
        style.theme_use('clam')
        style.configure('Hellgato.Horizontal.TProgressbar', background=ACCENT,
                        troughcolor=SURFACE, borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure('Hellgato.TCombobox', fieldbackground=SURFACE, background=BORDER,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER, padding=7)
        style.map('Hellgato.TCombobox', fieldbackground=[('readonly', SURFACE)],
                  foreground=[('readonly', TEXT)], selectbackground=[('readonly', SURFACE)],
                  selectforeground=[('readonly', TEXT)])
        window.option_add('*TCombobox*Listbox.background', SURFACE)
        window.option_add('*TCombobox*Listbox.foreground', TEXT)
        window.option_add('*TCombobox*Listbox.selectBackground', BORDER)
        window.option_add('*TCombobox*Listbox.selectForeground', TEXT)
        menu_bar = tk.Frame(window, bg=SURFACE, padx=14, pady=3)
        menu_bar.pack(fill='x')
        self.menu_buttons = {}
        self._native_menus = {}
        for key, accelerator in zip(self.menus, ('f', 's', 'h')):
            button = tk.Menubutton(menu_bar, text=self.translator.text(key), bg=SURFACE,
                fg=TEXT, activebackground=BORDER, activeforeground=TEXT, relief='flat',
                padx=12, pady=5, highlightthickness=0, takefocus=True)
            popup = tk.Menu(button, tearoff=False)
            for command in self.menus[key]:
                if command is None:
                    popup.add_separator()
                    continue
                popup.add_command(label=self.translator.text(command),
                    accelerator=self.shortcuts.get(command, ''),
                    state='normal' if self.command_states[command] else 'disabled',
                    command=lambda command=command: self.invoke_command(command))
                self._menu_items[command] = (popup, popup.index('end'))
            button.configure(menu=popup)
            button.pack(side='left')
            self._native_menus[key] = popup
            self.menu_buttons[key] = button
            self._labels.append((button, key))
            window.bind(f'<Alt-{accelerator}>', lambda event, key=key: self.open_menu(key))
        window.bind('<Configure>', self._window_moved, add='+')
        tk.Frame(window, height=1, bg=BORDER).pack(fill='x')
        for key, letter in (('ui.export', 'e'), ('ui.import', 'i')):
            window.bind(f'<Control-{letter}>', lambda event, key=key: self.invoke_command(key))
        content = tk.Frame(window, bg=BACKGROUND)
        content.pack(fill='both', expand=True, padx=28, pady=24)
        header = tk.Frame(content, bg=BACKGROUND)
        header.pack(fill='x', pady=(0, 24))
        self.logo = tk.PhotoImage(file=str(root / 'app/assets/icons/hellgato-64.png'))
        tk.Label(header, image=self.logo, bg=BACKGROUND).pack(side='left', padx=(0, 14))
        brand = tk.Frame(header, bg=BACKGROUND)
        brand.pack(side='left')
        tk.Label(brand, text='HELLGATO', font=('Segoe UI', 24, 'bold'),
                 bg=BACKGROUND, fg=ACCENT).pack(anchor='w')
        subtitle = tk.Label(brand, bg=BACKGROUND, fg=MUTED)
        subtitle.pack(anchor='w')
        self._labels.append((subtitle, 'app.subtitle'))
        tk.Label(header, text=f'BETA  {version.removesuffix("-beta")}', bg='#203341',
                 fg=ACCENT, font=('Segoe UI', 9), padx=10, pady=5).pack(side='right', anchor='n', pady=5)
        device = Card(content)
        device.pack(fill='x')
        top = tk.Frame(device.body, bg=SURFACE)
        top.pack(fill='x')
        for key, side in (('ui.device', 'left'), ('ui.hardware', 'right')):
            label = tk.Label(top, fg=MUTED, bg=SURFACE, font=('Segoe UI', 9))
            label.pack(side=side)
            self._labels.append((label, key))
        tk.Label(device.body, text='Mirabox N4 Pro', fg=TEXT, bg=SURFACE,
                 font=('Segoe UI', 20, 'bold')).pack(anchor='w', pady=(8, 0))
        tk.Label(device.body, text=serial, fg=MUTED, bg=SURFACE,
                 font=('Consolas', 10)).pack(anchor='w', pady=(2, 18))
        tk.Frame(device.body, height=1, bg=BORDER).pack(fill='x', pady=(0, 17))
        connection = tk.Frame(device.body, bg=SURFACE)
        connection.pack(fill='x')
        self.indicator = tk.Label(connection, text='●', bg=SURFACE, fg=MUTED, font=('Segoe UI', 12))
        self.indicator.pack(side='left', padx=(0, 8))
        self.connection_button = self.button(connection, 'connection.start', connect)
        self.connection_button.pack(side='right')
        self.connection_title = tk.Label(connection, bg=SURFACE, fg=TEXT,
                                         font=('Segoe UI', 12, 'bold'), anchor='w', justify='left')
        self.connection_title.pack(side='left', fill='x', expand=True, padx=(0, 12))
        self.connection_title.bind('<Configure>', lambda event: self.connection_title.configure(wraplength=event.width))
        self.detail = tk.Label(device.body, bg=SURFACE, fg=MUTED, anchor='nw',
                               justify='left', wraplength=620, height=2)
        self.detail.pack(fill='x', pady=(10, 0))
        self.detail.bind('<Configure>', lambda event: self.detail.configure(wraplength=event.width))
        self.notice = Card(content)
        notice_header = tk.Frame(self.notice.body, bg=SURFACE)
        notice_header.pack(fill='x')
        label = tk.Label(notice_header, fg=TEXT, bg=SURFACE, font=('Segoe UI', 11, 'bold'))
        label.pack(side='left')
        self._labels.append((label, 'ui.profiles'))
        self.dismiss_button = self.button(notice_header, 'ui.close', self.dismiss_transfer)
        self.dismiss_button.configure(pady=3, padx=10)
        self.dismiss_button.pack(side='right')
        self._labels.append((self.dismiss_button, 'ui.close'))
        self.transfer_status = tk.Label(self.notice.body, fg=MUTED, bg=SURFACE, anchor='w',
                                         justify='left', wraplength=620, font=('Segoe UI', 9))
        self.transfer_status.pack(fill='x', pady=(8, 0))
        self.transfer_status.bind('<Configure>', lambda event: self.transfer_status.configure(wraplength=event.width))
        progress_slot = tk.Frame(self.notice.body, bg=SURFACE, height=4)
        progress_slot.pack(fill='x', pady=(8, 0))
        self.progress = ttk.Progressbar(progress_slot, mode='indeterminate', style='Hellgato.Horizontal.TProgressbar')
        self.retranslate()

    @staticmethod
    def button(parent, text, command):
        return tk.Button(parent, text=text, command=command, bg=BORDER, fg=TEXT,
                         activebackground='#354b65', activeforeground=TEXT, disabledforeground='#8292a8',
                         relief='flat', bd=0, padx=16, pady=10, cursor='hand2',
                         highlightthickness=1, highlightbackground=parent.cget('bg'),
                         highlightcolor=ACCENT, takefocus=True)

    def invoke_command(self, key):
        if not self._closing and self.command_states[key] and self._dialog is None:
            self.close_menu()
            self.commands[key]()
        return 'break'

    def open_menu(self, key):
        self.close_menu()
        if self._closing or self._dialog is not None:
            return 'break'
        self._menu_key = key
        popup = self._popup = self._native_menus[key]
        anchor = self.menu_buttons[key]
        popup.post(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
        if self._popup is popup and popup.winfo_ismapped():
            popup.grab_set()
            popup.focus_set()
        return 'break'

    def _window_moved(self, event):
        if event.widget is self.window:
            self.close_menu(restore_focus=False)

    def _window_hidden(self, event):
        if event.widget is self.window:
            self.close_menu(restore_focus=False)
            self.close_dialog()

    def close_menu(self, restore_focus=True):
        for menu in self._native_menus.values():
            menu.unpost()
        grab = self.window.grab_current()
        if grab is not None and self._dialog is None:
            grab.grab_release()
        if self._menu_key and restore_focus:
            self.menu_buttons[self._menu_key].focus_set()
        self._popup = None
        self._menu_key = None
        return 'break'

    def _open_dialog(self, title_key):
        self.close_menu()
        if self._dialog:
            self._dialog.lift()
            return None
        dialog = self._dialog = tk.Toplevel(self.window, bg=BACKGROUND)
        dialog.withdraw()
        dialog.title(self.translator.text(title_key))
        self._dialog_title = title_key
        dialog.transient(self.window)
        dialog.resizable(False, False)
        dialog.bind('<Map>', lambda event: dark_caption(dialog) if event.widget is dialog else None)
        body = tk.Frame(dialog, bg=BACKGROUND, padx=28, pady=24)
        body.pack(fill='both', expand=True)
        self._dialog_labels = []
        dialog.protocol('WM_DELETE_WINDOW', self.close_dialog)
        dialog.bind('<Escape>', lambda event: self.close_dialog())
        return body

    def _dialog_label(self, parent, key, **options):
        label = tk.Label(parent, text=self.translator.text(key), bg=BACKGROUND,
                         fg=MUTED, justify='left', anchor='w', wraplength=460, **options)
        self._dialog_labels.append((label, key))
        return label

    def _show_dialog(self, body, focus=None):
        close = self.button(body, self.translator.text('ui.close'), self.close_dialog)
        close.pack(anchor='e', pady=(24, 0))
        self._dialog_labels.append((close, 'ui.close'))
        dialog = self._dialog
        dialog.update_idletasks()
        x = self.window.winfo_rootx() + (self.window.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.window.winfo_rooty() + 70
        dialog.geometry(f'+{max(0, x)}+{max(0, y)}')
        dialog.deiconify()
        dialog.grab_set()
        (focus or close).focus_force()

    def open_preferences(self):
        body = self._open_dialog('preferences.title')
        if body is None:
            return
        self._dialog_label(body, 'ui.language', font=('Segoe UI', 12, 'bold')).pack(anchor='w', pady=(0, 12))
        self.language_choice = ttk.Combobox(body, state='readonly', width=32,
            style='Hellgato.TCombobox', values=list(LANGUAGES.values()))
        self.language_choice.set(LANGUAGES[self.translator.language])
        self.language_choice.pack(fill='x')
        self.language_choice.bind('<<ComboboxSelected>>',
            lambda event: self.change_language(list(LANGUAGES)[self.language_choice.current()]))
        self._dialog_label(body, 'preferences.language_hint').pack(fill='x', pady=(12, 0))
        self._show_dialog(body, self.language_choice)

    def open_about(self):
        body = self._open_dialog('menu.about')
        if body is None:
            return
        tk.Label(body, text='Hellgato', bg=BACKGROUND, fg=ACCENT, font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        tk.Label(body, text=self.version, bg=BACKGROUND, fg=TEXT).pack(anchor='w', pady=(4, 16))
        for key in ('about.description', 'about.license', 'ui.tray_hint'):
            self._dialog_label(body, key).pack(fill='x', pady=(0, 12))
        self._show_dialog(body)

    def close_dialog(self):
        if self._dialog:
            self._dialog.grab_release()
            self._dialog.destroy()
            self._dialog = None
            self._dialog_labels = []
            self.window.focus_set()
        return 'break'

    def render_connection(self, phase, detail, running, stopping=False, busy=False, closing=False):
        presentation = connection_presentation(phase, running, stopping, busy, closing)
        snapshot = (presentation, detail, busy, closing, self.translator.language)
        if snapshot == self._last_connection:
            return
        self._last_connection = snapshot
        self._closing = closing
        self.connection_title.configure(text=self.translator.text(presentation.title))
        self.indicator.configure(fg=presentation.color)
        self.detail.configure(text=self.translator.text(detail))
        primary = not running and not closing
        self.connection_button.configure(text=self.translator.text(presentation.action),
            state='normal' if presentation.enabled else 'disabled',
            bg=ACCENT if primary else BORDER, fg=BACKGROUND if primary else TEXT,
            activebackground='#7ce7e7' if primary else '#354b65',
            activeforeground=BACKGROUND if primary else TEXT)
        for key in ('ui.export', 'ui.import'):
            self.command_states[key] = not (busy or closing or (running and stopping))
            if key in self._menu_items:
                menu, index = self._menu_items[key]
                menu.entryconfigure(index, state='normal' if self.command_states[key] else 'disabled')
        if closing:
            self.close_menu()
            self.close_dialog()
            for button in self.menu_buttons.values():
                button.configure(state='disabled')

    def render_transfer(self, message, busy=False, failed=False):
        self._transfer = (message, busy, failed)
        self.notice.pack(fill='x', pady=(16, 0))
        self.transfer_status.configure(text=self.translator.text(message), fg=ERROR if failed else MUTED if busy else ACCENT)
        self.dismiss_button.configure(state='disabled' if busy else 'normal')
        if busy and not self._transfer_busy:
            self.progress.place(x=0, y=0, relwidth=1, height=4)
            self.progress.start(15)
        elif not busy and self._transfer_busy:
            self.progress.stop()
            self.progress.place_forget()
        self._transfer_busy = busy

    def dismiss_transfer(self):
        if not self._transfer_busy:
            self.notice.pack_forget()
            self._transfer = None

    def retranslate(self):
        for key, (menu, index) in self._menu_items.items():
            menu.entryconfigure(index, label=self.translator.text(key))
        self.close_menu()
        for widget, key in self._labels + self._dialog_labels:
            widget.configure(text=self.translator.text(key))
        if self._dialog:
            self._dialog.title(self.translator.text(self._dialog_title))
            if self._dialog_title == 'preferences.title':
                self.language_choice.set(LANGUAGES[self.translator.language])
        self._last_connection = None
        if self._transfer:
            self.render_transfer(*self._transfer)
