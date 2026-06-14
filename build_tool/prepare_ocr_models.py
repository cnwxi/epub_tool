from __future__ import annotations

import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = "PP-OCRv5_server_rec"
MODEL_URL = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
    "official_inference_model/paddle3.0.0/PP-OCRv5_server_rec_infer.tar"
)
MODEL_DIR = REPO_ROOT / "src-tauri" / "bundle-resources" / "ocr-models" / MODEL_NAME
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
