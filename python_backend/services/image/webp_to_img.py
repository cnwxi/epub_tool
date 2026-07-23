from __future__ import annotations

from pathlib import Path

from python_backend.services.image.image_processing import process_images
from python_backend.services.utils.log import logwriter


logger = logwriter()


def run(
    input_file: str, output_dir: str | None, *, options: dict[str, object]
) -> int | str:
    target_dir = Path(output_dir) if output_dir else Path(input_file).parent
    output = target_dir / f"{Path(input_file).stem}_webp_to_img.epub"
    logger.write(f"正在尝试转换 EPUB 中的 WebP 图片: {input_file}")
    return process_images(
        input_file,
        str(output),
        mode="webp_to_image",
        quality=int(options.get("quality", 82)),
        png_quantize=bool(options.get("png_quantize", False)),
        logger=logger,
    )
