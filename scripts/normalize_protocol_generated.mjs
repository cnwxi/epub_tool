import { readFile, writeFile } from "node:fs/promises";

const generatedFile = new URL(
  "../frontend/src/generated/epub_tool/v1/engine_pb.ts",
  import.meta.url,
);
const source = await readFile(generatedFile, "utf8");
const normalized = source.replace(/\n+$/, "\n");

if (normalized !== source) {
  await writeFile(generatedFile, normalized, "utf8");
}
