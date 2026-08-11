# TODO

记录日期：2026-06-17  
执行者：Codex

## 字体处理：同一 font-family 下精确选择实际字体文件

### 当前现状

- `encrypt_font` 和 `decrypt_font` 已作为正式任务接入 sidecar、CLI 与前端。
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

---

## Rust 字体任务：统一复杂 CSS 字体决策内核

记录日期：2026-08-11

来源：2026-07-29 `main_rust` 分支的字体加解密方案讨论

### 问题与目标

Rust 字体任务中已有 `font_cascade.rs`、`font_rule_index.rs`、`font_selectors.rs`、`font_values.rs` 等复杂 CSS 解析候选模块，但它们此前仅由单元测试使用，未接入实际的字体加密、字体解密路径。生产路径仍依赖简化的字体选择规则，导致复杂选择器和 CSS 层叠结果无法稳定影响实际处理目标。

目标是建立唯一的字体决策内核，先把每个 XHTML 文本节点解析为其有效内嵌字体，再由字体加密和字体解密共同消费该结果：

```text
XHTML 节点 / 文本字符 -> 计算后的 font-family -> 实际内嵌字体文件
```

不再让加密与解密各自维护一套 CSS 字体匹配逻辑。

### 设计原则

- 不将 Python 当前实现视为 CSS 语义的唯一黄金标准；Python 与 Rust 的既有简化行为都可能遗漏复杂 CSS 规则。
- 建立独立的字体 CSS 行为规范和回归用例，明确每个 XHTML/CSS 输入应得到的有效 `font-family` 与字体文件。
- 加密、解密共用“元素/字符 -> 有效字体”的解析结果，避免处理范围漂移。
- 同时提供 Stylo 与 `lightningcss + selectors + 字体专用 cascade`，允许用户按任务选择字体决策引擎，不将产品限制在单一路线。
- 默认“自动”模式优先使用 Stylo；Stylo 无法运行时，才按单本 EPUB 切换到字体专用 cascade。
- 用户强制选择某个引擎时必须严格执行该选择；引擎失败时直接报告错误，不得自动切换到另一条路线。
- 自动模式的兜底必须按整本 EPUB 切换，并记录触发原因；禁止在同一本 EPUB 内按节点静默混用两套引擎结果。
- 两套引擎都无法可靠处理时，必须显式报不支持或拒绝处理，禁止继续按旧简化规则降级。
- 最终验收以文本节点字体判定、处理字符集合以及改写后的 XHTML/CSS/OPF 为准，不能只验证任务是否成功完成。

### 实施步骤

1. 定义统一的字体决策接口，使字体加密、字体解密和 `font_targets` 不直接依赖具体 CSS 引擎。
2. 在统一任务协议中增加 `fontCssEngine` 选项，并支持以下值：
   - `auto`：默认值，优先 Stylo；出现允许兜底的引擎错误时，整本 EPUB 改用字体专用 cascade；
   - `stylo`：仅使用 Stylo，失败时任务失败；
   - `cascade`：仅使用 `lightningcss + selectors + 字体专用 cascade`，失败时任务失败。
3. 在字体加密和字体解密页面提供一致的引擎选择控件，保存用户选择，并在任务日志、结果与历史记录中展示实际使用的引擎。
4. 实现 Stylo 引擎：
   - 为 EPUB XHTML DOM 实现元素、属性和树遍历适配层；
   - 加载内联与外链 CSS，并完成 EPUB 资源路径解析；
   - 将 Stylo 计算样式映射为字体任务需要的 `font-family`、`font-style`、`font-weight` 等结果；
   - 将计算结果映射到实际 `@font-face` 与内嵌字体文件。
5. 实现 `lightningcss + selectors + 字体专用 cascade` 引擎，并将 `font_rule_index` 接入 XHTML 节点遍历：
   - 属性选择器；
   - `:is()`；
   - `:nth-child()`、`:nth-of-type()`；
   - 选择器列表；
   - 内联样式；
   - 继承；
   - CSS custom property / `var()`；
   - `!important`。
6. 对字体专用 cascade 尚未覆盖但会影响字体决策的语义逐项实现和验证：
   - `@media`；
   - `@layer`；
   - `@scope`；
   - `revert`、`revert-layer`。
