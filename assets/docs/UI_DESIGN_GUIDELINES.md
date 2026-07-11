# UI 与动画统一设计方案

日期：2026-04-17  
执行者：Codex

## 1. 设计目标

本项目的桌面界面统一采用“轻磨砂玻璃”作为新的视觉方向，但本质仍然是效率工具界面，而不是展示型网站。所有设计决策优先满足以下目标：

- 长时间使用时保持清晰、稳定、低干扰。
- 让用户快速理解“当前做什么、处理到哪里、结果在哪里”。
- 通过局部磨砂质感增强层次，而不是依赖重装饰效果。
- 保持 macOS、Windows、Linux 下观感一致，不依赖单一平台特效。

## 2. 总体风格

整体风格定义为：

- 深色导航栏 + 暖色工作区。
- 低饱和配色，强调色集中使用。
- 卡片化分区，局部引入轻磨砂玻璃。
- 动画以状态反馈为主，不做表演型过场。

“轻磨砂玻璃”指：

- 使用半透明暖白背景，而不是纯白实底。
- 配合柔和渐变、亮边框、高光反射和分层阴影。
- 重点容器默认应具备 `backdrop-filter` 风格的模糊感和轻微饱和度增强。
- 不把日志区、长文本区、结果列表全部做成高强度玻璃。

## 3. 色彩系统

推荐将界面色彩收敛为以下角色：

- 导航主底色：深蓝灰，用于侧边栏与工具结构层。
- 内容区底色：暖白到米白渐变，用于主工作区背景。
- 主强调色：金棕，用于主按钮、进度、当前焦点和关键提示。
- 语义色：成功绿、跳过黄、失败红，仅用于统计和执行结果。

使用原则：

- 金棕只用于主操作与关键状态，不用于大面积铺底。
- 语义色只承担结果表达，不参与页面主体背景竞争。
- 常规卡片统一使用暖白半透明渐变，减少白底、灰底、黄底混用。

### 3.1 正式色卡

以下色卡基于当前项目图标提炼，但已经过“产品化降饱和”和“轻磨砂玻璃适配”处理，可作为后续前端变量和视觉规范的统一来源。

<table>
  <tr>
    <th align="left">类别</th>
    <th align="left">名称</th>
    <th align="left">预览</th>
    <th align="left">色值</th>
    <th align="left">建议用途</th>
  </tr>
  <tr>
    <td>品牌蓝</td>
    <td>Brand Navy 900</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#22344B;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#22344B</code></td>
    <td>侧边栏、主标题、核心结构色</td>
  </tr>
  <tr>
    <td>品牌蓝</td>
    <td>Brand Navy 700</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#3D5F80;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#3D5F80</code></td>
    <td>选中态、图标、次级强调</td>
  </tr>
  <tr>
    <td>品牌蓝</td>
    <td>Brand Navy 500</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#78A4CC;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#78A4CC</code></td>
    <td>高光、轻装饰、玻璃边缘反光</td>
  </tr>
  <tr>
    <td>强调色</td>
    <td>Accent Gold 700</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#B16C2F;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#B16C2F</code></td>
    <td>主按钮、进度条、焦点色</td>
  </tr>
  <tr>
    <td>强调色</td>
    <td>Accent Gold 500</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#CF8A36;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#CF8A36</code></td>
    <td>按钮 hover、渐变亮部</td>
  </tr>
  <tr>
    <td>强调色</td>
    <td>Accent Gold 300</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#E8C27A;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#E8C27A</code></td>
    <td>暖色光晕、弱提示背景</td>
  </tr>
  <tr>
    <td>背景</td>
    <td>Canvas</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#F6F1E8;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#F6F1E8</code></td>
    <td>主工作区背景</td>
  </tr>
  <tr>
    <td>背景</td>
    <td>Panel Glass</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#FFFFFF;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>rgba(255,255,255,0.78)</code></td>
    <td>常规玻璃卡片底</td>
  </tr>
  <tr>
    <td>背景</td>
    <td>Panel Warm</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#FFF9F0;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>rgba(255,249,240,0.82)</code></td>
    <td>状态总览、重点说明卡</td>
  </tr>
  <tr>
    <td>背景</td>
    <td>Panel Muted</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#F8F9FC;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>rgba(248,249,252,0.82)</code></td>
    <td>中性统计卡、说明卡</td>
  </tr>
  <tr>
    <td>文字</td>
    <td>Text Primary</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#14213D;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#14213D</code></td>
    <td>正文主文字、标题</td>
  </tr>
  <tr>
    <td>文字</td>
    <td>Text Secondary</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#5D6B80;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#5D6B80</code></td>
    <td>说明文字、辅助信息</td>
  </tr>
  <tr>
    <td>语义色</td>
    <td>Success</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#7DBB9A;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#7DBB9A</code></td>
    <td>成功结果、成功统计</td>
  </tr>
  <tr>
    <td>语义色</td>
    <td>Warning</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#D6A24A;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#D6A24A</code></td>
    <td>跳过、提示、待确认状态</td>
  </tr>
  <tr>
    <td>语义色</td>
    <td>Danger</td>
    <td><div style="width:64px;height:24px;border-radius:6px;background:#D7897F;border:1px solid rgba(0,0,0,0.12);"></div></td>
    <td><code>#D7897F</code></td>
    <td>失败、错误、警示结果</td>
  </tr>
