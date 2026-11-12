use serde_json::Value;

use crate::Result;

const READ_FAILED: u32 = 0x0400_0202;
const READ_TIMEOUT: u32 = 0x0500_0302;

#[derive(Debug, PartialEq, Eq)]
enum ReadDisposition {
    Data,
    Empty,
    CheckWritable,
    Disconnected,
    Fatal,
}

fn classify_read(result: u32, writable: Option<bool>) -> ReadDisposition {
    match result {
        0 => ReadDisposition::Data,
        READ_TIMEOUT => ReadDisposition::Empty,
        READ_FAILED => match writable {
            Some(true) => ReadDisposition::Empty,
            Some(false) => ReadDisposition::Disconnected,
            None => ReadDisposition::CheckWritable,
        },
        _ => ReadDisposition::Fatal,
    }
}

pub trait Backend {
    fn simulated(&self) -> bool {
        false
    }
    fn discover(&mut self) -> Result<Vec<Value>>;
    fn open(&mut self, device_id: &str, brightness: u8) -> Result<()>;
    fn frame(&mut self, jpeg: &[u8]) -> Result<()>;
    fn control(&mut self, name: &str, value: Option<u8>) -> Result<()>;
    fn poll(&mut self) -> Result<Vec<Vec<u8>>>;
    fn maintain(&mut self) -> Result<()>;
    fn close(&mut self) -> Result<()>;
}

#[cfg(windows)]
mod windows_backend {
    use super::Backend;
    use crate::{
        model::{self, Model},
        AdapterError, Result,
    };
    use serde_json::Value;
    use sha2::{Digest, Sha256};
    use std::{
        collections::HashMap,
        ffi::{c_char, c_int, c_void, CStr, CString},
        os::windows::ffi::OsStrExt,
        path::{Path, PathBuf},
        ptr,
        time::{Duration, Instant},
    };

    #[link(name = "kernel32")]
    extern "system" {
        fn LoadLibraryExW(path: *const u16, file: *mut c_void, flags: u32) -> *mut c_void;
        fn GetProcAddress(module: *mut c_void, name: *const c_char) -> *mut c_void;
        fn FreeLibrary(module: *mut c_void) -> i32;
        fn CreateMutexW(attributes: *mut c_void, owner: i32, name: *const u16) -> *mut c_void;
        fn CloseHandle(handle: *mut c_void) -> i32;
        fn GetLastError() -> u32;
        fn SetLastError(error: u32);
    }

    #[repr(C)]
    struct HidDeviceInfo {
        path: *const c_char,
        vendor_id: u16,
        product_id: u16,
        serial_number: *const u16,
        release_number: u16,
        manufacturer_string: *const u16,
        product_string: *const u16,
        usage_page: u16,
        usage: u16,
        interface_number: c_int,
        next: *mut HidDeviceInfo,
    }

    type Simple = unsafe extern "C" fn(*mut c_void) -> u32;
    type Enumerate = unsafe extern "C" fn(u16, u16) -> *mut HidDeviceInfo;
    type FreeEnumeration = unsafe extern "C" fn(*mut HidDeviceInfo);
    type Create = unsafe extern "C" fn(*const HidDeviceInfo, *mut *mut c_void) -> u32;
    type ReportSize = unsafe extern "C" fn(*mut c_void, u16, u16, u16) -> u32;
    type Firmware = unsafe extern "C" fn(*mut c_void, *mut c_char, usize) -> u32;
    type Brightness = unsafe extern "C" fn(*mut c_void, u8) -> u32;
    type Frame =
        unsafe extern "C" fn(*mut c_void, *const c_char, usize, u16, u16, u16, u16, u8) -> u32;
    type Read = unsafe extern "C" fn(*mut c_void, *mut u8, *mut usize, i32) -> u32;
    type CanWrite = unsafe extern "C" fn(*mut c_void, *mut c_int) -> u32;
    type KeyImage = unsafe extern "C" fn(*mut c_void, *const c_char, usize, u8) -> u32;

