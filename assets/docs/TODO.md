# TODO

记录日期：2026-06-17  
执行者：Codex

## 字体处理：同一 font-family 下精确选择实际字体文件

### 当前现状

- `font_encrypt` 和 `font_decrypt` 已作为正式任务接入 sidecar、CLI 与前端。
- 字体扫描已覆盖 `.ttf`、`.otf`、`.woff`、`.woff2`，并已有 WOFF2 回归。
- CSS 解析已不是纯正则路径，当前使用 `tinycss2` 和 `cssselect2` 处理常见 EPUB 字体命中场景，包括选择器匹配、`@import`、`@media`、`@supports`、`@layer`、`@scope`、inline style、`!important`、继承、CSS custom property / `var()` 等。
- 字体加密流程会先生成 `font_file -> 字符集合`，再改写字体 cmap 并回写正文。
- 字体解密流程会复用同一套字体命中逻辑生成 `font_file -> 混淆字符集合`，再渲染字形图片并交给本地 ONNX OCR 识别后回写正文。

### 关键缺口

- 当前 `@font-face` 仍主要被压缩成 `font-family -> 单个字体文件` 的粗粒度映射。
- 同一 `font-family` 下存在多个 `@font-face` 文件时，正文字符还不能按实际 CSS 字体选择规则稳定分流。
- 尚未将 `@font-face` 的 `font-weight`、`font-style`、`unicode-range` 和 CSS 来源顺序保存为结构化候选信息。
- 当前字符收集仍偏 tag 级别：先得到一个有效字体文件，再把该 tag 的直接文本加入该字体映射；这会影响 `unicode-range` 这类字符级字体 fallback 场景。

### 待办

- 建立结构化 `@font-face` 候选记录，至少包含：
  - normalized `font-family`
  - resolved `src` 字体文件路径
  - `font-weight`
  - `font-style`
  - `unicode-range`
  - CSS 来源顺序 / 后声明优先信息
- 实现字体候选选择逻辑：
  - 先匹配 `font-family`
  - 再判断 `unicode-range` 是否覆盖当前字符；未声明 `unicode-range` 视为覆盖全部
  - 再按 `font-style` 匹配度选择
  - 再按 `font-weight` 精确或最近距离选择
  - 仍并列时按 CSS cascade / 后声明优先保持稳定
- 支持常见 `font-weight`：
  - `normal` = 400
  - `bold` = 700
  - 数字权重，如 100、400、500、700、900
  - CSS Fonts Level 4 区间写法，如 `400 700`，至少不能破坏现有 family 命中行为
- 支持常见 `font-style`：
  - `normal`
  - `italic`
  - `oblique`
- 支持常见 `unicode-range`：
  - `U+4E00-9FFF`
  - `U+00??`
  - `U+0041`
  - 多段逗号分隔
- 调整正文映射为字符级字体选择，避免 `unicode-range` 不覆盖的字体文件被错误污染。

### 定向回归

- 同一 family 下 normal 与 bold 分别指向不同字体，bold 文本只映射 bold 字体。
- 同一 family 下 normal 与 italic 分别指向不同字体，italic 文本只映射 italic 字体。
- 同一 family 下 latin 与 CJK `unicode-range` 分别指向不同字体，英文和中文字符分别映射对应字体。
- `unicode-range` 不覆盖字符时，不应错误污染该字体映射，应回退到其他可覆盖候选或现有安全 fallback。
- 保证既有 WOFF2 与 `@import url(...) supports(...)` 回归继续通过。

### 范围边界

- 不追求实现完整浏览器级 CSS 引擎。
- EPUB 字体混淆和 OCR 的核心目标是判断“某个字符实际会使用哪个内嵌字体文件”，因此优先覆盖会影响字体文件选择的 CSS 子集。
- 布局、动画、grid、完整视觉排版等不直接影响字体文件选择的 CSS 特性暂不纳入目标。
- OCR 本身不需要完整 CSS 布局，但 OCR 输入字符集依赖字体映射准确性；字体映射错误会导致 OCR 渲染错字体、漏字符或误替换。
