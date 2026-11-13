use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{fmt, path::Path};

pub mod prepare;
pub mod status;
#[cfg(windows)]
mod windows;

#[derive(Debug, Serialize)]
pub struct HostError {
    pub code: String,
    pub message: String,
}
impl HostError {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}
impl fmt::Display for HostError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for HostError {}
pub type AdapterError = HostError;
pub type Result<T> = std::result::Result<T, HostError>;
pub fn file_digest(path: &Path) -> Result<String> {
    let bytes =
        std::fs::read(path).map_err(|error| HostError::new("HOST_FILE", error.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}
pub fn validate_host_path(path: &Path) -> Result<()> {
    if !path.is_absolute()
        || !path.file_name().is_some_and(|name| {
            name.to_string_lossy()
                .eq_ignore_ascii_case("StreamDeck.exe")
        })
        || !path.is_file()
    {
        return Err(HostError::new(
            "HOST_PATH",
            "An existing absolute StreamDeck.exe path is required",
        ));
    }
    Ok(())
}
pub fn validate_host_file(path: &Path, digest: &str) -> Result<()> {
    validate_host_path(path)?;
    if digest.len() != 64
        || !digest.bytes().all(|byte| byte.is_ascii_hexdigit())
        || file_digest(path)? != digest.to_ascii_lowercase()
    {
        return Err(HostError::new(
            "HOST_IDENTITY",
            "The reviewed Stream Deck executable does not match",
        ));
    }
    Ok(())
}
pub fn validate_native_config(value: &serde_json::Value) -> Result<()> {
    let token = value
        .get("token")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    let serial = value
        .get("serial")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    if value["endpoint"] != "http://127.0.0.1:5345"
        || token.len() < 32
        || token.len() > 256
        || token.chars().any(char::is_whitespace)
        || serial.len() != 12
        || !serial.starts_with("HGMOCK")
        || !serial[6..].bytes().all(|byte| byte.is_ascii_digit())
        || value
            .get("autoReconnect")
            .is_some_and(|value| !value.is_boolean())
    {
        return Err(HostError::new(
            "HOST_CONFIG",
            "Invalid local native extension configuration",
        ));
    }
    Ok(())
}
#[cfg(windows)]
pub use windows::{attach, probe};
#[cfg(not(windows))]
pub fn probe(_: &Path, _: &str) -> Result<serde_json::Value> {
    Err(HostError::new("HOST_PLATFORM", "Windows x64 is required"))
}
#[cfg(not(windows))]
pub fn attach(_: &Path, _: &str, _: &Path, _: &Path) -> Result<serde_json::Value> {
    Err(HostError::new("HOST_PLATFORM", "Windows x64 is required"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_config_requires_loopback_and_one_named_virtual_device() {
        let valid = serde_json::json!({"endpoint":"http://127.0.0.1:5345","token":"a".repeat(64),"serial":"HGMOCK000003","autoReconnect":true});
        assert!(validate_native_config(&valid).is_ok());
        for (key, replacement) in [
            ("endpoint", serde_json::json!("http://example.com:5345")),
            ("serial", serde_json::json!("other")),
            ("autoReconnect", serde_json::json!(1)),
            ("token", serde_json::json!("short")),
        ] {
            let mut changed = valid.clone();
            changed[key] = replacement;
            assert!(validate_native_config(&changed).is_err());
        }
    }
}