    struct Library {
        module: *mut c_void,
        enumerate: Enumerate,
        free_enumeration: FreeEnumeration,
        create: Create,
        destroy: Simple,
        report_size: ReportSize,
        firmware: Firmware,
        wake: Simple,
        brightness: Brightness,
        clear: Simple,
        frame: Frame,
        refresh: Simple,
        sleep: Simple,
        disconnected: Simple,
        heartbeat: Simple,
        read: Read,
        can_write: CanWrite,
        key_image: KeyImage,
    }

    impl Library {
        fn load(path: &Path) -> Result<Self> {
            if !path.is_absolute() || !path.is_file() {
                return Err(AdapterError::new(
                    "ERR_RUNTIME",
                    "An explicit absolute vendor DLL file is required",
                ));
            }
            let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
            let module = unsafe { LoadLibraryExW(wide.as_ptr(), ptr::null_mut(), 0x100 | 0x1000) };
            if module.is_null() {
                return Err(AdapterError::new(
                    "ERR_RUNTIME",
                    format!("Cannot load vendor runtime ({})", unsafe { GetLastError() }),
                ));
            }
            macro_rules! symbol {
                ($name:literal, $kind:ty) => {{
                    let address =
                        unsafe { GetProcAddress(module, concat!($name, "\0").as_ptr().cast()) };
                    if address.is_null() {
                        unsafe {
                            FreeLibrary(module);
                        }
                        return Err(AdapterError::new(
                            "ERR_RUNTIME",
                            concat!("Missing vendor symbol: ", $name),
                        ));
                    }
                    unsafe { std::mem::transmute::<*mut c_void, $kind>(address) }
                }};
            }
            Ok(Self {
                module,
                enumerate: symbol!("transport_hid_enumerate", Enumerate),
                free_enumeration: symbol!("transport_hid_free_enumeration", FreeEnumeration),
                create: symbol!("transport_create", Create),
                destroy: symbol!("transport_destroy", Simple),
                report_size: symbol!("transport_set_reportSize", ReportSize),
                firmware: symbol!("transport_get_firmware_version", Firmware),
                wake: symbol!("transport_wakeup_screen", Simple),
                brightness: symbol!("transport_set_key_brightness", Brightness),
                clear: symbol!("transport_clear_all_keys", Simple),
                frame: symbol!("transport_set_background_frame_stream", Frame),
                refresh: symbol!("transport_refresh", Simple),
                sleep: symbol!("transport_sleep", Simple),
                disconnected: symbol!("transport_disconnected", Simple),
                heartbeat: symbol!("transport_heartbeat", Simple),
                read: symbol!("transport_read", Read),
                can_write: symbol!("transport_can_write", CanWrite),
                key_image: symbol!("transport_set_key_image_stream", KeyImage),
            })
        }
    }
    impl Drop for Library {
        fn drop(&mut self) {
            unsafe {
                FreeLibrary(self.module);
            }
        }
    }

