use base64::{engine::general_purpose::STANDARD, Engine};
use image::{codecs::jpeg::JpegEncoder, RgbImage};
use serde_json::{json, Value};
use std::{
    io::Write,
    process::{Command, Stdio},
};

#[test]
fn fake_process_accepts_full_frames_and_keeps_stdout_as_jsonl() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_HellgatoMiraboxRustWorker"))
        .arg("--fake")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut jpeg = Vec::new();
    JpegEncoder::new_with_quality(&mut jpeg, 85)
        .encode_image(&RgbImage::new(800, 480))
        .unwrap();
    let requests = [
        json!({"id":1,"method":"discover"}),
        json!({"id":2,"method":"open","params":{"deviceId":"fake-n4-pro","generation":9,"sessionId":"process-test"}}),
        json!({"id":3,"method":"frame","params":{"jpeg":STANDARD.encode(&jpeg),"width":800,"height":480}}),
        json!({"id":4,"method":"close"}),
    ];
    let mut input = child.stdin.take().unwrap();
    for request in requests {
        writeln!(input, "{request}").unwrap();
    }
    drop(input);
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let messages: Vec<Value> = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(messages.len(), 5);
    assert_eq!(messages[0]["event"], "ready");
    assert_eq!(messages[0]["simulation"], true);
    assert_eq!(messages[2]["result"]["generation"], 9);
    assert_eq!(messages[3]["result"]["status"], "applied");
    assert_eq!(messages[3]["result"]["verification"], "simulation");
    assert_eq!(messages[4]["result"]["status"], "closed");
}

#[test]
fn describe_runs_without_a_vendor_runtime() {
    let output = Command::new(env!("CARGO_BIN_EXE_HellgatoMiraboxRustWorker"))
        .arg("--describe")
        .output()
        .unwrap();
    assert!(output.status.success());
    let descriptor: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(descriptor["capabilities"]["fullFrameOnly"], true);
    assert_eq!(descriptor["layout"]["stripRect"], json!([0, 353, 800, 112]));
}

#[test]
fn beta_models_accept_only_their_whole_canvas_and_report_compatibility_output() {
    for (model, width, height) in [("n4", 800, 480), ("293s", 532, 262)] {
        let mut child = Command::new(env!("CARGO_BIN_EXE_HellgatoMiraboxRustWorker"))
            .args(["--compat-model", model, "--fake"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let mut jpeg = Vec::new();
        JpegEncoder::new_with_quality(&mut jpeg, 85)
            .encode_image(&RgbImage::new(width, height))
            .unwrap();
        let mut input = child.stdin.take().unwrap();
        for request in [
            json!({"id":1,"method":"open","params":{"deviceId":"fake-n4-pro"}}),
            json!({"id":2,"method":"frame","params":{"width":width,"height":height,"jpeg":STANDARD.encode(&jpeg)}}),
            json!({"id":3,"method":"close"}),
        ] {
            writeln!(input, "{request}").unwrap();
        }
        drop(input);
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success());
        let messages: Vec<Value> = String::from_utf8(output.stdout)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(messages[0]["hardwareVerified"], false);
        assert_eq!(
            messages[0]["capabilities"]["usbOutput"],
            "compatibility-regions"
        );
        assert_eq!(messages[2]["result"]["status"], "applied");
        assert_eq!(messages[3]["result"]["status"], "closed");
    }
}
