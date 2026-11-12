use std::sync::OnceLock;
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Model {
    Pro,
    N4,
    S293,
}
static MODEL: OnceLock<Model> = OnceLock::new();
pub fn current() -> Model {
    *MODEL.get().unwrap_or(&Model::Pro)
}
pub fn select(name: &str) -> Result<(), String> {
    let model = match name {
        "n4" => Model::N4,
        "293s" => Model::S293,
        _ => return Err("Unknown compatibility model".into()),
    };
    MODEL
        .set(model)
        .map_err(|_| "Model already configured".into())
}
impl Model {
    pub fn id(self) -> &'static str {
        match self {
            Self::Pro => "mirabox",
            Self::N4 => "mirabox-n4-beta",
            Self::S293 => "mirabox-293s-beta",
        }
    }
    pub fn firmware(self) -> &'static str {
        match self {
            Self::Pro => "V4.N4 Pro E.02.010",
            Self::N4 => "V3.TS10N4.01.006",
            Self::S293 => "V2.293S.00.003",
        }
    }
    pub fn usb(self) -> (u16, u16) {
        match self {
            Self::Pro => (0x5548, 0x1021),
            Self::N4 => (0x0400, 0x3001),
            Self::S293 => (0x0400, 0x1000),
        }
    }
    pub fn canvas(self) -> (u32, u32) {
        if self == Self::S293 {
            (532, 262)
        } else {
            (800, 480)
        }
    }
}

pub fn descriptor() -> serde_json::Value {
    use serde_json::json;
    let model = current();
    let ts = model == Model::S293;
    let (width, height) = model.canvas();
    let keys:Vec<_>=(0..if ts {15}else{10}).map(|index|json!({"id":format!("key:{index}"),"rect":if ts {[2+(index%5)*89,1+(index/5)*88,85,85]}else{[7+(index%5)*169,5+(index/5)*174,112,112]},"sourceWidth":if ts {72}else{120},"sourceHeight":if ts {72}else{120}})).collect();
    let mut value = json!({"adapterId":model.id(),"modelId":if ts {"293s"}else{"n4"},"protocolVersion":1,"firmware":model.firmware(),"beta":true,"hardwareVerified":false,
        "layout":{"width":width,"height":height,"rotation":0,"keys":keys},
        "capabilities":{"keys":if ts {15}else{10},"dials":if ts {0}else{4},"touch":!ts,"fullFrameOnly":true,"hardwareFullFrame":false,"usbOutput":"compatibility-regions","width":width,"height":height,"maxFrameBytes":crate::MAX_FRAME_BYTES,"format":"jpeg"}});
    if !ts {
        value["layout"]["strip"] =
            json!({"x":0,"y":353,"width":800,"height":112,"logicalWidth":800,"logicalHeight":100});
    }
    value
}

pub fn regions_for(model: Model) -> Vec<(u8, [u32; 4], u32, u32)> {
    let ts = model == Model::S293;
    let ids: Vec<u8> = if ts {
        vec![
            13, 10, 7, 4, 1, 16, 14, 11, 8, 5, 2, 17, 15, 12, 9, 6, 3, 18,
        ]
    } else {
        vec![11, 12, 13, 14, 15, 6, 7, 8, 9, 10]
    };
    let mut regions: Vec<_> = ids
        .into_iter()
        .enumerate()
        .map(|(i, id)| {
            let i = i as u32;
            if ts {
                (id, [2 + i % 6 * 89, 1 + i / 6 * 88, 85, 85], 85, 85)
            } else {
                (id, [7 + i % 5 * 169, 5 + i / 5 * 174, 112, 112], 112, 112)
            }
        })
        .collect();
    if !ts {
        for (index, x) in [0, 204, 406, 610].into_iter().enumerate() {
            regions.push((index as u8 + 1, [x, 353, 176, 112], 176, 112));
        }
    }
    regions
}