</table>

### 3.2 推荐变量命名

建议前端样式逐步统一到以下命名体系：

```css
:root {
  --brand-navy-900: #22344B;
  --brand-navy-700: #3D5F80;
  --brand-navy-500: #78A4CC;

  --accent-gold-700: #B16C2F;
  --accent-gold-500: #CF8A36;
  --accent-gold-300: #E8C27A;

  --bg-canvas: #F6F1E8;
  --bg-panel: rgba(255, 255, 255, 0.78);
  --bg-panel-warm: rgba(255, 249, 240, 0.82);
  --bg-panel-muted: rgba(248, 249, 252, 0.82);

  --text-primary: #14213D;
  --text-secondary: #5D6B80;

  --success: #7DBB9A;
  --warning: #D6A24A;
  --danger: #D7897F;
}
```

### 3.3 推荐组合关系

- 侧边栏：`Brand Navy 900`
- 侧边栏 hover / active：`Brand Navy 700` 配合透明高亮
- 主按钮：`Accent Gold 700 -> Accent Gold 500`
- 主工作区背景：`Canvas`
- 常规玻璃卡片：`Panel Glass`
- 重点说明卡 / 状态总览：`Panel Warm`
- 中性统计卡：`Panel Muted`
- 主标题与正文：`Text Primary`
- 辅助说明与路径：`Text Secondary`

## 4. 玻璃层级规范

为避免“所有卡片都像玻璃”或“不同页面各自使用不同材质强度”，项目统一定义三档玻璃层级。所有半透明卡片、浮层、说明区都必须优先归入以下等级之一。

### 4.1 `glass-soft`

定位：基础信息层。

视觉特征：

- 半透明暖白底，透明度最低。
- 亮边框，带轻微顶部高光。
- 阴影轻，但应有明确的玻璃层分离感。
- 带低强度模糊和轻微饱和度增强，可近似理解为“轻玻璃 + 高可读性”。

推荐参数方向：

- 背景：`rgba(255, 255, 255, 0.58 ~ 0.72)`
- 边框：`rgba(255, 255, 255, 0.34 ~ 0.46)`
- 模糊：`blur(14px ~ 20px)`
- 饱和度：`saturate(1.08 ~ 1.16)`
- 阴影：多层阴影，外阴影 + 轻微内高光

适用区域：

- 功能页常规卡片
- 日志外层容器
- 结果外层容器
- 文件队列外层容器
- 普通说明卡

使用原则：

- 当信息密度高、文本较多时，优先使用 `glass-soft`。
- 这是项目的默认玻璃层。

### 4.2 `glass-medium`

定位：重点信息层。

视觉特征：

- 半透明暖白底更明显。
- 可加入轻微暖色渐变或冷暖混合渐变。
- 边框和阴影比 `glass-soft` 更清晰，并允许更明显的反光层。
- 允许有更明确的材质感、悬浮感和玻璃折射感。

推荐参数方向：

- 背景：`rgba(255, 248, 238, 0.62 ~ 0.74)` 或同级渐变
- 边框：`rgba(255, 255, 255, 0.42 ~ 0.56)`，必要时可叠加暖色内边界
- 模糊：`blur(20px ~ 26px)`
- 饱和度：`saturate(1.12 ~ 1.20)`
- 阴影：更明显的外阴影 + 顶部内高光 + 底部轻压暗

适用区域：

