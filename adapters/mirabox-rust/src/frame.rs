use std::io::Cursor;

use base64::{engine::general_purpose::STANDARD, Engine};
use image::{ImageFormat, ImageReader, Limits};

use crate::{AdapterError, Result, MAX_FRAME_BYTES};

pub fn decode_frame(encoded: &str, width: u64, height: u64) -> Result<Vec<u8>> {
    let expected = crate::model::current().canvas();
    if width != expected.0 as u64
        || height != expected.1 as u64
        || encoded.len() > MAX_FRAME_BYTES.div_ceil(3) * 4
    {
        return Err(AdapterError::new(
            "ERR_FRAME",
            "Only complete model-sized JPEG frames are accepted",
        ));
    }
    let bytes = STANDARD
        .decode(encoded)
        .map_err(|_| AdapterError::new("ERR_FRAME", "Invalid canonical base64"))?;
    if bytes.is_empty() || bytes.len() > MAX_FRAME_BYTES || STANDARD.encode(&bytes) != encoded {
        return Err(AdapterError::new(
            "ERR_FRAME",
            "Invalid frame byte count or base64 encoding",
        ));
    }
    validate_jpeg(&bytes)?;
    Ok(bytes)
}

pub fn validate_jpeg(bytes: &[u8]) -> Result<()> {
    let invalid = || {
        AdapterError::new(
            "ERR_FRAME",
            "Expected one baseline RGB JPEG of exactly 800x480 pixels",
        )
    };
    if bytes.len() > MAX_FRAME_BYTES
        || !bytes.starts_with(&[0xff, 0xd8])
        || !bytes.ends_with(&[0xff, 0xd9])
    {
        return Err(invalid());
    }
    let mut offset = 2;
    let mut found = false;
    while offset + 1 < bytes.len() {
        if bytes[offset] != 0xff {
            return Err(invalid());
        }
        while offset < bytes.len() && bytes[offset] == 0xff {
            offset += 1;
        }
        let marker = *bytes.get(offset).ok_or_else(invalid)?;
        offset += 1;
        if marker == 0xda {
            break;
        }
        if marker == 0xd9 || marker == 0x00 || (0xd0..=0xd8).contains(&marker) {
            return Err(invalid());
        }
        let length = u16::from_be_bytes([
            *bytes.get(offset).ok_or_else(invalid)?,
            *bytes.get(offset + 1).ok_or_else(invalid)?,
        ]) as usize;
        if length < 2 || offset + length > bytes.len() {
            return Err(invalid());
        }
        if (0xc0..=0xcf).contains(&marker) && ![0xc4, 0xc8, 0xcc].contains(&marker) {
            if marker != 0xc0 || found || length < 8 {
                return Err(invalid());
            }
            let precision = bytes[offset + 2];
            let height = u16::from_be_bytes([bytes[offset + 3], bytes[offset + 4]]);
            let width = u16::from_be_bytes([bytes[offset + 5], bytes[offset + 6]]);
            let expected = crate::model::current().canvas();
            if precision != 8
                || u32::from(width) != expected.0
                || u32::from(height) != expected.1
                || bytes[offset + 7] != 3
            {
                return Err(invalid());
            }
            found = true;
        }
        offset += length;
    }
    if !found {
        return Err(invalid());
    }
    let mut reader = ImageReader::with_format(Cursor::new(bytes), ImageFormat::Jpeg);
    let mut limits = Limits::default();
    limits.max_image_width = Some(800);
    limits.max_image_height = Some(480);
    limits.max_alloc = Some(8 * 1024 * 1024);
    reader.limits(limits);
    let decoded = reader.decode().map_err(|_| invalid())?;
    if (decoded.width(), decoded.height()) != crate::model::current().canvas() {
        return Err(invalid());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{codecs::jpeg::JpegEncoder, RgbImage};

    pub fn jpeg(width: u32, height: u32) -> Vec<u8> {
        let mut bytes = Vec::new();
        JpegEncoder::new_with_quality(&mut bytes, 85)
            .encode_image(&RgbImage::new(width, height))
            .unwrap();
        bytes
    }

    #[test]
    fn accepts_only_full_valid_baseline_frame() {
        let bytes = jpeg(800, 480);
        assert_eq!(
            decode_frame(&STANDARD.encode(&bytes), 800, 480).unwrap(),
            bytes
        );
        assert!(decode_frame(&STANDARD.encode(jpeg(200, 100)), 800, 480).is_err());
        assert!(decode_frame(&STANDARD.encode(&bytes), 200, 100).is_err());
    }

    #[test]
    fn rejects_truncated_and_progressive_frames() {
        let mut bytes = jpeg(800, 480);
        bytes.truncate(bytes.len() / 2);
        assert!(validate_jpeg(&bytes).is_err());
        let mut progressive = jpeg(800, 480);
        let marker = progressive
            .windows(2)
            .position(|pair| pair == [0xff, 0xc0])
            .unwrap();
        progressive[marker + 1] = 0xc2;
        assert!(validate_jpeg(&progressive).is_err());
    }
}
