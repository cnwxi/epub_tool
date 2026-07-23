from __future__ import annotations

from pathlib import Path

from python_backend.services.image.image_processing import process_images
from python_backend.services.utils.log import logwriter


logger = logwriter()


def run(input_file: str, output_dir: str | None, *, options: dict[str, object]) -> int:
    target_dir = Path(output_dir) if output_dir else Path(input_file).parent
    output = target_dir / f"{Path(input_file).stem}_image_to_webp.epub"
    logger.write("WebP 在部分 EPUB 2 或旧版阅读器中可能不受支持。")
    return process_images(
        input_file,
        str(output),
        mode="webp",
        quality=int(options.get("quality", 82)),
        logger=logger,
    )