- 设置页状态总览
- 版本更新卡
- 关于页 Hero 区
- 关于页统计区外层
- Dropzone 拖拽导入区

使用原则：

- 只用于“比普通卡片更重要”的信息区。
- 一个页面中 `glass-medium` 数量应少于 `glass-soft`，避免层级泛化。

### 4.3 `glass-strong`

定位：浮层强调层。

视觉特征：

- 材质感最强。
- 透明度更高，边缘高光、反光和折射感都更明显。
- 阴影更集中，体现短时悬浮和操作优先级。
- 可以配合更明显的背景模糊，但不能影响主界面可读性。

推荐参数方向：

- 背景：`rgba(255, 249, 242, 0.68 ~ 0.80)` 或更强的玻璃渐变
- 边框：`rgba(255, 255, 255, 0.56 ~ 0.68)` + 辅助高光边界
- 模糊：`blur(26px ~ 32px)`
- 饱和度：`saturate(1.18 ~ 1.26)`
- 阴影：强于 `glass-medium` 的多层阴影，但仍保持桌面工具的克制感

适用区域：

- 顶部更新提示条
- Toast / 临时提示
- 弹出层
- 二级浮动面板
- 需要短时间吸引注意力的系统状态提示

使用原则：

- `glass-strong` 不应用于常驻大块内容区域。
- 不应用于日志、结果列表、长说明正文、队列列表。
- 同一屏中应严格控制数量，避免界面发飘。

### 4.4 层级使用顺序

默认优先级如下：

1. 能用 `glass-soft` 就不要上 `glass-medium`
2. 能用 `glass-medium` 就不要上 `glass-strong`
3. `glass-strong` 只给浮层和短时提示，不给基础结构区

### 4.5 禁止事项

以下情况禁止使用高强度玻璃：

- 长日志正文区域
- 大量文件列表
- 错误详情长文本区
- 结果明细滚动区
- 连续多块并列内容区全部使用 `glass-medium` 或 `glass-strong`

原因：

- 会降低阅读效率
- 会削弱信息层级
- 会让桌面工具界面失去稳定感

### 4.6 推荐变量命名

建议后续样式系统增加以下材质层变量：

```css
:root {
  --glass-soft-bg: rgba(255, 255, 255, 0.62);
  --glass-soft-border: rgba(255, 255, 255, 0.42);
  --glass-soft-shadow:
    0 18px 34px rgba(20, 33, 61, 0.10),
    0 2px 0 rgba(255, 255, 255, 0.32) inset,
    0 -1px 0 rgba(20, 33, 61, 0.04) inset;

  --glass-medium-bg: rgba(255, 248, 238, 0.68);
  --glass-medium-border: rgba(255, 255, 255, 0.52);
  --glass-medium-shadow:
    0 22px 42px rgba(20, 33, 61, 0.12),
    0 2px 0 rgba(255, 255, 255, 0.38) inset,
    0 -1px 0 rgba(177, 108, 47, 0.06) inset;

  --glass-strong-bg: rgba(255, 249, 242, 0.74);
  --glass-strong-border: rgba(255, 255, 255, 0.62);
  --glass-strong-shadow:
    0 24px 48px rgba(20, 33, 61, 0.16),
    0 2px 0 rgba(255, 255, 255, 0.44) inset,
    0 -1px 0 rgba(177, 108, 47, 0.08) inset;
}
```

### 4.7 已落地实现说明

当前前端样式已按上述方向完成第一轮增强，重点包括：

- 设置页和关于页已切到正式色卡与玻璃层级。
- `glass-soft / medium / strong` 已同步为前端 CSS token。
- 玻璃层当前默认包含：
  - 半透明渐变背景
  - `backdrop-filter` 模糊
  - `saturate(...)` 轻微饱和度增强
  - 外阴影 + 内高光
- 设置页与关于页的主要玻璃卡片已加入顶部反光和左上角高光层。

后续功能页改造也应沿用同一实现方式，不再新增新的材质分支。

## 5. 字号与层级

全项目统一标题层级，不允许页面内部单独放大同级标题：

- 页面标题：`h2`，如“格式化”“设置”“关于”。
- 区块标题：`h3 / h4`，如“输出与执行”“版本更新”“累计处理概览”。
- 说明标签：`eyebrow`，用于区块分类和辅助识别。
- 正文说明：默认 `14px`。
- 次级说明、路径、辅助信息：`12px-13px`。

规则：

