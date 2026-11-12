use hellgato_windows_host::{attach, probe};
use std::{collections::HashMap, path::PathBuf};

fn main() {
    let mut args = std::env::args().skip(1);
    let mode = args.next().unwrap_or_default();
    let mut values = HashMap::new();
    while let Some(key) = args.next() {
        let Some(value) = args.next() else {
            eprintln!("Missing argument value");
            std::process::exit(2);
        };
        values.insert(key, value);
    }
    let result = (|| {
        let exe = PathBuf::from(values.get("--exe").ok_or("--exe is required")?);
        let digest = values.get("--sha256").ok_or("--sha256 is required")?;
        match mode.as_str() {
            "--remove" => {
                hellgato_windows_host::prepare::remove(&exe).map_err(|error| error.to_string())
            }
            "--probe" => probe(&exe, digest).map_err(|error| error.to_string()),
            "--attach" => attach(
                &exe,
                digest,
                &PathBuf::from(values.get("--dll").ok_or("--dll is required")?),
                &PathBuf::from(values.get("--config").ok_or("--config is required")?),
            )
            .map_err(|error| error.to_string()),
            "--prepare" => hellgato_windows_host::prepare::prepare(
                &exe,
                digest,
                &PathBuf::from(values.get("--proxy").ok_or("--proxy is required")?),
                &PathBuf::from(values.get("--plan").ok_or("--plan is required")?),
            )
            .map_err(|error| error.to_string()),
            _ => Err("Use --probe, --attach, or --prepare".into()),
        }
    })();
    match result {
        Ok(value) => println!("{value}"),
        Err(message) => {
            println!("{}", serde_json::json!({"ready":false,"error":message}));
            std::process::exit(1);
        }
    }
}