7. 定义 `auto` 模式的兜底触发条件：仅在 Stylo 初始化失败、当前构建不支持 Stylo、文档解析失败或出现已识别的引擎错误时，才允许整本 EPUB 切换到字体专用 cascade；日志与任务结果必须标明原因。
8. 扩展共享的 `@font-face` 候选选择，使其能与本文件前一节的 `font-weight`、`font-style`、`unicode-range` 和来源顺序规则共同工作，并将文本收集推进到字符级映射。
9. 当 Stylo 和字体专用 cascade 覆盖既有行为后，删除 `StrictSelector` 等旧的简化决策路径，避免第三套选择逻辑继续存在。

### 回归与验收

- 建立独立 CSS/XHTML fixture 集，覆盖优先级、继承、变量、伪类、属性选择器、`@media`、`@layer` 和 `@scope`。
- 对每个 fixture 断言：文本节点的有效字体、对应内嵌字体文件、加密/解密处理字符集合，以及 XHTML/CSS/OPF 的改写结果。
- 使用真实 EPUB 样本验证：生成 EPUB 可打开、字体显示正常、加密与解密对相同文本节点选择同一字体。
- OCR 低置信度时保持保守失败和人工复核产物，禁止猜测替换字符。
- 在 macOS、Windows、Linux 上运行相同 fixture，确保同一版本程序得到一致结果。
- 分别验证 `auto`、`stylo`、`cascade` 三种任务选项及其持久化、协议传递、日志和历史记录展示。
- 验证 `auto` 模式正常任务优先命中 Stylo，并为每一种兜底触发条件建立定向测试。
- 验证强制 `stylo` 或 `cascade` 模式失败时不会静默切换引擎。
- 对同一 fixture 分别运行 Stylo 与字体专用 cascade，记录字体决策差异；任一引擎的结果超出其已声明支持范围时必须失败，不能伪装为等价成功。

### 技术路线决策

提供三种运行模式：

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `auto` | 优先 Stylo，符合兜底条件时按单本 EPUB 切换到字体专用 cascade | 默认模式，兼顾 CSS 完整度与任务可用性 |
| `stylo` | 始终使用 Stylo，不自动切换 | 需要更接近浏览器 CSS 语义，并要求结果可复现 |
| `cascade` | 始终使用字体专用 cascade，不尝试 Stylo | 需要较轻运行路径、诊断差异或规避 Stylo 特定问题 |

Stylo 引擎采用：

```text
Stylo + EPUB XHTML DOM 适配层 + 统一字体决策接口
```

- Stylo 负责 `auto` 模式的优先路径，以及 `stylo` 模式的唯一 CSS 选择器匹配、级联和计算样式实现。
- EPUB XHTML DOM 适配层负责向 Stylo 暴露元素、属性、树关系和样式资源。
- 统一字体决策接口负责将 Stylo 结果转换为字体加密、字体解密和 `font_targets` 共用的字体文件映射。
- 默认路径必须验证依赖规模、编译时间、Worker 启动时间、运行内存、三平台构建和安装包增量。

字体专用 cascade 引擎采用：

```text
lightningcss + selectors + 字体专用 cascade
```

- `lightningcss` 负责 CSS 解析与 AST 遍历。
- `selectors` 负责复杂选择器匹配，需复用同一 EPUB XHTML DOM 数据模型。
- 字体专用 cascade 只计算影响字体文件选择的属性，并明确声明支持范围。
- 在 `auto` 模式中，它只在 Stylo 满足明确兜底条件时启用；在 `cascade` 模式中，它也可以由用户直接选择并独立运行。
- 无论使用默认还是兜底引擎，加密与解密都必须共用同一次字体决策结果。

系统 WebView 不作为字体处理的唯一决策引擎：macOS、Windows、Linux 的 WebKit / WebView2 / WebKitGTK 内核及版本不同，无法保证同一 EPUB 在各平台产生一致的字体决策；同时它也会破坏现有无界面独立 Worker 的运行模型。

### 范围边界

- 当前目标是准确判定 EPUB 文本实际应使用的内嵌字体文件，不追求完整视觉渲染。
- 不直接影响字体选择的布局、动画、grid 等 CSS 特性不纳入首期实现。
- 不承诺覆盖所有现实世界 CSS 或所有 EPUB 阅读器的私有行为；支持范围必须由 fixture 和错误策略显式定义。
- 以上为待实施方案；截至本记录创建时，复杂 CSS 决策内核接入、Stylo 引入和完整回归体系均未在本 TODO 中标记为已完成。
