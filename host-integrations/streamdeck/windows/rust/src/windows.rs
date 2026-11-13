use crate::{
    file_digest, status, validate_host_file, validate_host_path, validate_native_config, HostError,
    Result,
};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    ffi::c_void,
    mem::{size_of, zeroed},
    os::windows::ffi::OsStrExt,
    path::{Path, PathBuf},
    ptr,
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, FreeLibrary, GetLastError, HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::{
            Debug::WriteProcessMemory,
            ToolHelp::{
                CreateToolhelp32Snapshot, Module32FirstW, Module32NextW, Process32FirstW,
                Process32NextW, MODULEENTRY32W, PROCESSENTRY32W, TH32CS_SNAPMODULE,
                TH32CS_SNAPMODULE32, TH32CS_SNAPPROCESS,
            },
        },
        LibraryLoader::{
            GetModuleFileNameW, GetModuleHandleExW, GetModuleHandleW, GetProcAddress,
            LoadLibraryExW,
        },
        Memory::{
            VirtualAllocEx, VirtualFreeEx, MEM_COMMIT, MEM_RELEASE, MEM_RESERVE, PAGE_READWRITE,
        },
        Threading::{
            CreateRemoteThread, GetExitCodeThread, OpenProcess, QueryFullProcessImageNameW,
            WaitForSingleObject,
        },
    },
};

struct Handle(HANDLE);
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
fn error(operation: &str) -> HostError {
    HostError::new(
        "HOST_API",
        format!("{operation} failed ({})", unsafe { GetLastError() }),
    )
}
fn handle(value: HANDLE, operation: &str) -> Result<Handle> {
    if value.is_null() || value == INVALID_HANDLE_VALUE {
        Err(error(operation))
    } else {
        Ok(Handle(value))
    }
}
fn wide(path: &Path) -> Vec<u16> {
    path.as_os_str().encode_wide().chain(Some(0)).collect()
}
fn text(bytes: &[u16]) -> String {
    String::from_utf16_lossy(bytes.split(|byte| *byte == 0).next().unwrap_or_default())
}
fn same_path(left: &Path, right: &Path) -> bool {
    match (left.canonicalize(), right.canonicalize()) {
        (Ok(left), Ok(right)) => left
            .to_string_lossy()
            .eq_ignore_ascii_case(&right.to_string_lossy()),
        _ => false,
    }
}
fn process_path(process: HANDLE) -> Result<PathBuf> {
    let mut buffer = vec![0_u16; 32768];
    let mut length = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) } == 0 {
        return Err(error("process path"));
    }
    Ok(PathBuf::from(String::from_utf16_lossy(
        &buffer[..length as usize],
    )))
}
pub(crate) fn matching_processes(executable: &Path) -> Result<Vec<u32>> {
    let snapshot = handle(
        unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) },
        "process snapshot",
    )?;
    let mut entry: PROCESSENTRY32W = unsafe { zeroed() };
    entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
    let mut result = Vec::new();
    let mut found = unsafe { Process32FirstW(snapshot.0, &mut entry) } != 0;
    while found {
        if text(&entry.szExeFile).eq_ignore_ascii_case("StreamDeck.exe") {
            if let Ok(process) = handle(
                unsafe { OpenProcess(0x1000, 0, entry.th32ProcessID) },
                "inspect process",
            ) {
                if process_path(process.0).is_ok_and(|path| same_path(&path, executable)) {
                    result.push(entry.th32ProcessID);
                }
            }
        }
        found = unsafe { Process32NextW(snapshot.0, &mut entry) } != 0;
    }
    Ok(result)
}

pub fn probe(executable: &Path, digest: &str) -> Result<Value> {
    validate_host_path(executable)?;
    let actual = file_digest(executable)?;
    let processes = matching_processes(executable)?;
    if processes.len() != 1 {
        return Ok(
            json!({"ready":false,"running":false,"reason":if processes.is_empty() {"host-not-running"} else {"multiple-host-processes"}}),
        );
    }
    let mut report = status::inspect(processes[0])?;
    report["executable"] = json!(executable);
    if file_digest(executable)? != actual {
        return Err(HostError::new(
            "HOST_IDENTITY",
            "Host executable changed during inspection",
        ));
    }
    report["executableSha256"] = json!(actual);
    report["tested"] = json!(actual == digest.to_ascii_lowercase());
    Ok(report)
}

