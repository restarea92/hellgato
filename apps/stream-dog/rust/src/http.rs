use serde_json::Value;
use std::{
    io::{BufReader, Read, Write},
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
    let header = format!("{method} {path} HTTP/1.1\r\nHost: {address}\r\nAuthorization: Bearer {token}\r\nConnection: keep-alive\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n", body.len());
    let mut encoded = header.into_bytes();
    encoded.extend_from_slice(&body);
    stream
        .write_all(&encoded)
        .map_err(|error| error.to_string())?;
    read_response(stream)
}

fn read_response(stream: impl Read) -> Result<Value, String> {
    let mut reader = BufReader::new(stream);
    let mut bytes = Vec::new();
    while !bytes.ends_with(b"\r\n\r\n") {
        if bytes.len() >= 16 * 1024 {
            return Err("Runtime response headers exceeded their byte limit".into());
        }
        let mut byte = [0];
        reader.read_exact(&mut byte).map_err(|error| {
            format!("Response headers stopped at {} bytes: {error}", bytes.len())
        })?;
        bytes.push(byte[0]);
    }
    let header = String::from_utf8_lossy(&bytes);
    let status = header
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or("Invalid HTTP status")?;
    let lengths: Vec<_> = header
        .lines()
        .filter_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("content-length")
                .then_some(value.trim())
        })
        .collect();
    if lengths.len() != 1 {
        return Err("Runtime response requires one content length".into());
    }
    let length = lengths[0]
        .parse::<usize>()
        .map_err(|_| "Invalid response content length")?;
    if length > MAX_BODY {
        return Err("Runtime response exceeded its byte limit".into());
    }
    let mut body = vec![0; length];
    reader
        .read_exact(&mut body)
        .map_err(|error| format!("Incomplete response body: {error}"))?;
    let value: Value = serde_json::from_slice(&body).map_err(|error| error.to_string())?;
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

    #[test]
    fn complete_responses_do_not_wait_for_connection_close() {
        struct ResetAfterBody(std::io::Cursor<Vec<u8>>);
        impl Read for ResetAfterBody {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                if self.0.position() == self.0.get_ref().len() as u64 {
                    return Err(std::io::ErrorKind::ConnectionReset.into());
                }
                self.0.read(buffer)
            }
        }
        let bytes = b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{\"ok\":true}";
        assert_eq!(
            read_response(ResetAfterBody(std::io::Cursor::new(bytes.to_vec()))).unwrap(),
            serde_json::json!({"ok":true})
        );
        for bytes in [
            b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\n{}".as_slice(),
            b"HTTP/1.1 200 OK\r\nContent-Length: 9999999\r\n\r\n{}".as_slice(),
            b"HTTP/1.1 200 OK\r\n\r\n{}".as_slice(),
        ] {
            assert!(read_response(std::io::Cursor::new(bytes)).is_err());
        }
    }

    #[test]
    fn a_live_connection_can_finish_one_response_before_eof() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut headers = Vec::new();
            while !headers.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                socket.read_exact(&mut byte).unwrap();
                headers.push(byte[0]);
            }
            socket
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{\"ok\":true}")
                .unwrap();
            let mut byte = [0];
            assert_eq!(socket.read(&mut byte).unwrap(), 0);
        });
        assert_eq!(
            request(&endpoint, "fixture-token", None).unwrap(),
            serde_json::json!({"ok":true})
        );
        server.join().unwrap();
    }
}
