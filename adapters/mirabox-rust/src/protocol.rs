use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    io::{self, BufRead, Write},
    time::Instant,
};

use crate::{
    descriptor, frame::decode_frame, input::InputDecoder, native::Backend, AdapterError, Result,
    MAX_MESSAGE_BYTES,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub id: Value,
    pub method: String,
    #[serde(default)]
    pub params: Value,
}

impl Request {
    pub fn parse(bytes: &[u8]) -> Result<Self> {
        if bytes.len() > MAX_MESSAGE_BYTES {
            return Err(AdapterError::new(
                "ERR_PROTOCOL",
                "Message exceeds its byte limit",
            ));
        }
        let request: Self = serde_json::from_slice(bytes)
            .map_err(|_| AdapterError::new("ERR_PROTOCOL", "Invalid request JSON"))?;
        let valid_id = request
            .id
            .as_str()
            .is_some_and(|value| !value.is_empty() && value.len() <= 128)
            || request
                .id
                .as_u64()
                .is_some_and(|value| value <= 9_007_199_254_740_991);
        if !valid_id
            || request.method.is_empty()
            || request.method.len() > 64
            || (!request.params.is_null() && !request.params.is_object())
        {
            return Err(AdapterError::new(
                "ERR_PROTOCOL",
                "Invalid request envelope",
            ));
        }
        Ok(request)
    }
}

pub fn read_request(reader: &mut impl BufRead) -> Result<Option<Request>> {
    let mut line = Vec::new();
    let read = std::io::Read::take(&mut *reader, (MAX_MESSAGE_BYTES + 1) as u64)
        .read_until(b'\n', &mut line)
        .map_err(|_| AdapterError::new("ERR_PROTOCOL", "Cannot read command stream"))?;
    if read == 0 {
        return Ok(None);
    }
    if line.len() > MAX_MESSAGE_BYTES {
        return Err(AdapterError::new(
            "ERR_PROTOCOL",
            "Message exceeds its byte limit",
        ));
    }
    Request::parse(&line).map(Some)
}

fn string<'a>(params: &'a Value, key: &str) -> Result<&'a str> {
    params
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .ok_or_else(|| AdapterError::new("ERR_CONTRACT", format!("Missing or invalid {key}")))
}

fn brightness(value: Option<&Value>) -> Result<u8> {
    value
        .and_then(Value::as_u64)
        .filter(|value| *value <= 100)
        .map(|value| value as u8)
        .ok_or_else(|| {
            AdapterError::new(
                "ERR_CONTRACT",
                "Brightness must be an integer between 0 and 100",
            )
        })
}

pub struct Controller<B: Backend> {
    backend: B,
    device: Option<String>,
    generation: u64,
    session_id: Option<String>,
    sequence: u64,
    started: Instant,
    decoder: InputDecoder,
}