fn modules(pid: u32) -> Result<BTreeMap<String, (usize, PathBuf)>> {
    let snapshot = handle(
        unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid) },
        "module snapshot",
    )?;
    let mut entry: MODULEENTRY32W = unsafe { zeroed() };
    entry.dwSize = size_of::<MODULEENTRY32W>() as u32;
    if unsafe { Module32FirstW(snapshot.0, &mut entry) } == 0 {
        return Err(error("first module"));
    }
    let mut result = BTreeMap::new();
    loop {
        result.insert(
            text(&entry.szModule).to_ascii_lowercase(),
            (
                entry.modBaseAddr as usize,
                PathBuf::from(text(&entry.szExePath)),
            ),
        );
        if unsafe { Module32NextW(snapshot.0, &mut entry) } == 0 {
            break;
        }
    }
    Ok(result)
}

fn remote_system_export(
    loaded: &BTreeMap<String, (usize, PathBuf)>,
    function: &[u8],
) -> Result<usize> {
    unsafe {
        let kernel = GetModuleHandleW(wide(Path::new("kernel32.dll")).as_ptr());
        if kernel.is_null() {
            return Err(error("system module"));
        }
        let address = GetProcAddress(kernel, function.as_ptr())
            .ok_or_else(|| error("system export"))? as *const () as usize;
        let mut owner = ptr::null_mut();
        if GetModuleHandleExW(6, address as *const u16, &mut owner) == 0 {
            return Err(error("export owner"));
        }
        let mut filename = vec![0_u16; 32768];
        let length = GetModuleFileNameW(owner, filename.as_mut_ptr(), filename.len() as u32);
        if length == 0 {
            return Err(error("export owner path"));
        }
        let name = PathBuf::from(String::from_utf16_lossy(&filename[..length as usize]));
        let name = name
            .file_name()
            .ok_or_else(|| HostError::new("HOST_MODULE", "Missing system module name"))?
            .to_string_lossy()
            .to_ascii_lowercase();
        let target = loaded.get(&name).ok_or_else(|| {
            HostError::new(
                "HOST_MODULE",
                "Host has not loaded the matching system module",
            )
        })?;
        target
            .0
            .checked_add(address - owner as usize)
            .ok_or_else(|| HostError::new("HOST_ADDRESS", "Invalid export offset"))
    }
}

fn remote_call(process: HANDLE, address: usize, payload: &[u16]) -> Result<u32> {
    unsafe {
        let size = std::mem::size_of_val(payload);
        let allocation = VirtualAllocEx(
            process,
            ptr::null(),
            size,
            MEM_COMMIT | MEM_RESERVE,
            PAGE_READWRITE,
        );
        if allocation.is_null() {
            return Err(error("remote allocation"));
        }
        let mut thread_started = false;
        let mut completed = false;
        let result = (|| {
            let mut written = 0;
            if WriteProcessMemory(
                process,
                allocation,
                payload.as_ptr().cast(),
                size,
                &mut written,
            ) == 0
                || written != size
            {
                return Err(error("extension arguments"));
            }
            let start = std::mem::transmute::<usize, unsafe extern "system" fn(*mut c_void) -> u32>(
                address,
            );
            let thread = handle(
                CreateRemoteThread(
                    process,
                    ptr::null(),
                    0,
                    Some(start),
                    allocation,
                    0,
                    ptr::null_mut(),
                ),
                "extension thread",
            )?;
            thread_started = true;
            if WaitForSingleObject(thread.0, 5000) != 0 {
                return Err(HostError::new(
                    "HOST_TIMEOUT",
                    "Native extension initialization is still running",
                ));
            }
            completed = true;
            let mut exit = 0;
            if GetExitCodeThread(thread.0, &mut exit) == 0 {
                return Err(error("extension result"));
            }
            Ok(exit)
        })();
        // A timed-out thread may still read its argument; retain it until host exit.
        if completed || !thread_started {
            VirtualFreeEx(process, allocation, 0, MEM_RELEASE);
        }
        result
    }
}

