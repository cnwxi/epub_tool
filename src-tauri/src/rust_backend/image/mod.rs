pub(crate) mod image_compress;
mod image_processing;
pub(crate) mod image_to_webp;
mod replace_cover;
pub(crate) mod webp_to_img;

pub(crate) use image_processing::{ImageProcessOutcome, ImageTask};
pub(crate) use replace_cover::ReplaceCoverTask;