    struct Ownership {
        handle: *mut c_void,
    }
    impl Ownership {
        fn acquire() -> Result<Self> {
            let name: Vec<u16> = (if model::current() == Model::Pro {
                "Local\\Hellgato.N4Pro.Bridge".to_owned()
            } else {
                format!("Local\\Hellgato.Compat.{}", model::current().id())
            } + "\0")
                .encode_utf16()
                .collect();
            unsafe {
                SetLastError(0);
                let handle = CreateMutexW(ptr::null_mut(), 0, name.as_ptr());
                let error = GetLastError();
                if handle.is_null() || error == 183 {
                    if !handle.is_null() {
                        CloseHandle(handle);
                    }
                    return Err(AdapterError::new(
                        "ERR_DEVICE_BUSY",
                        "Another N4 engine owns the device",
                    ));
                }
                Ok(Self { handle })
            }
        }
    }
    impl Drop for Ownership {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.handle);
            }
        }
    }

    struct Device {
        path: CString,
        serial: Vec<u16>,
        manufacturer: Vec<u16>,
        product: Vec<u16>,
        release: u16,
        usage_page: u16,
        usage: u16,
        interface: c_int,
    }
    unsafe fn copy_wide(pointer: *const u16) -> Result<Vec<u16>> {
        if pointer.is_null() {
            return Ok(vec![0]);
        }
        let mut value = Vec::new();
        for index in 0..1024 {
            let unit = *pointer.add(index);
            value.push(unit);
            if unit == 0 {
                return Ok(value);
            }
        }
        Err(AdapterError::new(
            "ERR_DISCOVERY",
            "Vendor string exceeds its bound",
        ))
    }

    pub struct NativeBackend {
        path: PathBuf,
        library: Option<Library>,
        ownership: Option<Ownership>,
        devices: HashMap<String, Device>,
        handle: *mut c_void,
        admitted: bool,
        close_failed: bool,
        read_compat_reported: bool,
        heartbeat_at: Instant,
        compat_cache: HashMap<u8, Vec<u8>>,
    }

    impl NativeBackend {
        pub fn new(path: PathBuf) -> Self {
            Self {
                path,
                library: None,
                ownership: None,
                devices: HashMap::new(),
                handle: ptr::null_mut(),
                admitted: false,
                close_failed: false,
                read_compat_reported: false,
                heartbeat_at: Instant::now(),
                compat_cache: HashMap::new(),
            }
        }
        fn load(&mut self) -> Result<()> {
            if self.library.is_none() {
                let ownership = Ownership::acquire()?;
                let library = Library::load(&self.path)?;
                self.ownership = Some(ownership);
                self.library = Some(library);
            }
            Ok(())
        }
        fn checked(result: u32, operation: &str) -> Result<()> {
            if result == 0 {
                Ok(())
            } else {
                Err(AdapterError::new(
                    "ERR_NATIVE",
                    format!("{operation} returned 0x{result:08X}"),
                ))
            }
        }
        fn require_open(&self) -> Result<&Library> {
            if self.handle.is_null() || !self.admitted || self.close_failed {
                return Err(AdapterError::new("ERR_DEVICE_CLOSED", "Device is not open"));
            }
            self.library
                .as_ref()
                .ok_or_else(|| AdapterError::new("ERR_RUNTIME", "Runtime is unavailable"))
        }
    }

    impl Backend for NativeBackend {
        fn discover(&mut self) -> Result<Vec<Value>> {
            if !self.handle.is_null() {
                return Err(AdapterError::new(
                    "ERR_STATE",
                    "Close the device before discovery",
                ));
            }
            self.load()?;
            let library = self.library.as_ref().unwrap();
            let (vid, pid) = model::current().usb();
            let head = unsafe { (library.enumerate)(vid, pid) };
            let result = (|| {
                let mut devices = HashMap::new();
                let mut current = head;
                let mut count = 0;
                while !current.is_null() {
                    count += 1;
                    if count > 64 {
                        return Err(AdapterError::new(
                            "ERR_DISCOVERY",
                            "Too many vendor interfaces",
                        ));
                    }
                    let info = unsafe { &*current };
                    if info.vendor_id == vid
                        && info.product_id == pid
                        && (model::current() == Model::Pro
                            || (info.usage_page == 0xffa0
                                && info.release_number
                                    == if model::current() == Model::N4 {
                                        2
                                    } else {
                                        0x0300
                                    }))
                        && info.usage_page > 1025
                        && info.usage == 1
                        && !info.path.is_null()
                    {
                        let path = unsafe { CStr::from_ptr(info.path) }.to_owned();
                        if path.as_bytes().len() > 4096 {
                            return Err(AdapterError::new(
                                "ERR_DISCOVERY",
                                "Vendor device path exceeds its bound",
                            ));
                        }
                        let id = format!("n4-{:x}", Sha256::digest(path.as_bytes()));
                        let device = Device {
                            path,
                            serial: unsafe { copy_wide(info.serial_number)? },
                            manufacturer: unsafe { copy_wide(info.manufacturer_string)? },
                            product: unsafe { copy_wide(info.product_string)? },
                            release: info.release_number,
                            usage_page: info.usage_page,
                            usage: info.usage,
                            interface: info.interface_number,
                        };
                        if devices.insert(id, device).is_some() {
                            return Err(AdapterError::new(
                                "ERR_DISCOVERY",
                                "Ambiguous vendor interface identity",
                            ));
                        }
                    }
                    current = info.next;
                }
                Ok(devices)
            })();
            if !head.is_null() {
                unsafe {
                    (library.free_enumeration)(head);
                }
            }
            self.devices = result?;
            let mut result: Vec<Value> = self
                .devices
                .keys()
                .map(|id| crate::device_descriptor(id, false))
                .collect();
            result.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
            Ok(result)
        }
        fn open(&mut self, device_id: &str, brightness: u8) -> Result<()> {
            if !self.handle.is_null() {
                return Err(AdapterError::new("ERR_STATE", "Device is already open"));
            }
            self.load()?;
            let device = self.devices.get(device_id).ok_or_else(|| {
                AdapterError::new(
                    "ERR_DEVICE_NOT_FOUND",
                    "Discover and explicitly select a device first",
                )
            })?;
            let info = HidDeviceInfo {
                path: device.path.as_ptr(),
                vendor_id: model::current().usb().0,
                product_id: model::current().usb().1,
                serial_number: device.serial.as_ptr(),
                release_number: device.release,
                manufacturer_string: device.manufacturer.as_ptr(),
                product_string: device.product.as_ptr(),
                usage_page: device.usage_page,
                usage: device.usage,
                interface_number: device.interface,
                next: ptr::null_mut(),
            };
            let library = self.library.as_ref().unwrap();
            let created = unsafe { (library.create)(&info, &mut self.handle) };
            if created != 0 || self.handle.is_null() {
                let _ = self.close();
                return Err(AdapterError::new(
                    "ERR_DEVICE_OPEN",
                    format!("Native open failed (0x{created:08X})"),
                ));
            }
            let initialized = (|| {
                Self::checked(
                    unsafe {
                        (library.report_size)(
                            self.handle,
                            513,
                            if model::current() == Model::S293 {
                                513
                            } else {
                                1025
                            },
                            0,
                        )
                    },
                    "report-size",
                )?;
                let mut firmware = [0_u8; 64];
                Self::checked(
                    unsafe {
                        (library.firmware)(
                            self.handle,
                            firmware.as_mut_ptr().cast(),
                            firmware.len(),
                        )
                    },
                    "firmware-read",
                )?;
                let firmware = firmware
                    .split(|byte| *byte == 0)
                    .find(|part| !part.is_empty())
                    .unwrap_or_default();
                if firmware != model::current().firmware().as_bytes() {
                    return Err(AdapterError::new(
                        "ERR_FIRMWARE",
                        format!("Exact firmware {} is required", model::current().firmware()),
                    ));
                }
                self.admitted = true;
                Self::checked(unsafe { (library.wake)(self.handle) }, "wake")?;
                Self::checked(
                    unsafe { (library.brightness)(self.handle, brightness) },
                    "brightness",
                )?;
                if model::current() == Model::Pro {
                    Self::checked(
                        unsafe { (library.clear)(self.handle) },
                        "clear-key-overlays",
                    )?;
                }
                Self::checked(unsafe { (library.heartbeat)(self.handle) }, "heartbeat")?;
                Ok(())
            })();
            if let Err(error) = initialized {
                let _ = self.close();
                return Err(error);
            }
            self.heartbeat_at = Instant::now() + Duration::from_secs(8);
            self.read_compat_reported = false;
            self.compat_cache.clear();
            Ok(())
        }
        fn frame(&mut self, jpeg: &[u8]) -> Result<()> {
            if model::current() != Model::Pro {
                let key_image = self.require_open()?.key_image;
                for (wire, encoded) in crate::compatibility::encode_regions(jpeg, model::current())?
                {
                    if self.compat_cache.get(&wire) == Some(&encoded) {
                        continue;
                    }
                    Self::checked(
                        unsafe {
                            key_image(self.handle, encoded.as_ptr().cast(), encoded.len(), wire)
                        },
                        "compatibility-region-submit",
                    )?;
                    self.compat_cache.insert(wire, encoded);
                }
                return Self::checked(
                    unsafe { (self.require_open()?.refresh)(self.handle) },
                    "compatibility-refresh",
                );
            }
            let library = self.require_open()?;
            let mut can_write = 0;
            Self::checked(
                unsafe { (library.can_write)(self.handle, &mut can_write) },
                "writable-status",
            )?;
            if can_write == 0 {
                return Err(AdapterError::new(
                    "ERR_DEVICE_CLOSED",
                    "Native device is not writable",
                ));
            }
            Self::checked(
                unsafe {
                    (library.frame)(
                        self.handle,
                        jpeg.as_ptr().cast(),
                        jpeg.len(),
                        800,
                        480,
                        0,
                        0,
                        0,
                    )
                },
                "frame-submit",
            )?;
            Self::checked(unsafe { (library.refresh)(self.handle) }, "refresh")
        }
        fn control(&mut self, name: &str, value: Option<u8>) -> Result<()> {
            if name == "wake" {
                self.compat_cache.clear();
            }
            let library = self.require_open()?;
            match name {
                "brightness" => Self::checked(
                    unsafe { (library.brightness)(self.handle, value.unwrap()) },
                    "brightness",
                ),
                "sleep" => Self::checked(unsafe { (library.sleep)(self.handle) }, "sleep"),
                "wake" => Self::checked(unsafe { (library.wake)(self.handle) }, "wake"),
                _ => Err(AdapterError::new(
                    "ERR_UNSUPPORTED",
                    "Unsupported volatile control",
                )),
            }
        }
        fn poll(&mut self) -> Result<Vec<Vec<u8>>> {
            if self.handle.is_null() || !self.admitted || self.close_failed {
                return Ok(Vec::new());
            }
            let (read, can_write) = {
                let library = self.require_open()?;
                (library.read, library.can_write)
            };
            let mut packets = Vec::new();
            for _ in 0..16 {
                let mut bytes = [0_u8; 1024];
                let mut length = bytes.len();
                let result = unsafe { read(self.handle, bytes.as_mut_ptr(), &mut length, 0) };
                let mut disposition = super::classify_read(result, None);
                if disposition == super::ReadDisposition::CheckWritable {
                    let mut writable = 0;
                    Self::checked(
                        unsafe { can_write(self.handle, &mut writable) },
                        "read-writable-status",
                    )?;
                    disposition = super::classify_read(result, Some(writable != 0));
                    // The vendor Python polling loop tolerates nonzero reads. Keep this
                    // generic READ_FAILED observable and tolerate it only while writable.
                    if disposition == super::ReadDisposition::Empty && !self.read_compat_reported {
                        eprintln!("{{\"event\":\"native-diagnostic\",\"code\":\"NONBLOCKING_READ_FAILED_WRITABLE\",\"nativeCode\":\"0x04000202\"}}");
                        self.read_compat_reported = true;
                    }
                }
                match disposition {
                    super::ReadDisposition::Data => {}
                    super::ReadDisposition::Empty => break,
                    super::ReadDisposition::Disconnected => {
                        return Err(AdapterError::new(
                            "ERR_DEVICE_CLOSED",
                            "Native device became non-writable during input polling",
                        ))
                    }
                    _ => {
                        return Err(AdapterError::new(
                            "ERR_NATIVE",
                            format!("input-read returned 0x{result:08X}"),
                        ))
                    }
                }
                if length == 0 {
                    break;
                }
                if length > bytes.len() {
                    return Err(AdapterError::new(
                        "ERR_NATIVE",
                        "Native input exceeded its buffer",
                    ));
                }
                packets.push(bytes[..length].to_vec());
            }
            Ok(packets)
        }
        fn maintain(&mut self) -> Result<()> {
            if self.handle.is_null()
                || !self.admitted
                || self.close_failed
                || Instant::now() < self.heartbeat_at
            {
                return Ok(());
            }
            let library = self.require_open()?;
            Self::checked(unsafe { (library.heartbeat)(self.handle) }, "heartbeat")?;
            self.heartbeat_at = Instant::now() + Duration::from_secs(8);
            Ok(())
        }
        fn close(&mut self) -> Result<()> {
            if self.handle.is_null() {
                return Ok(());
            }
            if self.close_failed {
                return Err(AdapterError::new(
                    "ERR_NATIVE_CLOSE",
                    "Native close is unconfirmed; terminate this worker before another session",
                ));
            }
            let library = self.library.as_ref().unwrap();
            let mut can_write = 0;
            let writable =
                unsafe { (library.can_write)(self.handle, &mut can_write) } == 0 && can_write != 0;
            let disconnect = if self.admitted && writable {
                unsafe { (library.disconnected)(self.handle) }
            } else {
                0
            };
            let destroyed = unsafe { (library.destroy)(self.handle) };
            if destroyed != 0 {
                self.close_failed = true;
                return Err(AdapterError::new("ERR_NATIVE_CLOSE", format!("Native close returned 0x{destroyed:08X}; ownership retained until process exit")));
            }
            self.handle = ptr::null_mut();
            self.admitted = false;
            Self::checked(disconnect, "disconnect")
        }
    }
    impl Drop for NativeBackend {
        fn drop(&mut self) {
            if !self.close_failed {
                let _ = self.close();
            }
            if !self.handle.is_null() {
                // A live native handle must retain its DLL and ownership until process exit.
                if let Some(library) = self.library.take() {
                    std::mem::forget(library);
                }
                if let Some(ownership) = self.ownership.take() {
                    std::mem::forget(ownership);
                }
            }
        }
    }
}

