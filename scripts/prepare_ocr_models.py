from __future__ import annotations

import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

try:
    from scripts.ocr_model_config import (
        ocr_model_dir,
        ocr_model_url,
        resolve_ocr_model_name,
    )
except ModuleNotFoundError:
    from ocr_model_config import (
        ocr_model_dir,
        ocr_model_url,
        resolve_ocr_model_name,
    )

MODEL_NAME = resolve_ocr_model_name()
MODEL_URL = ocr_model_url(MODEL_NAME)
MODEL_DIR = ocr_model_dir(MODEL_NAME)
REQUIRED_FILES = ("inference.yml", "inference.pdiparams", "inference.json")


def model_is_ready() -> bool:
    return all((MODEL_DIR / file_name).is_file() for file_name in REQUIRED_FILES)


def copy_extracted_model(extracted_root: Path) -> None:
    source_dir = extracted_root / f"{MODEL_NAME}_infer"
    if not source_dir.is_dir():
        raise SystemExit(f"OCR model archive did not contain expected directory: {source_dir}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for file_name in REQUIRED_FILES:
        source_file = source_dir / file_name
        if not source_file.is_file():
            raise SystemExit(f"OCR model archive missing required file: {source_file}")
        shutil.copy2(source_file, MODEL_DIR / file_name)


def download_model() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / f"{MODEL_NAME}_infer.tar"
        print(f"Downloading OCR model {MODEL_NAME}: {MODEL_URL}")
        urllib.request.urlretrieve(MODEL_URL, archive_path)

        extract_dir = temp_root / "extract"
        extract_dir.mkdir()
        with tarfile.open(archive_path) as archive:
            archive.extractall(extract_dir)

        copy_extracted_model(extract_dir)


def prepare_ocr_models() -> Path:
    if model_is_ready():
        print(f"OCR model reused: {MODEL_DIR}")
        return MODEL_DIR

    download_model()
    if not model_is_ready():
        raise SystemExit(f"OCR model preparation failed: {MODEL_DIR}")

    print(f"OCR model prepared: {MODEL_DIR}")
    return MODEL_DIR


def main() -> int:
    prepare_ocr_models()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