fn validate_dll(path: &Path) -> Result<()> {
    let bytes =
        std::fs::read(path).map_err(|error| HostError::new("HOST_DLL", error.to_string()))?;
    if bytes.len() < 64 || &bytes[..2] != b"MZ" {
        return Err(HostError::new("HOST_DLL", "Invalid native extension image"));
    }
    let offset = u32::from_le_bytes(bytes[60..64].try_into().unwrap()) as usize;
    if offset > bytes.len().saturating_sub(6)
        || &bytes[offset..offset + 4] != b"PE\0\0"
        || &bytes[offset + 4..offset + 6] != b"\x64\x86"
    {
        return Err(HostError::new(
            "HOST_DLL",
            "Native extension must be an AMD64 PE image",
        ));
    }
    Ok(())
}

pub fn attach(executable: &Path, digest: &str, dll: &Path, config: &Path) -> Result<Value> {
    if !dll.is_absolute() || !config.is_absolute() {
        return Err(HostError::new(
            "HOST_PATH",
            "Explicit absolute extension paths are required",
        ));
    }
    let configuration: Value = serde_json::from_slice(
        &std::fs::read(config).map_err(|error| HostError::new("HOST_CONFIG", error.to_string()))?,
    )
    .map_err(|_| HostError::new("HOST_CONFIG", "Invalid configuration JSON"))?;
    validate_native_config(&configuration)?;
    validate_dll(dll)?;
    let report = probe(executable, digest)?;
    if report["running"] != true {
        return Err(HostError::new(
            "HOST_NOT_READY",
            "Open the Stream Deck application",
        ));
    }
    validate_host_file(executable, report["executableSha256"].as_str().unwrap())?;
    let pid = report["pid"].as_u64().unwrap() as u32;
    let process = handle(
        unsafe { OpenProcess(0x043a, 0, pid) },
        "open host extension target",
    )?;
    if !same_path(&process_path(process.0)?, executable)
        || status::inspect(pid)?["created"] != report["created"]
    {
        return Err(HostError::new(
            "HOST_IDENTITY",
            "Host process changed during attachment",
        ));
    }
    let mut loaded = modules(pid)?;
    if !["qt6core.dll", "qt6widgets.dll", "qt6network.dll"]
        .iter()
        .all(|name| loaded.contains_key(*name))
    {
        return Err(HostError::new(
            "HOST_QT",
            "Host Qt interfaces are not ready",
        ));
    }
    let name = dll
        .file_name()
        .unwrap()
        .to_string_lossy()
        .to_ascii_lowercase();
    if !loaded.contains_key(&name) {
        let load = remote_system_export(&loaded, b"LoadLibraryW\0")?;
        remote_call(process.0, load, &wide(dll))?;
        loaded = modules(pid)?;
    }
    let target = loaded
        .get(&name)
        .ok_or_else(|| HostError::new("HOST_DLL", "Extension did not load"))?;
    if !same_path(&target.1, dll) && file_digest(&target.1)? != file_digest(dll)? {
        return Err(HostError::new(
            "HOST_DLL",
            "Another extension image has the same filename",
        ));
    }
    unsafe {
        let local = LoadLibraryExW(wide(dll).as_ptr(), ptr::null_mut(), 1);
        if local.is_null() {
            return Err(error("map extension image"));
        }
        let export = GetProcAddress(local, c"HellgatoInitialize".as_ptr().cast());
        let address =
            export.map(|function| target.0 + function as *const () as usize - local as usize);
        FreeLibrary(local);
        let address = address.ok_or_else(|| {
            HostError::new("HOST_DLL", "Extension initialization export is missing")
        })?;
        let result = remote_call(process.0, address, &wide(config))?;
        if result != 0 {
            return Err(HostError::new(
                "HOST_CONFIG",
                format!("Extension rejected configuration ({result})"),
            ));
        }
    }
    Ok(json!({"event":"nativeUiLoaded","pid":pid,"created":report["created"],"dll":dll}))
}
