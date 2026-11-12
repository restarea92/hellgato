use crate::{
    model::{self, Model},
    AdapterError, Result,
};

pub fn encode_regions(jpeg: &[u8], model: Model) -> Result<Vec<(u8, Vec<u8>)>> {
    let decoded = image::load_from_memory_with_format(jpeg, image::ImageFormat::Jpeg)
        .map_err(|error| AdapterError::new("ERR_FRAME", error.to_string()))?
        .to_rgb8();
    if (decoded.width(), decoded.height()) != model.canvas() {
        return Err(AdapterError::new(
            "ERR_FRAME",
            "Wrong compatibility canvas dimensions",
        ));
    }
    let cap = if model == Model::S293 { 5120 } else { 10100 };
    model::regions_for(model)
        .into_iter()
        .map(|(wire, rect, width, height)| {
            let patch =
                image::imageops::crop_imm(&decoded, rect[0], rect[1], rect[2], rect[3]).to_image();
            let patch = image::imageops::resize(
                &patch,
                width,
                height,
                image::imageops::FilterType::Triangle,
            );
            let patch = if model == Model::S293 {
                image::imageops::rotate90(&patch)
            } else {
                image::imageops::rotate180(&patch)
            };
            let mut encoded = Vec::new();
            for quality in [85, 75, 65, 55, 45, 35, 25, 15] {
                encoded.clear();
                image::codecs::jpeg::JpegEncoder::new_with_quality(&mut encoded, quality)
                    .encode_image(&patch)
                    .map_err(|error| AdapterError::new("ERR_FRAME", error.to_string()))?;
                if encoded.len() <= cap {
                    break;
                }
            }
            if encoded.len() > cap {
                return Err(AdapterError::new(
                    "ERR_FRAME",
                    "Compatibility region exceeds firmware JPEG budget",
                ));
            }
            Ok((wire, encoded))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn real_region_encoder_preserves_model_ids_dimensions_rotation_and_byte_budgets() {
        for model in [Model::N4, Model::S293] {
            let (width, height) = model.canvas();
            let mut image = image::RgbImage::new(width, height);
            let rect = model::regions_for(model)[0].1;
            for y in rect[1]..rect[1] + 24 {
                for x in rect[0]..rect[0] + 24 {
                    image.put_pixel(x, y, image::Rgb([240, 20, 20]));
                }
            }
            let mut jpeg = Vec::new();
            image::codecs::jpeg::JpegEncoder::new_with_quality(&mut jpeg, 95)
                .encode_image(&image)
                .unwrap();
            let regions = encode_regions(&jpeg, model).unwrap();
            assert_eq!(regions.len(), if model == Model::N4 { 14 } else { 18 });
            assert_eq!(regions[0].0, if model == Model::N4 { 11 } else { 13 });
            for ((_, bytes), (_, _, w, h)) in regions.iter().zip(model::regions_for(model)) {
                let decoded = image::load_from_memory(bytes).unwrap();
                assert_eq!((decoded.width(), decoded.height()), (w, h));
                assert!(bytes.len() <= if model == Model::S293 { 5120 } else { 10100 });
            }
            let decoded = image::load_from_memory(&regions[0].1).unwrap().to_rgb8();
            let pixel = decoded.get_pixel(
                decoded.width() - 8,
                if model == Model::S293 {
                    8
                } else {
                    decoded.height() - 8
                },
            );
            assert!(pixel[0] > 200 && pixel[1] < 60 && pixel[2] < 60);
        }
    }
}
