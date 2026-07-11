from __future__ import annotations

from pathlib import Path

try:
    from scripts.ocr_model_config import onnx_model_dir, resolve_ocr_model_name
except ModuleNotFoundError:
    from ocr_model_config import onnx_model_dir, resolve_ocr_model_name


def verify_ocr_onnx_models() -> Path:
    model_name = resolve_ocr_model_name()
    model_dir = onnx_model_dir(model_name)
    model_file = model_dir / "inference.onnx"
    config_file = model_dir / "inference.yml"
    missing = [path for path in (model_file, config_file) if not path.is_file()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise SystemExit(
            "ONNX OCR 模型资源不完整: "
            + missing_text
            + "。默认构建只校验已提交的 ONNX 模型；请恢复提交的模型文件，"
            "或按 assets/docs/BUILD_AND_BUNDLE.md 的维护流程重新生成后提交。"
        )

    try:
        import yaml
        import onnxruntime as ort
    except Exception as exc:
        raise SystemExit("ONNX OCR 模型校验需要安装 requirements/requirements.txt 运行时依赖。") from exc

    config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    postprocess_name = (config.get("PostProcess") or {}).get("name")
    character_dict = (config.get("PostProcess") or {}).get("character_dict") or []
    if postprocess_name != "CTCLabelDecode":
        raise SystemExit(f"ONNX OCR 模型不是 CTCLabelDecode，当前运行时无法解码: {postprocess_name}")
    if not character_dict:
        raise SystemExit(f"ONNX OCR 配置缺少 character_dict: {config_file}")

    session_options = ort.SessionOptions()
    session_options.log_severity_level = 3
    session = ort.InferenceSession(
        str(model_file),
        sess_options=session_options,
        providers=["CPUExecutionProvider"],
    )
    outputs = session.get_outputs()
    if len(outputs) != 1:
        raise SystemExit(f"ONNX OCR 模型输出数量不是 1，当前运行时无法解码: {len(outputs)}")
    output_shape = outputs[0].shape
    expected_vocab_size = len(character_dict) + 2
    if len(output_shape) != 3 or output_shape[-1] != expected_vocab_size:
        raise SystemExit(
            "ONNX OCR 模型输出形状与 CTC 字典不匹配: "
            f"shape={output_shape}, expected_vocab={expected_vocab_size}"
        )

    print(
        "ONNX OCR model verified: "
        f"{model_dir} output={outputs[0].name} shape={output_shape}"
    )
    return model_dir


def main() -> int:
    verify_ocr_onnx_models()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
