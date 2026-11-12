use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Component, Path},
};

pub fn verify(root: &Path) -> Result<(), String> {
    let root = root.canonicalize().map_err(|error| error.to_string())?;
    let manifest: Value = serde_json::from_slice(
        &fs::read(root.join("bundle-manifest.json")).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    if manifest["schemaVersion"] != 1 || manifest["kind"] != "hellgato-stream-dog-rust" {
        return Err("Unsupported runtime bundle".into());
    }
    let files = manifest["files"]
        .as_object()
        .ok_or("Missing runtime bundle inventory")?;
    if files.is_empty() || files.len() > 20000 {
        return Err("Invalid runtime bundle inventory".into());
    }
    for (name, expected) in files {
        let relative = Path::new(name);
        if relative.is_absolute()
            || relative
                .components()
                .any(|part| !matches!(part, Component::Normal(_)))
        {
            return Err("Bundle file escapes its root".into());
        }
        let path = root
            .join(relative)
            .canonicalize()
            .map_err(|error| error.to_string())?;
        if !path.starts_with(&root) || !path.is_file() {
            return Err("Bundle file is outside its inventory root".into());
        }
        let bytes = fs::read(path).map_err(|error| error.to_string())?;
        if expected.as_str() != Some(format!("{:x}", Sha256::digest(bytes)).as_str()) {
            return Err(format!("Runtime bundle file changed: {name}"));
        }
    }
    Ok(())
}
