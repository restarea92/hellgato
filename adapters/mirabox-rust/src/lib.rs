pub mod compatibility;
pub mod frame;
pub mod input;
pub mod model;

use serde::Serialize;
use serde_json::{json, Value};

pub const EXPECTED_FIRMWARE: &str = "V4.N4 Pro E.02.010";
pub const MAX_FRAME_BYTES: usize = 1_048_576;
pub const MAX_MESSAGE_BYTES: usize = 1_500_000;

#[derive(Debug, Clone, Serialize)]
pub struct AdapterError {
    pub code: String,
    pub message: String,
}

impl AdapterError {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

impl std::fmt::Display for AdapterError {
    fn fmt(&self, output: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(output, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for AdapterError {}

pub type Result<T> = std::result::Result<T, AdapterError>;

pub fn descriptor() -> Value {
    if model::current() != model::Model::Pro {
        return model::descriptor();
    }
    let keys: Vec<Value> = (0..10)
        .map(|index| {
            json!({
                "id": format!("key:{index}"),
                "rect": [7 + (index % 5) * 169, 5 + (index / 5) * 174, 112, 112],
                "sourceWidth": 120, "sourceHeight": 120,
            })
        })
        .collect();
    json!({
        "adapterId": "mirabox", "modelId": "n4-pro", "protocolVersion": 1,
        "firmware": EXPECTED_FIRMWARE,
        "layout": {
            "width": 800, "height": 480, "rotation": 180,
            "keys": keys, "stripRect": [0, 353, 800, 112],
            "strip": {"x":0,"y":353,"width":800,"height":112,"logicalWidth":800,"logicalHeight":100},
            "logicalTouch": {"width": 800, "height": 100},
            "touchCoordinates": "normalized-local-strip",
        },
        "capabilities": {
            "keys": 10, "dials": 4, "touch": true,
            "fullFrameOnly": true, "format": "jpeg",
            "width": 800, "height": 480, "maxFrameBytes": MAX_FRAME_BYTES,
            "frameRotation": "caller-supplied-wire-180",
            "dialPress": "pulse", "dialRelease": "unavailable",
            "verification": "native-submission-unconfirmed",
        },
    })
}

pub fn device_descriptor(id: &str, simulation: bool) -> Value {
    if model::current() != model::Model::Pro {
        let model = model::current();
        let descriptor = model::descriptor();
        let displays:Vec<Value>=descriptor["layout"]["keys"].as_array().unwrap().iter().map(|key|json!({"id":key["id"],"width":key["sourceWidth"],"height":key["sourceHeight"],"format":"jpeg","partial":false})).chain(if model == model::Model::N4 {vec![json!({"id":"touch","width":800,"height":100,"format":"jpeg","partial":true})]}else{vec![]}).chain(vec![json!({"id":"lcd","width":model.canvas().0,"height":model.canvas().1,"format":"jpeg","partial":false})]).collect();
        let mut value = json!({"id":id,"adapterId":model.id(),"modelId":if model == model::Model::N4 {"n4"}else{"293s"},"displayName":if model == model::Model::N4 {"N4 / TS10N4 · 호환 베타"}else{"293S / TS115 · 호환 베타"},"firmware":if simulation {model.firmware()}else{""},"capabilities":{"keys":descriptor["capabilities"]["keys"],"dials":descriptor["capabilities"]["dials"],"displays":displays,"dialPress":"pulse","dialRelease":"unavailable","features":{"brightness":{"status":"native"},"sleep":{"status":"native"},"wake":{"status":"native"}}}});
        if model == model::Model::N4 {
            value["capabilities"]["touch"] = json!({"width":800,"height":100});
        }
        value["capabilities"]["features"]["keyRelease"] =
            json!({"status":if model==model::Model::S293 {"emulated"}else{"native"}});
        return value;
    }
    let mut displays: Vec<Value> = (0..10).map(|index| json!({
        "id":format!("key:{index}"),"width":112,"height":112,"format":"jpeg","partial":false,
    })).collect();
    displays.push(json!({"id":"touch","width":800,"height":100,"format":"jpeg","partial":true}));
    displays.push(json!({"id":"lcd","width":800,"height":480,"format":"jpeg","partial":false}));
    json!({"id":id,"adapterId":"mirabox","modelId":"n4-pro",
        "firmware":if simulation {EXPECTED_FIRMWARE} else {""},
        "displayName":if simulation {"Simulated N4 Pro E"} else {"Mirabox N4 Pro E"},
        "capabilities":{"keys":10,"dials":4,"touch":{"width":800,"height":100},
            "displays":displays,"dialPress":"pulse","dialRelease":"unavailable",
            "features":{"brightness":{"status":"native"},"sleep":{"status":"native"},"wake":{"status":"native"}}}})
}
