from __future__ import annotations

from pathlib import Path

from python_backend.services.image_processing import process_images
from python_backend.services.log import logwriter


logger = logwriter()


def run(input_file: str, output_dir: str | None, *, options: dict[str, object]) -> int:
    target_dir = Path(output_dir) if output_dir else Path(input_file).parent
    output = target_dir / f"{Path(input_file).stem}_image_compress.epub"
    jpeg_quality = int(options.get("jpeg_quality", 82))
    webp_quality = int(options.get("webp_quality", 82))
    return process_images(
        input_file,
        str(output),
        mode="compress",
        quality=jpeg_quality,
        webp_quality=webp_quality,
        png_to_jpg=bool(options.get("png_to_jpg", False)),
        logger=logger,
    )
