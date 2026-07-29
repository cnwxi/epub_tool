import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const modelName = process.env.EPUB_TOOL_OCR_MODEL_NAME || "PP-OCRv6_small_rec";
const modelDir = resolve(scriptDir, "..", "src-tauri", "bundle-resources", "ocr-models", `${modelName}_onnx`);
const requiredFiles = ["inference.onnx", "inference.yml"];
const missingFiles = requiredFiles.filter((file) => !existsSync(resolve(modelDir, file)));

if (missingFiles.length > 0) {
  throw new Error(`ONNX OCR 模型资源不完整: ${missingFiles.map((file) => resolve(modelDir, file)).join(", ")}`);
}

console.log(`ONNX OCR model resources verified: ${modelDir}`);