- 同层级标题必须使用统一字号、行高、字重。
- 说明文字应低于标题一个层级，但保证可读性。
- 路径、日志类次级文字允许更轻，但不能低到影响识别。

## 6. 卡片系统

卡片分为三类：

### 5.1 主功能卡

用于输入源、输出与执行、文件队列、日志、结果等高频操作区域。

- 背景：暖白轻磨砂。
- 边框：低对比细边框。
- 阴影：柔和，强调容器边界，不制造漂浮感。
- 重点保证可读性，不做过强玻璃和颜色铺底。

### 5.2 信息卡

用于设置状态、版本更新、日志位置、说明模块。

- 可使用更明显的玻璃质感。
- 可带轻微暖色渐变，增强产品感。
- 适合统一 hover 抬升与阴影变化。

### 5.3 数据卡

用于统计仪表盘、总数卡、成功/跳过/失败卡。

- 保持统一圆角、边框和内边距。
- 总数卡使用中性浅底。
- 成功/跳过/失败卡使用低饱和语义色浅底，不用纯色块。

## 7. 动画规范

动画统一定位为“轻反馈动画”，用于建立层级、反馈操作和解释状态变化。

推荐保留的动画：

- 页面进入：轻微上移淡入。
- 分区进入：短距离错峰进入。
- 卡片 hover：轻微上浮。
- 按钮点击：短促按压反馈。
- 进度条：平滑线性展开。
- 数字变化：递增或淡切。
- 状态文案切换：淡入淡出。

推荐时长：

- 页面与区块进入：`240ms-320ms`
- 卡片 hover：`180ms-220ms`
- 按钮按压：`120ms-160ms`
- 状态文案切换：`160ms-220ms`
- 数据统计动画：`800ms-1600ms`

限制：

- 只优先动画 `opacity`、`transform`、`box-shadow`。
- 避免大位移、强弹簧、循环闪烁和复杂旋转。
- 必须支持 `prefers-reduced-motion` 降级。

## 8. 页面应用范围

### 7.1 功能执行页

目标：清晰、稳定、可扫描。

规范：

- 顶部展示功能名与一句话用途说明。
- 中部固定为输入源、执行配置、文件队列。
- 下部固定为日志与结果。
- 日志区、结果区、路径区保持高可读性，只使用轻玻璃，不使用强模糊。

### 7.2 设置页

目标：稳定、精简、可配置。

规范：

- 仅放用户可修改配置与可直接调用的管理工具。
- 状态总览、版本更新、偏好、日志工具、历史记录作为固定模块。
- 适合使用较明显但克制的磨砂玻璃卡片体系。

### 7.3 关于页

目标：完整、统一、略带展示感。

规范：

- 顶部优先展示累计统计仪表盘。
- 中部展示功能范围、执行规则、输入方式等摘要卡。
- 下部展示输出规则、日志说明等结构化说明。
- 关于页允许比设置页更强调材质和分层，但仍以阅读清晰为前提。

## 9. 组件应用原则

以下区域适合强化玻璃感：

- 顶部更新提示条
- 设置页状态总览
- 关于页统计区
- 拖拽导入区
- 浮层、提示层、弹出面板

以下区域必须控制玻璃强度：

- 处理日志
- 结果列表
- 文件队列
- 长文本说明区
- 路径与错误详情展示区

原因：这些区域信息密度高，过强玻璃会降低长时间阅读体验。

## 10. 响应式与窗口缩放

桌面端布局优先保证结构稳定，不因窗口缩放导致层级混乱。

规范：

- 左侧导航为稳定结构层，桌面宽度下不应随内容一起滚动。
- 右侧工作区独立滚动。
- 窗口缩窄时优先减少横向密度，不打乱页面骨架。
- 统计卡、设置卡等在中等宽度下允许重排，但不应在正常桌面宽度下碎裂成过多行。

## 11. 后续演进原则

后续 UI 修改必须遵循以下规则：

- 优先统一，不新增孤立风格。
- 先收敛颜色、层级、间距，再增加装饰效果。
- 所有新增模块都必须归入“主功能卡 / 信息卡 / 数据卡”之一。
- 所有新增动画都必须说明用途，不能只为“更炫”。
- 在功能页中始终优先信息清晰度，不以玻璃效果换取可读性。

一句话总结本项目的 UI 方向：

> 以深色导航、暖色内容区为基础，局部引入轻磨砂玻璃质感和轻反馈动画的桌面效率工具界面。
