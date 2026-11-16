#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::{HWND, POINT, RECT},
    Graphics::Gdi::{CreateRoundRectRgn, DeleteObject, ScreenToClient, SetWindowRgn},
    System::Threading::GetCurrentProcessId,
    UI::{
        Input::KeyboardAndMouse::{GetAsyncKeyState, GetCapture, ReleaseCapture, SetCapture},
        WindowsAndMessaging::{
            FindWindowW, GetCursorPos, GetWindowLongPtrW, GetWindowRect, GetWindowThreadProcessId,
            SetLayeredWindowAttributes, SetWindowLongPtrW, SetWindowPos, GWL_EXSTYLE, LWA_ALPHA,
            SWP_NOACTIVATE, SWP_NOSIZE, SWP_NOZORDER, WS_EX_LAYERED, WS_EX_NOACTIVATE,
            WS_EX_TOOLWINDOW,
        },
    },
};

#[cfg(windows)]
fn owned_window(title: &str) -> Option<HWND> {
    let title: Vec<u16> = title.encode_utf16().chain(Some(0)).collect();
    unsafe {
        let window = FindWindowW(std::ptr::null(), title.as_ptr());
        let mut process = 0;
        if window.is_null() {
            return None;
        }
        GetWindowThreadProcessId(window, &mut process);
        (process == GetCurrentProcessId()).then_some(window)
    }
}

pub fn configure(title: &str, radius: f32) -> bool {
    #[cfg(windows)]
    unsafe {
        let Some(window) = owned_window(title) else {
            return false;
        };
        let style = GetWindowLongPtrW(window, GWL_EXSTYLE);
        SetWindowLongPtrW(
            window,
            GWL_EXSTYLE,
            style | WS_EX_NOACTIVATE as isize | WS_EX_TOOLWINDOW as isize | WS_EX_LAYERED as isize,
        );
        let mut rect = RECT::default();
        if GetWindowRect(window, &mut rect) == 0 {
            return false;
        }
        let diameter = (radius * 2.0).round() as i32;
        let region = CreateRoundRectRgn(
            0,
            0,
            rect.right - rect.left + 1,
            rect.bottom - rect.top + 1,
            diameter,
            diameter,
        );
        if region.is_null() {
            return false;
        }
        if SetWindowRgn(window, region, 1) == 0 {
            DeleteObject(region);
            return false;
        }
        SetLayeredWindowAttributes(window, 0, 255, LWA_ALPHA) != 0
    }
    #[cfg(not(windows))]
    {
        let _ = (title, radius);
        true
    }
}

pub fn opacity(title: &str, alpha: u8) -> bool {
    #[cfg(windows)]
    unsafe {
        owned_window(title)
            .is_some_and(|window| SetLayeredWindowAttributes(window, 0, alpha, LWA_ALPHA) != 0)
    }
    #[cfg(not(windows))]
    {
        let _ = (title, alpha);
        true
    }
}

pub fn button_released() -> bool {
    #[cfg(windows)]
    unsafe {
        GetAsyncKeyState(1) as u16 & 0x8000 == 0
    }
    #[cfg(not(windows))]
    {
        false
    }
}

pub fn pointer(title: &str, pixels_per_point: f32) -> Option<[f32; 2]> {
    #[cfg(windows)]
    unsafe {
        let window = owned_window(title)?;
        let mut point = POINT::default();
        if GetCursorPos(&mut point) == 0 || ScreenToClient(window, &mut point) == 0 {
            return None;
        }
        Some([
            point.x as f32 / pixels_per_point,
            point.y as f32 / pixels_per_point,
        ])
    }
    #[cfg(not(windows))]
    {
        let _ = (title, pixels_per_point);
        None
    }
}

pub struct Drag {
    window: usize,
    pointer: [i32; 2],
    origin: [i32; 2],
}

impl Drag {
    pub fn begin(title: &str) -> Option<Self> {
        #[cfg(windows)]
        unsafe {
            let window = owned_window(title)?;
            let mut pointer = POINT::default();
            let mut rect = RECT::default();
            if GetCursorPos(&mut pointer) == 0 || GetWindowRect(window, &mut rect) == 0 {
                return None;
            }
            SetCapture(window);
            Some(Self {
                window: window as usize,
                pointer: [pointer.x, pointer.y],
                origin: [rect.left, rect.top],
            })
        }
        #[cfg(not(windows))]
        {
            let _ = title;
            None
        }
    }

    pub fn update(&self) {
        #[cfg(windows)]
        unsafe {
            let mut pointer = POINT::default();
            if GetCursorPos(&mut pointer) != 0 {
                self.move_to([pointer.x, pointer.y]);
            }
        }
    }

    #[cfg(windows)]
    fn move_to(&self, pointer: [i32; 2]) {
        // egui's StartDrag requires focus; this preserves the action target's focus.
        unsafe {
            SetWindowPos(
                self.window as HWND,
                std::ptr::null_mut(),
                self.origin[0] + pointer[0] - self.pointer[0],
                self.origin[1] + pointer[1] - self.pointer[1],
                0,
                0,
                SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER,
            );
        }
    }
}

impl Drop for Drag {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            if GetCapture() as usize == self.window {
                ReleaseCapture();
            }
        }
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use windows_sys::Win32::{
        System::LibraryLoader::GetModuleHandleW,
        UI::WindowsAndMessaging::{
            CreateWindowExW, DestroyWindow, GetForegroundWindow, GetLayeredWindowAttributes,
            WS_POPUP,
        },
    };

    #[test]
    fn inactive_window_moves_without_activation_and_uses_one_opacity_value() {
        unsafe {
            let title = format!("Hellgato inactive drag test {}", GetCurrentProcessId());
            let class: Vec<u16> = "STATIC".encode_utf16().chain(Some(0)).collect();
            let caption: Vec<u16> = title.encode_utf16().chain(Some(0)).collect();
            let handle = CreateWindowExW(
                WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                class.as_ptr(),
                caption.as_ptr(),
                WS_POPUP,
                -3000,
                -3000,
                100,
                80,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                GetModuleHandleW(std::ptr::null()),
                std::ptr::null(),
            );
            assert!(!handle.is_null());
            struct Window(HWND);
            impl Drop for Window {
                fn drop(&mut self) {
                    unsafe {
                        DestroyWindow(self.0);
                    }
                }
            }
            let _window = Window(handle);
            let foreground = GetForegroundWindow();
            assert!(configure(&title, 12.0));
            assert!(opacity(&title, 209));
            let mut alpha = 0;
            let mut flags = 0;
            let mut color = 0;
            assert_ne!(
                GetLayeredWindowAttributes(handle, &mut color, &mut alpha, &mut flags),
                0
            );
            assert_eq!(alpha, 209);
            assert_eq!(flags, LWA_ALPHA);
            let drag = Drag::begin(&title).unwrap();
            drag.move_to([drag.pointer[0] + 24, drag.pointer[1] - 17]);
            let mut rect = RECT::default();
            GetWindowRect(handle, &mut rect);
            assert_eq!([rect.left, rect.top], [-2976, -3017]);
            assert_eq!(GetForegroundWindow(), foreground);
            drop(drag);
        }
    }
}
