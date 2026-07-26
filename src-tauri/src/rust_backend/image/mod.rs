pub(crate) mod image_compress;
pub(crate) mod image_to_webp;
mod image_processing;
pub(crate) mod webp_to_img;
mod replace_cover;

pub(crate) use image_processing::{ImageProcessOutcome, ImageTask};
pub(crate) use replace_cover::ReplaceCoverTask;
