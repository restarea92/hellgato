use serde_json::Value;
use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    time::Duration,
};

const MAX_BODY: usize = 2 * 1024 * 1024;
pub fn address(endpoint: &str) -> Result<String, String> {
    let value = endpoint
        .strip_prefix("http://127.0.0.1:")
        .ok_or("Runtime must use numeric loopback HTTP")?;
    if value.is_empty()
        || value.contains('/')
        || value.parse::<u16>().ok().filter(|port| *port > 0).is_none()
    {
        return Err("Invalid runtime endpoint".into());
    }
    Ok(format!("127.0.0.1:{value}"))
}
pub fn request(
    endpoint: &str,
    token: &str,
    action: Option<(&str, &Value)>,
) -> Result<Value, String> {
    let address = address(endpoint)?;
    let mut stream = TcpStream::connect_timeout(
        &address
            .parse::<SocketAddr>()
            .map_err(|error| error.to_string())?,
        Duration::from_secs(2),
    )
    .map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| error.to_string())?;
    let (method, path, body) = match action {
        Some((action, params)) => (
            "POST",
            "/api/command",
            serde_json::to_vec(&serde_json::json!({"action":action,"params":params}))
                .map_err(|error| error.to_string())?,
        ),
        None => ("GET", "/api/state", Vec::new()),
    };
    let header = format!("{method} {path} HTTP/1.0\r\nHost: {address}\r\nAuthorization: Bearer {token}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n", body.len());
    stream
        .write_all(header.as_bytes())
        .and_then(|_| stream.write_all(&body))
        .map_err(|error| error.to_string())?;
    let mut bytes = Vec::new();
    stream
        .take((MAX_BODY + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > MAX_BODY {
        return Err("Runtime response exceeded its byte limit".into());
    }
    let split = bytes
        .windows(4)
        .position(|part| part == b"\r\n\r\n")
        .ok_or("Invalid HTTP response")?;
    let header = String::from_utf8_lossy(&bytes[..split]);
    let status = header
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or("Invalid HTTP status")?;
    let value: Value =
        serde_json::from_slice(&bytes[split + 4..]).map_err(|error| error.to_string())?;
    if status != 200 {
        return Err(value["error"]
            .as_str()
            .unwrap_or("Runtime request failed")
            .into());
    }
    Ok(value)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn endpoints_are_numeric_loopback_only() {
        assert_eq!(address("http://127.0.0.1:5345").unwrap(), "127.0.0.1:5345");
        for value in [
            "http://example.com:5345",
            "http://127.0.0.1:5345/",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
        ] {
            assert!(address(value).is_err());
        }
    }
}
