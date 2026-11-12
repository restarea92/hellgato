use std::path::Path;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Event {
    Show,
    Quit,
}

#[cfg(windows)]
mod native {
    use super::*;
    use eframe::egui;
    use sha2::{Digest, Sha256};
    use std::{ptr, sync::mpsc, thread};
    use windows_sys::Win32::{
        Foundation::*,
        System::LibraryLoader::GetModuleHandleW,
        UI::{Shell::*, WindowsAndMessaging::*},
    };
    const CALLBACK: u32 = WM_APP + 1;
    const SHOW: u32 = WM_APP + 2;
    const QUIT: u32 = WM_APP + 3;
    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }
    fn class_name(path: &Path) -> Vec<u16> {
        let key = path.to_string_lossy().replace('/', "\\").to_lowercase();
        wide(&format!(
            "Hellgato.StreamDog.Tray.{:x}",
            Sha256::digest(key.as_bytes())
        ))
    }
    pub fn signal(path: &Path, event: Event) -> bool {
        unsafe {
            let window = FindWindowW(class_name(path).as_ptr(), ptr::null());
            !window.is_null()
                && PostMessageW(window, if event == Event::Show { SHOW } else { QUIT }, 0, 0) != 0
        }
    }
    pub fn running(path: &Path) -> bool {
        unsafe { !FindWindowW(class_name(path).as_ptr(), ptr::null()).is_null() }
    }
    struct State {
        sender: mpsc::Sender<Event>,
        context: egui::Context,
        icon: HICON,
        restart: u32,
    }
    unsafe fn add_icon(window: HWND, icon: HICON) -> bool {
        let mut data = NOTIFYICONDATAW {
            cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: window,
            uID: 1,
            uFlags: NIF_MESSAGE | NIF_ICON | NIF_TIP,
            uCallbackMessage: CALLBACK,
            hIcon: icon,
            ..Default::default()
        };
        let tip = wide("Hellgato Stream Dog");
        data.szTip[..tip.len()].copy_from_slice(&tip);
        Shell_NotifyIconW(NIM_ADD, &data) != 0
    }
    unsafe extern "system" fn procedure(
        window: HWND,
        message: u32,
        w: WPARAM,
        l: LPARAM,
    ) -> LRESULT {
        if message == WM_NCCREATE {
            let create = &*(l as *const CREATESTRUCTW);
            SetWindowLongPtrW(window, GWLP_USERDATA, create.lpCreateParams as isize);
        }
        let state = GetWindowLongPtrW(window, GWLP_USERDATA) as *mut State;
        if !state.is_null() {
            let state = &*state;
            let mut action = None;
            if message == SHOW {
                action = Some(Event::Show);
            }
            if message == QUIT {
                action = Some(Event::Quit);
            }
            if message == state.restart {
                add_icon(window, state.icon);
            }
            if message == CALLBACK {
                match l as u32 {
                    WM_LBUTTONUP | WM_LBUTTONDBLCLK => action = Some(Event::Show),
                    WM_RBUTTONUP | WM_CONTEXTMENU => {
                        let menu = CreatePopupMenu();
                        AppendMenuW(menu, MF_STRING, 1, wide("열기").as_ptr());
                        AppendMenuW(menu, MF_SEPARATOR, 0, ptr::null());
                        AppendMenuW(menu, MF_STRING, 2, wide("종료").as_ptr());
                        let mut point = POINT::default();
                        GetCursorPos(&mut point);
                        SetForegroundWindow(window);
                        let command = TrackPopupMenu(
                            menu,
                            TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
                            point.x,
                            point.y,
                            0,
                            window,
                            ptr::null(),
                        );
                        DestroyMenu(menu);
                        PostMessageW(window, WM_NULL, 0, 0);
                        action = match command {
                            1 => Some(Event::Show),
                            2 => Some(Event::Quit),
                            _ => None,
                        };
                    }
                    _ => {}
                }
            }
            if let Some(event) = action {
                let _ = state.sender.send(event);
                state.context.request_repaint();
                return 0;
            }
            if message == WM_CLOSE {
                DestroyWindow(window);
                return 0;
            }
            if message == WM_DESTROY {
                let data = NOTIFYICONDATAW {
                    cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
                    hWnd: window,
                    uID: 1,
                    ..Default::default()
                };
                Shell_NotifyIconW(NIM_DELETE, &data);
                DestroyIcon(state.icon);
                let owned = GetWindowLongPtrW(window, GWLP_USERDATA) as *mut State;
                SetWindowLongPtrW(window, GWLP_USERDATA, 0);
                drop(Box::from_raw(owned));
                PostQuitMessage(0);
                return 0;
            }
        }
        DefWindowProcW(window, message, w, l)
    }
    pub struct Tray {
        window: usize,
        worker: Option<thread::JoinHandle<()>>,
        receiver: mpsc::Receiver<Event>,
    }
    impl Tray {
        pub fn new(path: &Path, context: egui::Context) -> Result<Self, String> {
            let class = class_name(path);
            let (sender, receiver) = mpsc::channel();
            let (ready_tx, ready_rx) = mpsc::sync_channel::<Result<usize, String>>(1);
            let worker = thread::spawn(move || unsafe {
                let instance = GetModuleHandleW(ptr::null());
                let class_data = WNDCLASSW {
                    lpfnWndProc: Some(procedure),
                    hInstance: instance,
                    lpszClassName: class.as_ptr(),
                    ..Default::default()
                };
                RegisterClassW(&class_data);
                let bytes = crate::chrome::APP_ICON;
                let icon = CreateIconFromResourceEx(
                    bytes.as_ptr(),
                    bytes.len() as u32,
                    1,
                    0x00030000,
                    32,
                    32,
                    0,
                );
                let state = Box::into_raw(Box::new(State {
                    sender,
                    context,
                    icon,
                    restart: RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()),
                }));
                let window = CreateWindowExW(
                    0,
                    class.as_ptr(),
                    wide("Hellgato Stream Dog tray").as_ptr(),
                    0,
                    0,
                    0,
                    0,
                    0,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    instance,
                    state.cast(),
                );
                if window.is_null() {
                    drop(Box::from_raw(state));
                    DestroyIcon(icon);
                    let _ = ready_tx.send(Err("Cannot create tray event window".into()));
                    return;
                }
                if icon.is_null() || !add_icon(window, icon) {
                    DestroyWindow(window);
                    let _ = ready_tx.send(Err("Cannot add notification icon".into()));
                    return;
                }
                let _ = ready_tx.send(Ok(window as usize));
                let mut message = MSG::default();
                while GetMessageW(&mut message, ptr::null_mut(), 0, 0) > 0 {
                    TranslateMessage(&message);
                    DispatchMessageW(&message);
                }
                UnregisterClassW(class.as_ptr(), instance);
            });
            let window = ready_rx.recv().map_err(|_| "Tray startup failed")??;
            Ok(Self {
                window,
                worker: Some(worker),
                receiver,
            })
        }
        pub fn events(&self) -> Vec<Event> {
            self.receiver.try_iter().collect()
        }
    }
    impl Drop for Tray {
        fn drop(&mut self) {
            unsafe {
                PostMessageW(self.window as HWND, WM_CLOSE, 0, 0);
            }
            if let Some(worker) = self.worker.take() {
                let _ = worker.join();
            }
        }
    }
}
#[cfg(windows)]
pub use native::{running, signal, Tray};
