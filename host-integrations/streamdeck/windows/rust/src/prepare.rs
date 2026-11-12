use crate::{file_digest, validate_host_file, HostError, Result};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

const OWNER: &str = "Hellgato.Startup.VERSION/1";
const DLL: &str = "version.dll";
const PLAN: &str = "HellgatoStartup.plan";
const MANIFEST: &str = "HellgatoStartup.json";
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn read(path: &Path) -> Result<Vec<u8>> {
    fs::read(path).map_err(|error| HostError::new("HOST_PREPARE_FILE", error.to_string()))
}

pub fn validate_owned(folder: &Path) -> Result<Option<Value>> {
    let path = folder.join(MANIFEST);
    if !path.exists() {
        if folder.join(DLL).exists() || folder.join(PLAN).exists() {
            return Err(HostError::new(
                "HOST_OWNERSHIP",
                "Existing startup files are not owned by Hellgato",
            ));
        }
        return Ok(None);
    }
    let value: Value = serde_json::from_slice(&read(&path)?)
        .map_err(|_| HostError::new("HOST_OWNERSHIP", "Invalid startup ownership manifest"))?;
    let files = value["files"]
        .as_object()
        .ok_or_else(|| HostError::new("HOST_OWNERSHIP", "Missing startup file inventory"))?;
    if value["owner"] != OWNER
        || files.len() != 2
        || !files.contains_key(DLL)
        || !files.contains_key(PLAN)
    {
        return Err(HostError::new(
            "HOST_OWNERSHIP",
            "Unknown startup file owner",
        ));
    }
    for (name, expected) in files {
        if expected.as_str() != Some(file_digest(&folder.join(name))?.as_str()) {
            return Err(HostError::new(
                "HOST_OWNERSHIP",
                format!("Startup file changed outside Hellgato: {name}"),
            ));
        }
    }
    Ok(Some(value))
}

pub fn prepare(executable: &Path, expected: &str, proxy: &Path, plan: &Path) -> Result<Value> {
    validate_host_file(executable, expected)?;
    if !proxy.is_absolute() || !plan.is_absolute() {
        return Err(HostError::new(
            "HOST_PATH",
            "Absolute reviewed startup files are required",
        ));
    }
    let (proxy, plan) = (read(proxy)?, read(plan)?);
    if !proxy.starts_with(b"MZ")
        || !proxy
            .windows(b"HellgatoStartupIdentity".len())
            .any(|part| part == b"HellgatoStartupIdentity")
        || plan.len() < 72
        || &plan[..8] != b"HGSTART1"
        || digest(&read(executable)?) != expected.to_ascii_lowercase()
        || plan[36..68]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
            != expected.to_ascii_lowercase()
    {
        return Err(HostError::new(
            "HOST_PLAN",
            "Startup plan does not match the reviewed Stream Deck executable",
        ));
    }
    let folder = executable
        .parent()
        .ok_or_else(|| HostError::new("HOST_PATH", "Missing host directory"))?;
    let record = json!({"owner":OWNER,"executableSha256":expected.to_ascii_lowercase(),"files":{"version.dll":digest(&proxy),"HellgatoStartup.plan":digest(&plan)}});
    if validate_owned(folder)?.as_ref() == Some(&record) {
        return Ok(json!({"prepared":true,"changed":false}));
    }
    #[cfg(windows)]
    if !crate::windows::matching_processes(executable)?.is_empty() {
        return Err(HostError::new(
            "HOST_RUNNING",
            "Close Stream Deck yourself before preparing startup support",
        ));
    }
    let mut contents = BTreeMap::new();
    contents.insert(DLL, proxy);
    contents.insert(PLAN, plan);
    contents.insert(
        MANIFEST,
        serde_json::to_vec_pretty(&record)
            .map_err(|error| HostError::new("HOST_PLAN", error.to_string()))?,
    );
    validate_owned(folder)?;
    replace_files(folder, &contents)?;
    Ok(json!({"prepared":true,"changed":true}))
}