#[cfg(windows)]
pub use windows_backend::NativeBackend;

#[cfg(not(windows))]
pub struct NativeBackend;
#[cfg(not(windows))]
impl NativeBackend {
    pub fn new(_path: std::path::PathBuf) -> Self {
        Self
    }
}
#[cfg(not(windows))]
impl Backend for NativeBackend {
    fn discover(&mut self) -> Result<Vec<Value>> {
        Err(crate::AdapterError::new(
            "ERR_PLATFORM",
            "Physical adapter requires Windows x64",
        ))
    }
    fn open(&mut self, _: &str, _: u8) -> Result<()> {
        self.discover().map(|_| ())
    }
    fn frame(&mut self, _: &[u8]) -> Result<()> {
        self.discover().map(|_| ())
    }
    fn control(&mut self, _: &str, _: Option<u8>) -> Result<()> {
        self.discover().map(|_| ())
    }
    fn poll(&mut self) -> Result<Vec<Vec<u8>>> {
        Ok(Vec::new())
    }
    fn maintain(&mut self) -> Result<()> {
        Ok(())
    }
    fn close(&mut self) -> Result<()> {
        Ok(())
    }
}

#[cfg(test)]
mod read_tests {
    use super::*;

    #[test]
    fn read_failed_requires_positive_writable_status_and_is_not_a_timeout() {
        assert_eq!(
            classify_read(READ_FAILED, None),
            ReadDisposition::CheckWritable
        );
        assert_eq!(
            classify_read(READ_FAILED, Some(true)),
            ReadDisposition::Empty
        );
        assert_eq!(
            classify_read(READ_FAILED, Some(false)),
            ReadDisposition::Disconnected
        );
        assert_eq!(classify_read(READ_TIMEOUT, None), ReadDisposition::Empty);
        assert_eq!(classify_read(0, None), ReadDisposition::Data);
    }

    #[test]
    fn unrelated_errors_remain_fatal_even_if_writable() {
        for code in [0x0200_0204, 0x0400_0201, 0x0500_0303, 0xffff_ffff] {
            assert_eq!(classify_read(code, Some(true)), ReadDisposition::Fatal);
        }
    }
}