impl<B: Backend> Controller<B> {
    pub fn new(backend: B) -> Self {
        Self {
            backend,
            device: None,
            generation: 0,
            session_id: None,
            sequence: 0,
            started: Instant::now(),
            decoder: InputDecoder::default(),
        }
    }
    pub fn execute(&mut self, request: &Request) -> Value {
        match self.dispatch(&request.method, &request.params) {
            Ok(result) => json!({"id":request.id,"result":result}),
            Err(error) => json!({"id":request.id,"error":error}),
        }
    }
    fn check_output_context(&self, params: &Value) -> Result<()> {
        if let Some(generation) = params.get("generation") {
            if generation.as_u64() != Some(self.generation) {
                return Err(AdapterError::new(
                    "ERR_STALE_GENERATION",
                    "Output belongs to a different device generation",
                ));
            }
        }
        if let Some(device_id) = params.get("deviceId") {
            if device_id.as_str() != self.device.as_deref() {
                return Err(AdapterError::new(
                    "ERR_DEVICE_ID",
                    "Output belongs to a different device",
                ));
            }
        }
        if let Some(session_id) = params.get("sessionId") {
            if session_id.as_str() != self.session_id.as_deref() {
                return Err(AdapterError::new(
                    "ERR_SESSION",
                    "Output belongs to a different session",
                ));
            }
        }
        Ok(())
    }
    fn dispatch(&mut self, method: &str, params: &Value) -> Result<Value> {
        match method {
            "describe" => Ok(descriptor()),
            "discover" => Ok(json!({"devices":self.backend.discover()?})),
            "open" => {
                if self.device.is_some() {
                    return Err(AdapterError::new(
                        "ERR_STATE",
                        "Close the current device before opening",
                    ));
                }
                let device_id = string(params, "deviceId")?;
                let level = if params.get("brightness").is_some() {
                    brightness(params.get("brightness"))?
                } else {
                    52
                };
                let generation = match params.get("generation") {
                    Some(value) => value.as_u64().filter(|value| {
                        *value > self.generation && *value <= 9_007_199_254_740_991
                    }),
                    None => self.generation.checked_add(1),
                }
                .ok_or_else(|| {
                    AdapterError::new("ERR_CONTRACT", "Generation must increase on every open")
                })?;
                let session_id = if params.get("sessionId").is_some() {
                    Some(string(params, "sessionId")?.to_owned())
                } else {
                    None
                };
                self.backend.open(device_id, level)?;
                self.device = Some(device_id.to_owned());
                self.generation = generation;
                self.session_id = session_id;
                self.sequence = 0;
                self.decoder = InputDecoder::default();
                Ok(
                    json!({"status":"open","deviceId":device_id,"generation":generation,"sessionId":self.session_id,"firmware":crate::model::current().firmware()}),
                )
            }
            "frame" => {
                if self.device.is_none() {
                    return Err(AdapterError::new("ERR_DEVICE_CLOSED", "Device is not open"));
                }
                self.check_output_context(params)?;
                if params.get("region").is_some() {
                    return Err(AdapterError::new(
                        "ERR_FRAME",
                        "Partial hardware output is unsupported",
                    ));
                }
                let encoded = params
                    .get("jpeg")
                    .and_then(Value::as_str)
                    .ok_or_else(|| AdapterError::new("ERR_FRAME", "Missing JPEG frame"))?;
                let bytes = decode_frame(
                    encoded,
                    params.get("width").and_then(Value::as_u64).unwrap_or(0),
                    params.get("height").and_then(Value::as_u64).unwrap_or(0),
                )?;
                let started = Instant::now();
                self.backend.frame(&bytes)?;
                Ok(
                    json!({"status":if self.backend.simulated() {"applied"} else {"submitted"},"verification":if self.backend.simulated() {"simulation"} else {"unconfirmed"},"bytes":bytes.len(),"submitMs":started.elapsed().as_secs_f64()*1000.0,
                    "deviceId":self.device,"generation":self.generation}),
                )
            }
            "control" => {
                if self.device.is_none() {
                    return Err(AdapterError::new("ERR_DEVICE_CLOSED", "Device is not open"));
                }
                self.check_output_context(params)?;
                let name = string(params, "control")?;
                let value = match name {
                    "brightness" => Some(brightness(params.get("value"))?),
                    "knobBrightness" if crate::model::current() == crate::model::Model::Pro => {
                        Some(brightness(params.get("value"))?)
                    }
                    "sleep" | "wake" => None,
                    _ => {
                        return Err(AdapterError::new(
                            "ERR_UNSUPPORTED",
                            "Unsupported volatile control for this model",
                        ))
                    }
                };
                self.backend.control(name, value)?;
                Ok(
                    json!({"status":if self.backend.simulated() {"applied"} else {"submitted"},"verification":if self.backend.simulated() {"simulation"} else {"unconfirmed"}}),
                )
            }
            "close" => {
                self.close()?;
                Ok(json!({"status":"closed"}))
            }
            _ => Err(AdapterError::new("ERR_METHOD", "Unknown adapter method")),
        }
    }
    pub fn poll(&mut self) -> Result<Vec<Value>> {
        let mut events = Vec::new();
        self.backend.maintain()?;
        for packet in self.backend.poll()? {
            for mut input in self
                .decoder
                .decode(&packet, self.started.elapsed().as_secs_f64())
            {
                let Some(device) = &self.device else {
                    continue;
                };
                self.sequence += 1;
                input["event"] = json!(if input["type"] == "touchPoint" {
                    "touchPoint"
                } else {
                    "input"
                });
                input["deviceId"] = json!(device);
                input["generation"] = json!(self.generation);
                input["sessionId"] = json!(self.session_id);
                input["sequence"] = json!(self.sequence);
                events.push(input);
            }
        }
        Ok(events)
    }
    pub fn close(&mut self) -> Result<()> {
        let result = self.backend.close();
        self.device = None;
        self.decoder = InputDecoder::default();
        result
    }
}

