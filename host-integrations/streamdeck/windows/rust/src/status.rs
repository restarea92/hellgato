use serde_json::{json, Value};

use crate::{AdapterError, Result};

pub fn decode_status(bytes: &[u8], pid: u32, created: u64) -> Result<Value> {
    if bytes.len() != 56 {
        return Err(AdapterError::new(
            "ERR_HOST_STATUS",
            "Invalid startup status size",
        ));
    }
    let u32_at = |offset| u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap());
    let u64_at = |offset| u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap());
    if &bytes[..8] != b"HGREADY1" || u32_at(8) != 1 || u32_at(12) != pid || u64_at(16) != created {
        return Err(AdapterError::new(
            "ERR_HOST_STATUS",
            "Startup status belongs to a different process lifetime",
        ));
    }
    let state = u32_at(24);
    if !(1..=4).contains(&state) {
        return Err(AdapterError::new(
            "ERR_HOST_STATUS",
            "Invalid startup state",
        ));
    }
    Ok(
        json!({"pid":pid,"created":created.to_string(),"state":state,"error":u32_at(28),
        "running":true,"ready":state == 2 && u64_at(32) != 0 && u64_at(40) != 0 && u64_at(48) != 0}),
    )
}

#[cfg(windows)]
pub fn inspect(pid: u32) -> Result<Value> {
    use std::{ffi::c_void, ptr};
    #[repr(C)]
    struct FileTime {
        low: u32,
        high: u32,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut c_void;
        fn GetProcessTimes(
            process: *mut c_void,
            created: *mut FileTime,
            exit: *mut FileTime,
            kernel: *mut FileTime,
            user: *mut FileTime,
        ) -> i32;
        fn OpenFileMappingW(access: u32, inherit: i32, name: *const u16) -> *mut c_void;
        fn MapViewOfFile(
            mapping: *mut c_void,
            access: u32,
            high: u32,
            low: u32,
            size: usize,
        ) -> *const c_void;
        fn UnmapViewOfFile(address: *const c_void) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
        fn GetLastError() -> u32;
    }
    if pid == 0 {
        return Err(AdapterError::new(
            "ERR_HOST_STATUS",
            "A nonzero host PID is required",
        ));
    }
    unsafe {
        let process = OpenProcess(0x1000, 0, pid);
        if process.is_null() {
            return Err(AdapterError::new(
                "ERR_HOST_STATUS",
                format!("Cannot inspect host process ({})", GetLastError()),
            ));
        }
        let mut created = FileTime { low: 0, high: 0 };
        let mut exit = FileTime { low: 0, high: 0 };
        let mut kernel = FileTime { low: 0, high: 0 };
        let mut user = FileTime { low: 0, high: 0 };
        let got_times = GetProcessTimes(process, &mut created, &mut exit, &mut kernel, &mut user);
        let times_error = GetLastError();
        if got_times == 0 || exit.low != 0 || exit.high != 0 {
            CloseHandle(process);
            return Err(AdapterError::new(
                "ERR_HOST_STATUS",
                format!("Host process is not live ({times_error})"),
            ));
        }
        let created = ((created.high as u64) << 32) | created.low as u64;
        let name: Vec<u16> = format!("Local\\Hellgato.Startup.{pid}\0")
            .encode_utf16()
            .collect();
        let mapping = OpenFileMappingW(4, 0, name.as_ptr());
        if mapping.is_null() {
            let error = GetLastError();
            CloseHandle(process);
            return if error == 2 {
                Ok(
                    json!({"pid":pid,"created":created.to_string(),"running":true,"ready":false,"reason":"startup-status-missing"}),
                )
            } else {
                Err(AdapterError::new(
                    "ERR_HOST_STATUS",
                    format!("Cannot open startup status ({error})"),
                ))
            };
        }
        let view = MapViewOfFile(mapping, 4, 0, 0, 56);
        if view.is_null() {
            let error = GetLastError();
            CloseHandle(mapping);
            CloseHandle(process);
            return Err(AdapterError::new(
                "ERR_HOST_STATUS",
                format!("Cannot map startup status ({error})"),
            ));
        }
        let mut first = [0_u8; 56];
        let mut second = [0_u8; 56];
        for (index, byte) in first.iter_mut().enumerate() {
            *byte = ptr::read_volatile((view as *const u8).add(index));
        }
        std::sync::atomic::fence(std::sync::atomic::Ordering::SeqCst);
        for (index, byte) in second.iter_mut().enumerate() {
            *byte = ptr::read_volatile((view as *const u8).add(index));
        }
        let still_live = GetProcessTimes(
            process,
            &mut FileTime { low: 0, high: 0 },
            &mut exit,
            &mut kernel,
            &mut user,
        ) != 0
            && exit.low == 0
            && exit.high == 0;
        UnmapViewOfFile(view);
        CloseHandle(mapping);
        CloseHandle(process);
        if !still_live {
            return Err(AdapterError::new(
                "ERR_HOST_STATUS",
                "Host exited during readiness inspection",
            ));
        }
        if first != second || second[24..28] == [0; 4] {
            return Ok(
                json!({"pid":pid,"created":created.to_string(),"running":true,"ready":false,"reason":"startup-status-changing"}),
            );
        }
        Ok(decode_status(&second, pid, created).unwrap_or_else(|error| json!({"pid":pid,"created":created.to_string(),"running":true,"ready":false,"reason":error.code})))
    }
}

#[cfg(not(windows))]
pub fn inspect(_pid: u32) -> Result<Value> {
    Err(AdapterError::new(
        "ERR_PLATFORM",
        "Windows host status is only available on Windows",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn record() -> [u8; 56] {
        let mut bytes = [0; 56];
        bytes[..8].copy_from_slice(b"HGREADY1");
        bytes[8..12].copy_from_slice(&1_u32.to_le_bytes());
        bytes[12..16].copy_from_slice(&42_u32.to_le_bytes());
        bytes[16..24].copy_from_slice(&123_u64.to_le_bytes());
        bytes[24..28].copy_from_slice(&2_u32.to_le_bytes());
        for offset in [32, 40, 48] {
            bytes[offset..offset + 8].copy_from_slice(&1_u64.to_le_bytes());
        }
        bytes
    }
    #[test]
    fn rejects_stale_host_lifetime_and_incomplete_ready_record() {
        let mut bytes = record();
        assert_eq!(decode_status(&bytes, 42, 123).unwrap()["ready"], true);
        assert!(decode_status(&bytes, 42, 124).is_err());
        assert!(decode_status(&bytes, 43, 123).is_err());
        bytes[48..56].fill(0);
        assert_eq!(decode_status(&bytes, 42, 123).unwrap()["ready"], false);
    }
}
