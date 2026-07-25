use super::image_processing::{ImageMode, ImageTask};

pub fn task() -> ImageTask {
    ImageTask::new(ImageMode::WebpToImage)
}