#[derive(Default)]
pub struct FakeBackend {
    opened: bool,
}
impl Backend for FakeBackend {
    fn simulated(&self) -> bool {
        true
    }
    fn discover(&mut self) -> Result<Vec<Value>> {
        Ok(vec![crate::device_descriptor("fake-n4-pro", true)])
    }
    fn open(&mut self, id: &str, _: u8) -> Result<()> {
        if id != "fake-n4-pro" || self.opened {
            return Err(AdapterError::new(
                "ERR_DEVICE_OPEN",
                "Invalid fake device or open state",
            ));
        }
        self.opened = true;
        Ok(())
    }
    fn frame(&mut self, _: &[u8]) -> Result<()> {
        Ok(())
    }
    fn control(&mut self, _: &str, _: Option<u8>) -> Result<()> {
        Ok(())
    }
    fn poll(&mut self) -> Result<Vec<Vec<u8>>> {
        Ok(Vec::new())
    }
    fn maintain(&mut self) -> Result<()> {
        Ok(())
    }
    fn close(&mut self) -> Result<()> {
        self.opened = false;
        Ok(())
    }
}

pub fn emit(value: &Value) -> io::Result<()> {
    #[cfg(windows)]
    if let Some(output) = RESERVED_OUTPUT.get() {
        let mut output = output
            .lock()
            .map_err(|_| io::Error::other("JSON output lock was poisoned"))?;
        serde_json::to_writer(&mut *output, value)?;
        output.write_all(b"\n")?;
        return output.flush();
    }
    let stdout = io::stdout();
    let mut output = stdout.lock();
    serde_json::to_writer(&mut output, value)?;
    output.write_all(b"\n")?;
    output.flush()
}

#[cfg(windows)]
static RESERVED_OUTPUT: std::sync::OnceLock<std::sync::Mutex<std::fs::File>> =
    std::sync::OnceLock::new();

#[cfg(windows)]
pub fn reserve_output() -> Result<()> {
    use std::{ffi::c_void, os::windows::io::FromRawHandle, ptr};
    #[link(name = "kernel32")]
    extern "system" {
        fn GetStdHandle(which: u32) -> *mut c_void;
        fn SetStdHandle(which: u32, handle: *mut c_void) -> i32;
        fn GetCurrentProcess() -> *mut c_void;
        fn DuplicateHandle(
            source_process: *mut c_void,
            source: *mut c_void,
            target_process: *mut c_void,
            target: *mut *mut c_void,
            access: u32,
            inherit: i32,
            options: u32,
        ) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }
    #[link(name = "ucrt")]
    extern "C" {
        fn _dup2(source: i32, target: i32) -> i32;
    }
    if RESERVED_OUTPUT.get().is_some() {
        return Ok(());
    }
    unsafe {
        let process = GetCurrentProcess();
        let original = GetStdHandle(-11_i32 as u32);
        let stderr = GetStdHandle(-12_i32 as u32);
        let mut reserved = ptr::null_mut();
        if original.is_null()
            || original as isize == -1
            || stderr.is_null()
            || stderr as isize == -1
            || DuplicateHandle(process, original, process, &mut reserved, 0, 0, 2) == 0
        {
            return Err(AdapterError::new(
                "ERR_PROTOCOL",
                "Cannot reserve JSON output handle",
            ));
        }
        if _dup2(2, 1) != 0 || SetStdHandle(-11_i32 as u32, stderr) == 0 {
            CloseHandle(reserved);
            return Err(AdapterError::new(
                "ERR_PROTOCOL",
                "Cannot isolate vendor stdout",
            ));
        }
        let file = std::fs::File::from_raw_handle(reserved);
        RESERVED_OUTPUT
            .set(std::sync::Mutex::new(file))
            .map_err(|_| AdapterError::new("ERR_PROTOCOL", "JSON output was already reserved"))?;
    }
    Ok(())
}

