use serde_json::{json, Value};

#[derive(Default)]
pub struct InputDecoder {
    first: Option<(u16, u16)>,
    last: Option<(u16, u16)>,
    started: f64,
    updated: f64,
}

impl InputDecoder {
    pub fn decode(&mut self, data: &[u8], now: f64) -> Vec<Value> {
        self.decode_model(data, now, crate::model::current())
    }
    fn decode_model(&mut self, data: &[u8], now: f64, model: crate::model::Model) -> Vec<Value> {
        if model != crate::model::Model::Pro {
            let data = if data.len() > 1 && data[0] == 0 {
                &data[1..]
            } else {
                data
            };
            if data.len() < 11 || !data.starts_with(b"ACK") || &data[5..7] != b"OK" {
                return vec![];
            }
            let (code, state) = (data[9], data[10]);
            if model == crate::model::Model::S293 {
                const MAP: [usize; 16] = [99, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11, 0, 5, 10];
                return if (1..=15).contains(&code) && state == 0 {
                    vec![
                        json!({"type":"key","index":MAP[code as usize],"state":"down","physicalRelease":"unavailable"}),
                        json!({"type":"key","index":MAP[code as usize],"state":"up","synthetic":true}),
                    ]
                } else {
                    vec![]
                };
            }
            if (0x40..=0x43).contains(&code) && state == 0 {
                return vec![
                    json!({"type":"touch","gesture":"tap","x":u16::from(code-0x40)*200+100,"y":50,"coordinates":"synthetic-region-center"}),
                ];
            }
            if (1..=10).contains(&code) && state > 1 {
                return vec![];
            }
            return self.decode_n4(data, now);
        }
        self.decode_n4(data, now)
    }
    fn decode_n4(&mut self, data: &[u8], now: f64) -> Vec<Value> {
        if data.len() >= 14 && data.starts_with(b"ACK") && &data[4..7] == b"ARX" {
            let x = u16::from_be_bytes([data[10], data[11]]);
            let raw_y = u16::from_be_bytes([data[12], data[13]]);
            if x >= 800 || raw_y >= 112 {
                return Vec::new();
            }
            let point = (x, raw_y * 100 / 112);
            if self.first.is_none() || now - self.updated > 0.25 {
                self.first = Some(point);
                self.started = now;
            }
            self.last = Some(point);
            self.updated = now;
            return vec![json!({"type":"touchPoint","x":x,"y":point.1,"rawY":raw_y})];
        }
        if data.len() < 11 || !data.starts_with(b"ACK") || &data[5..7] != b"OK" || data[9] == 0xff {
            return Vec::new();
        }
        let code = data[9];
        let state = data[10];
        let mut values = Vec::new();
        if (1..=10).contains(&code) {
            values.push(
                json!({"type":"key","index":code - 1,"state":if state == 1 {"down"} else {"up"}}),
            );
        } else if let Some((index, delta)) = match code {
            0xa0 => Some((0, -1)),
            0xa1 => Some((0, 1)),
            0x50 => Some((1, -1)),
            0x51 => Some((1, 1)),
            0x90 => Some((2, -1)),
            0x91 => Some((2, 1)),
            0x70 => Some((3, -1)),
            0x71 => Some((3, 1)),
            _ => None,
        } {
            values.push(json!({"type":"dial","kind":"rotate","index":index,"delta":delta}));
        } else if state == 1 && [0x37, 0x35, 0x33, 0x36].contains(&code) {
            let index = [0x37, 0x35, 0x33, 0x36]
                .iter()
                .position(|value| *value == code)
                .unwrap();
            values.push(json!({"type":"dial","kind":"pulse","index":index}));
        } else if code == 0x38 || code == 0x39 {
            if let Some(value) = self.finish(now, Some(code)) {
                values.push(value);
            }
        } else if state != 1 && ((11..=14).contains(&code) || (0x40..=0x43).contains(&code)) {
            if let Some(value) = self.finish(now, None) {
                values.push(value);
            }
        }
        for value in &mut values {
            value["raw"] = json!({"code":code,"state":state});
        }
        values
    }

    fn finish(&mut self, now: f64, direction: Option<u8>) -> Option<Value> {
        let fresh = self.first.is_some() && now - self.updated <= 0.25;
        let result = if let Some(direction) = direction {
            let (start, end) =
                if fresh && self.last.unwrap().0.abs_diff(self.first.unwrap().0) >= 20 {
                    (self.first.unwrap(), self.last.unwrap())
                } else if direction == 0x39 {
                    ((700, 50), (100, 50))
                } else {
                    ((100, 50), (700, 50))
                };
            Some(
                json!({"type":"touch","gesture":"swipe","x":start.0,"y":start.1,"endX":end.0,"endY":end.1}),
            )
        } else if fresh {
            let start = self.first.unwrap();
            Some(
                json!({"type":"touch","gesture":if now - self.started >= 0.5 {"hold"} else {"tap"},"x":start.0,"y":start.1}),
            )
        } else {
            None
        };
        self.first = None;
        self.last = None;
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_input_keeps_measured_mapping_and_identifies_synthetic_events() {
        let mut decoder = InputDecoder::default();
        let pulses = decoder.decode_model(&button(13, 0), 0.0, crate::model::Model::S293);
        assert_eq!(pulses.len(), 2);
        assert_eq!(pulses[0]["index"], 0);
        assert_eq!(pulses[1]["synthetic"], true);
        assert!(decoder
            .decode_model(&button(13, 1), 0.0, crate::model::Model::S293)
            .is_empty());
        let touch = decoder.decode_model(&button(0x43, 0), 0.0, crate::model::Model::N4);
        assert_eq!(touch[0]["x"], 700);
        assert_eq!(touch[0]["coordinates"], "synthetic-region-center");
    }

    fn button(code: u8, state: u8) -> [u8; 14] {
        let mut bytes = [0; 14];
        bytes[..3].copy_from_slice(b"ACK");
        bytes[5..7].copy_from_slice(b"OK");
        bytes[9] = code;
        bytes[10] = state;
        bytes
    }

    #[test]
    fn ignores_write_ack_and_preserves_physical_key_numbers() {
        let mut decoder = InputDecoder::default();
        assert!(decoder.decode(&button(0xff, 1), 0.0).is_empty());
        assert_eq!(decoder.decode(&button(5, 1), 0.0)[0]["index"], 4);
        assert_eq!(decoder.decode(&button(10, 0), 0.0)[0]["state"], "up");
        assert_eq!(decoder.decode(&button(0x70, 0), 0.0)[0]["delta"], -1);
    }

    #[test]
    fn touch_uses_local_sensor_coordinates_and_release_finishes_tap() {
        let mut decoder = InputDecoder::default();
        let mut point = [0; 14];
        point[..3].copy_from_slice(b"ACK");
        point[4..7].copy_from_slice(b"ARX");
        point[10..12].copy_from_slice(&470_u16.to_be_bytes());
        point[12..14].copy_from_slice(&111_u16.to_be_bytes());
        let sample = decoder.decode(&point, 1.0);
        assert_eq!(sample[0]["x"], 470);
        assert_eq!(sample[0]["y"], 99);
        let tap = decoder.decode(&button(0x42, 0), 1.1);
        assert_eq!(tap[0]["gesture"], "tap");
        assert_eq!(tap[0]["x"], 470);
        assert_eq!(tap[0]["y"], 99);
        assert!(decoder.decode(&button(0x42, 0), 1.2).is_empty());
    }
}