fn replace_files(folder: &Path, contents: &BTreeMap<&str, Vec<u8>>) -> Result<()> {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let mut originals = BTreeMap::new();
    let mut staged = Vec::new();
    let mut changed = Vec::new();
    let result: std::io::Result<()> = (|| {
        for (name, bytes) in contents {
            let path = folder.join(name);
            originals.insert(
                *name,
                if path.exists() {
                    Some(fs::read(&path)?)
                } else {
                    None
                },
            );
            let temporary = folder.join(format!(
                ".hellgato-rust-{}-{nonce}-{name}",
                std::process::id()
            ));
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)?;
            staged.push((*name, temporary));
            file.write_all(bytes)?;
            file.sync_all()?;
        }
        for name in [DLL, PLAN, MANIFEST] {
            let temporary = &staged.iter().find(|entry| entry.0 == name).unwrap().1;
            fs::rename(temporary, folder.join(name))?;
            changed.push(name);
        }
        Ok(())
    })();
    if result.is_err() {
        for name in changed.into_iter().rev() {
            match &originals[name] {
                Some(bytes) => {
                    let _ = fs::write(folder.join(name), bytes);
                }
                None => {
                    let _ = fs::remove_file(folder.join(name));
                }
            }
        }
    }
    for (_, temporary) in staged {
        let _ = fs::remove_file(temporary);
    }
    result.map_err(|error| {
        HostError::new(
            if error.kind() == std::io::ErrorKind::PermissionDenied {
                "HOST_PERMISSIONS"
            } else {
                "HOST_PREPARE_WRITE"
            },
            error.to_string(),
        )
    })
}

pub fn remove(executable: &Path) -> Result<Value> {
    if !executable.is_absolute() {
        return Err(HostError::new(
            "HOST_PATH",
            "An absolute host path is required",
        ));
    }
    let folder = executable
        .parent()
        .ok_or_else(|| HostError::new("HOST_PATH", "Missing host directory"))?;
    if !folder.join(MANIFEST).exists() {
        return Ok(json!({"removed":false}));
    }
    validate_owned(folder)?;
    #[cfg(windows)]
    if !crate::windows::matching_processes(executable)?.is_empty() {
        return Err(HostError::new(
            "HOST_RUNNING",
            "Close Stream Deck yourself before removing startup support",
        ));
    }
    for name in [DLL, PLAN, MANIFEST] {
        fs::remove_file(folder.join(name))
            .map_err(|error| HostError::new("HOST_REMOVE", error.to_string()))?;
    }
    Ok(json!({"removed":true}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn removal_preserves_foreign_files_and_requires_matching_ownership() {
        let folder = std::env::temp_dir().join(format!(
            "hellgato-host-remove-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&folder).unwrap();
        let exe = folder.join("StreamDeck.exe");
        fs::write(&exe, b"fixture").unwrap();
        fs::write(folder.join("other.dll"), b"keep").unwrap();
        fs::write(folder.join(DLL), b"owned").unwrap();
        fs::write(folder.join(PLAN), b"plan").unwrap();
        assert_eq!(remove(&exe).unwrap()["removed"], false);
        assert!(folder.join(DLL).exists());
        fs::write(folder.join(MANIFEST),serde_json::to_vec(&json!({"owner":OWNER,"files":{"version.dll":digest(b"owned"),"HellgatoStartup.plan":digest(b"plan")}})).unwrap()).unwrap();
        assert_eq!(remove(&exe).unwrap()["removed"], true);
        assert!(folder.join("other.dll").exists());
        assert!(!folder.join(DLL).exists());
        assert!(folder
            .canonicalize()
            .unwrap()
            .starts_with(std::env::temp_dir().canonicalize().unwrap()));
        fs::remove_dir_all(folder).unwrap();
    }
    #[test]
    fn unknown_or_altered_startup_files_are_not_replaced() {
        let folder = std::env::temp_dir().join(format!(
            "hellgato-host-owner-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&folder).unwrap();
        fs::write(folder.join(DLL), b"unrelated").unwrap();
        assert!(validate_owned(&folder).is_err());
        fs::write(folder.join(PLAN), b"plan").unwrap();
        let record = json!({"owner":OWNER,"files":{"version.dll":digest(b"unrelated"),"HellgatoStartup.plan":digest(b"plan")}});
        fs::write(folder.join(MANIFEST), serde_json::to_vec(&record).unwrap()).unwrap();
        assert!(validate_owned(&folder).unwrap().is_some());
        fs::write(folder.join(PLAN), b"changed").unwrap();
        assert!(validate_owned(&folder).is_err());
        assert!(folder
            .canonicalize()
            .unwrap()
            .starts_with(std::env::temp_dir().canonicalize().unwrap()));
        fs::remove_dir_all(folder).unwrap();
    }
}