#[cfg(not(windows))]
pub fn reserve_output() -> Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn request(method: &str, params: Value) -> Request {
        Request {
            id: json!(1),
            method: method.into(),
            params,
        }
    }

    #[test]
    fn command_contract_rejects_partial_output_and_persistent_controls() {
        let mut controller = Controller::new(FakeBackend::default());
        assert!(controller.execute(&request("frame", json!({})))["error"].is_object());
        assert_eq!(
            controller.execute(&request(
                "open",
                json!({"deviceId":"fake-n4-pro","generation":7,"sessionId":"test"})
            ))["result"]["generation"],
            7
        );
        assert_eq!(
            controller.execute(&request("frame", json!({"region":{}})))["error"]["code"],
            "ERR_FRAME"
        );
        assert_eq!(
            controller.execute(&request("control", json!({"control":"setStandbyImage"})))["error"]
                ["code"],
            "ERR_UNSUPPORTED"
        );
        assert!(controller.execute(&request(
            "control",
            json!({"control":"brightness","value":101})
        ))["error"]
            .is_object());
        for value in [json!(-1), json!(101), json!(true)] {
            assert!(controller.execute(&request(
                "control",
                json!({"control":"knobBrightness","value":value})
            ))["error"]
                .is_object());
        }
        assert!(controller.execute(&request(
            "control",
            json!({"control":"knobBrightness","value":35})
        ))["result"]
            .is_object());
        controller.close().unwrap();
        assert!(controller.execute(&request(
            "open",
            json!({"deviceId":"fake-n4-pro","generation":7})
        ))["error"]
            .is_object());
    }

    #[test]
    fn bounded_reader_rejects_oversize_and_invalid_envelopes() {
        assert!(Request::parse(br#"{"id":null,"method":"open"}"#).is_err());
        assert!(Request::parse(br#"{"id":1,"method":"open","params":[]}"#).is_err());
        let oversized = vec![b'x'; MAX_MESSAGE_BYTES + 1];
        assert!(read_request(&mut Cursor::new(oversized)).is_err());
        let mut input = Cursor::new(b"{\"id\":1,\"method\":\"describe\"}\n");
        assert_eq!(
            read_request(&mut input).unwrap().unwrap().method,
            "describe"
        );
        assert!(read_request(&mut input).unwrap().is_none());
    }

    #[test]
    fn rejects_stale_and_foreign_output_after_reopen() {
        let mut controller = Controller::new(FakeBackend::default());
        let first = request(
            "open",
            json!({"deviceId":"fake-n4-pro","generation":1,"sessionId":"first"}),
        );
        assert!(controller.execute(&first)["result"].is_object());
        controller.close().unwrap();
        let second = request(
            "open",
            json!({"deviceId":"fake-n4-pro","generation":2,"sessionId":"second"}),
        );
        assert!(controller.execute(&second)["result"].is_object());
        assert_eq!(
            controller.execute(&request(
                "frame",
                json!({"generation":1,"jpeg":"","width":800,"height":480})
            ))["error"]["code"],
            "ERR_STALE_GENERATION"
        );
        assert_eq!(
            controller.execute(&request(
                "control",
                json!({"generation":1,"control":"wake"})
            ))["error"]["code"],
            "ERR_STALE_GENERATION"
        );
        assert_eq!(
            controller.execute(&request(
                "frame",
                json!({"generation":2,"deviceId":"other-device"})
            ))["error"]["code"],
            "ERR_DEVICE_ID"
        );
        assert_eq!(
            controller.execute(&request(
                "frame",
                json!({"generation":2,"sessionId":"first"})
            ))["error"]["code"],
            "ERR_SESSION"
        );
        assert!(controller.execute(&request(
            "control",
            json!({"generation":2,"sessionId":"second","deviceId":"fake-n4-pro","control":"wake"})
        ))["result"]
            .is_object());
    }
}
