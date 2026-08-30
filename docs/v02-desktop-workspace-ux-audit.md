# V02-51A 桌面工作台视觉与交互系统：只读审计

- 任务：Issue #50 / `[0.2.0][V02-51A]` Audit the desktop workspace visual and interaction system
- 基线：`2613d978f64a2faee0282c6250501a453c13df0f`（`origin/master`，分支 `codex/v02-51a-desktop-workspace-ux-audit`）
- 性质：L2 UI 只读审计与设计 token / 页面模板，不实现代码，不改现有 UI
- 范围：全局壳、工作台、五条高频路径的视觉与交互合同
- 明确不做：Tauri/Electron 选型（V02-53）；供应商平台内部组件（以本基线描述，细节交给已冻结的 V02-11A）；文案删减清单（V02-50）

本文约束桌面窗口下的信息密度、层级、面板、状态和动效。不覆盖移动端为首要目标。行号相对本基线，**不引用** `codex/v02-11a-provider-settings-ui` 的供应商拆分。

---

## 1. 当前全局结构

### 1.1 应用壳

| 层 | 文件 | 现状 |
| --- | --- | --- |
| 根 | `app/layout.tsx` | `lang="zh-CN"`，无字体链路；CSS 声明了 Noto Sans/Serif SC（`globals.css:14-15`）但未加载，实际回退雅黑/宋体 |
| Query | `app/providers.tsx` | React Query，不是页面 |
| 壳 | `components/shell.tsx` | `AppShell` 只有 `app-main`。`GlobalNav`：项目 / 帮助 / 设置 |
| 废弃轨 | `globals.css:28-39` `.rail` | 74px 左侧轨道仍在样式里，**没有任何 TSX 挂载** |
| 首页 | `app/page.tsx` | sticky `topbar` 74px + `dashboard-grid` 主栏/328px 侧栏 |
| 工作台 | `project-workspace.tsx` | `workspace-topbar` + `workspace-layout`（侧栏宽度 CSS 变量）+ `workspace-canvas` + 底栏 `QueueDock` |
| 设置 | `app/settings/page.tsx` | 同一 `topbar` + `settings-board` 主栏/粘滞诊断侧栏 |
| 帮助 | `app/help/page.tsx` | 独立长页，不进工作台网格 |

全局导航只有三入口。导演、视觉分镜、场景资产包都还没有顶栏位置。工作台把「流程编排」「项目设置」放进左侧步骤列表下方（`workspace-chrome.tsx:78-79`）。

### 1.2 工作台骨架（桌面）

```
┌ topbar 58–74px：返回项目 · 项目名 · 工作流 · 项目设置 ─┐
│ ┌ 侧栏 188–360px ┐┌ 主栏 canvas ──────────────────┐ │
│ │ 步骤 01–07     ││ 段标题 + 内容                   │ │
│ │ FL 流程 / ST   ││ 分镜时另有检查器列               │ │
│ └────────────────┘└────────────────────────────────┘ │
└ queue-dock 48px 固定底栏（可 localStorage 隐藏）     ┘
```

- 侧栏宽度：`mangaflow.project-sidebar-width`，188–360，默认 214（`project-workspace.tsx:56-59`，`globals.css:187`）。
- 分镜检查器宽度：`mangaflow.storyboard-inspector-width`，320–620，默认 390。
- 底栏隐藏：`mangaflow.queue-dock-hidden`。
- 主栏右侧「检查器」不是全局槽：只在分镜工作台出现。生成页把门禁/参考/操作堆在主栏流里。资产页是双列 workbench，没有停靠检查器。
- `workspace-right`（`globals.css:350`）样式还在，工作台当前不用它做第三列。

### 1.3 断点（互相对打）

| 查询 | 作用 | 问题 |
| --- | --- | --- |
| `max-width: 1180px` | dashboard 单列、工作台侧栏默认 188 | 1280 桌面仍双列，但 1180–1279 首页已单列 |
| `max-width: 1023.98px` | 分镜检查器改堆叠 | 与 900–1279 主适配面冲突 |
| `max-width: 900px` | 设置板单列、部分网格 | 设置在 900–1279 仍是主栏+粘滞侧栏 |
| `max-width: 760px` | 工作台侧栏改抽屉、顶栏压缩、queue 统计隐藏 | 真正的「窄桌面」被推到平板宽度 |
| `min-width: 761px` | 侧栏 sticky 高 | 与 760 抽屉交界 |
| `min-width: 1440px` | 分镜页条改左侧 | 1280–1439 没有这条 |
| `max-width: 1439px` | 风格管线两列 | 与设置无关 |

结论：桌面主面应按 **≥1280 / 900–1279** 设计，而不是继续以 760 为工作台折叠点。900–1279 才是 13–14 寸窗口。

### 1.4 两套密度在打架

后期「Stabilization baseline」（`globals.css:1280-1288,1383-1388`）用 `!important` 把 `body` 14px、控件 12px/40px 最小高度盖上去。更早的组件规则仍写 5–9px 标签、25px 按钮（资产、绑定流、provider 基线工具栏）。结果是：

- 可读性补丁生效时，字号突然跳到 12/14，但字距 `letter-spacing: .14em`、英文小标、三列 7px 说明还在，版面被撑破。
- 部分选择器（`.asset-actions button { height: 25px }`）被 `min-height: 40px !important` 覆盖，按钮变高但网格间隙仍按 25px 排。
- `small { font-size: 12px !important }` 让卡片计数、辅助说明全部同级，层级塌掉。

V02-51 必须先定 **一套密度**，再让组件删除与 token 冲突的魔法数字。不要再叠第四层 `!important`。

---

## 2. 五条高频路径（当前摩擦）

### 2.1 供应商设置（`/settings`）

基线结构：状态条五格 → `ProviderManagement` 大卡 → Vertex 四按钮卡 → 运行设置；右侧诊断 + 存储。

摩擦（只描述壳，不重做 V02-11A 内部）：

- `.settings-board` 到 900px 才单列（`:821,:1159`）。900–1279 主栏被粘滞侧栏挤到约 560–760px。
- 供应商工具栏三列、创建表单四列，到 760px 才折（基线 `:833,:841,:1166`）。这是壳层网格问题，实现供应商内部拆分时不要回头改全站 token。
- 首页侧栏仍是 `VERTEX AI / LEGACY` 卡（`page.tsx:209-223`），与设置页双入口。V02-10 删除产品级 Vertex 首选后，本系统应把首页侧栏改成「连接健康聚合」，而不是再画一张专属卡。
- 诊断侧栏 `max-height: calc(100vh - 108px)` 自滚，主栏也滚，形成套娃。

本审计 **禁止** 规定供应商卡片内部的搜索/筛选/JSON 控件。那些以 V02-11A 交付为准。本系统只提供：页面模板（主+侧）、卡片外壳 token、状态色、焦点环。

### 2.2 资产（工作台 `assets`）

`AssetsSection` + 子导航人物/服装/风格/参考。双列 `asset-workbench`，卡片 74px 缩略图，绑定链五段。

摩擦：

- 标签 5–8px（`:284-297,:308-310`），后期 !important 把字抬高后绑定链挤爆。
- 没有检查器槽：选中素材的动作、生成管线、记录列表挤在同一列滚动。
- 生成结果用 `CandidateArtwork` + lightbox，缩略图路径已走 `publicUrl` → `/thumbnail/640`（`api.ts:761-767`）。原则对，但网格 `eager` 仅首张，其余默认懒加载依赖 Next/Image。
- 场景资产（V02-21）与角色模型包（V02-23）尚未存在。本系统预留「资产工作区模板」，不能假定第三列已有。

### 2.3 分镜（工作台 `storyboard`）

联系表 + 检查器，详见 V02-31A。对 **本系统** 的含义：

- 这是唯一接近「主栏 + 检查器」的页面，应成为画布类模板的原型。
- 900–1279 把检查器砸到下面（1024 断点），与「桌面主面保留检查器」目标相反。
- 视觉画布（V02-31）未实现。本系统只规定画布页的槽：工具栏、画布、检查器、底栏，不规定 handle 形状。

### 2.4 生成结果（工作台 `generate` + `library`）

生成页：页选择、门禁、参考、操作条、`candidate-grid` 三列（`:651`）、lightbox。素材库：cursor 分页，每页最多 30 批次（`library-section.tsx:100`）。

摩擦：

- 候选卡无虚拟化。一页多次抽卡后 DOM 线性增长。
- 素材库有分页无虚拟化；5 列 90px 格（`:743`）在宽屏合理，900px 改 2 列。
- lightbox 缩放按钮 50%–250%，无键盘 `+`/`-`/`Esc` 绑定（关闭靠点击背景）。`role="dialog"` 无焦点陷阱。
- 底栏 queue-dock 与生成页「任务」信息重复；生成页本身不显示完整失败列表。
- 「历史候选」在生成页按批次切换，不是时间轴面板。导演对比（V02-43）还没有槽。

### 2.5 导演未来入口（不存在）

路线图 V02-41：画布 + 选区 + 对话/命令历史 + 变更预览。今日：

- 无导航项、无路由、无命令栏。
- 最接近的是分镜检查器与工作流工作室。
- 本系统必须 **预留** 而不实现：工作台步骤在「分页与分镜」和「单页生成」之间或作为画布上的模式切换；右侧「历史/命令」槽与检查器互斥或标签化。

依赖 V02-40 命令 schema 与 V02-31 画布选中态。未完成前不要画假命令输入框。

---

## 3. 视觉库存：密度、间距、字体、颜色、层级

### 3.1 Token 现状

`:root`（`globals.css:3-16`）只有：

`--ink --ink-soft --paper --paper-deep --white --line --line-dark --vermillion --green --amber --sans --serif`

缺失且已被代码引用或需要的：

- `--muted`：`production-diagnostics` 用了（`:1290`）但 **未定义**。
- `--danger`：失败态有时 vermillion，有时 `#b23c25` / `#9b4b39` / `#c95b48` / `#8e4133`。
- `--warning-bg` / `--success-bg`：保存条、stale banner、queue 各自手写。
- 间距、圆角、阴影、z-index、字体级谱全部是魔法数字。
- `--mono` 在供应商连接 URL 用了（基线 provider CSS），`:root` 未定义（浏览器忽略，回退默认等宽）。

### 3.2 字体级谱（建议取代 5–32px 散点）

| Token | 建议 | 用途 |
| --- | --- | --- |
| `--text-display` | 32–40 / serif | 首页 h1、设置英雄（已接近） |
| `--text-title` | 20–24 / serif | 段标题 `canvas-header h2` |
| `--text-body` | 14 / sans | 主说明、空状态正文 |
| `--text-ui` | 13 / sans | 按钮、输入、导航 |
| `--text-meta` | 12 / sans | 辅助、时间、计数 |
| `--text-micro` | **禁止低于 11** | 淘汰 5–9px 小标；英文索引可 11、字距收紧 |

加载：用 `next/font` 或正式 `@font-face` 接上已声明的 Noto，避免未加载时的 FOIT/回退跳动（这是视觉系统问题，不是文案）。

### 3.3 间距与层级

建议 4px 网格：`--space-1` 4 … `--space-8` 32。卡片内边距统一 `--space-4`（16）而不是 8/9/10/13 混用。

阴影只保留两级：`--shadow-card`（现 5px 5px 0 墨色）、`--shadow-pop`（抽屉）。不要再为 hover 加第三种 7px 7px。

边框：默认 `--line`，强调 `--ink`，危险 `--danger`。选中用 inset 3px token 色，不要每页一种。

z-index 刻度：`base 0 / sticky 20 / dock 22 / overlay 30 / dialog 60 / lightbox 70`。现 `create-drawer` 61、queue 22–23、nav 30，大致可用，写成 token。

### 3.4 状态色

| 状态 | 建议 | 现状混用 |
| --- | --- | --- |
| 成功/健康 | `--green` + 浅底 | 保存条 `#edf3e9`（可收成 token） |
| 警告/降级 | `--amber` | stale `#fff3df` / `#9a6534` |
| 危险/失败 | 单一 `--danger` | 多套红 |
| 进行中 | amber 底 + 不转除 spinner 外的大动画 | 保存中 `#f7f0dc` |
| 离线/未配置 | `--ink-soft` + 虚线边 | health-pill 另写 `#b23c25` |
| 焦点 | `outline: 3px solid var(--vermillion); offset 3px`（已有 `:1285`） | 工作台步骤却用 1px inset（`:204`），不一致 |

焦点环必须全局同一，组件不得改成 1px。

### 3.5 控件

按钮三级：主（ink）、次（outline）、鬼（ghost）。最小高度 36 桌面紧凑 / 40 默认。禁止第三套 25px 图标钮除非带 40px 热区。

表单：标签 12 meta，控件 13 ui，错误 12 danger 绑 `aria-describedby`。

卡片：`control-card` 可升为通用 `AppCard`：header 48–52px、body padding 16。设置、诊断、存储已经接近。

---

## 4. 桌面窗口下的槽位合同

所有桌面页落入三种模板之一。新功能不得发明第四种整体骨架。

### 4.1 模板 A：概览（首页、帮助）

顶栏 + 主栏 + 可选 280–328px 侧栏。≥1280 双列；900–1279 侧栏改到主栏下，不进抽屉。

### 4.2 模板 B：工作台（资产、剧本、分镜、生成、库、任务、未来导演）

顶栏 + 可停靠左导航 + 主栏 + **可选右检查器** + 底栏任务条。

| 宽度 | 左导航 | 检查器 | 底栏 |
| --- | --- | --- | --- |
| ≥1280 | 停靠，可拖宽，可折叠成 48px 图标轨 | 分镜/导演默认开；资产/生成可关 | 48px |
| 900–1279 | 可停靠或图标轨，不进汉堡 | 检查器改为右侧抽屉，选中时打开 | 48px，统计可省略 |
| <900 | 汉堡抽屉（现 760 行为） | 底部抽屉 | 保留 |

折叠/恢复：左导航与检查器、底栏的开闭写入 `localStorage`（已有侧栏宽、检查器宽、dock 隐藏）。缺的是 **左导航折叠成图标轨** 和 **检查器作为通用槽**。

### 4.3 模板 C：控制台（系统设置、项目设置）

顶栏 + 主栏卡片栈 + 粘滞诊断侧栏。≥1280 双列；900–1279 **设置板单列**（把现 900px 提到 1100–1280 之间），诊断改手风琴，避免粘滞侧栏吃主栏。

供应商平台内部布局由 V02-11A 负责，本模板只给 `.settings-board`。

---

## 5. 面板停靠、折叠、快捷键

现状可停靠：项目侧栏、分镜检查器、queue-dock。不可：诊断侧栏、生成参考区、资产记录、工作流节点检查器（workflow-studio 自有一套）。

目标：

- `DockProvider` 级别的约定（实现 Issue 再拆）：左、右、底三个槽，键名稳定，避免每页一套 localStorage。
- 折叠图标轨后仍显示当前步骤与未读/失败点。
- 快捷键（桌面约定，实现时集中一个 `keymap` 模块，避免和表单输入冲突）：

| 键 | 动作 | 现状 |
| --- | --- | --- |
| `Ctrl/Cmd+S` | 保存当前草稿 | 仅工作流画布（`use-viewport-interactions.ts:65`） |
| `Esc` | 关抽屉/lightbox/取消拖拽 | lightbox 无 Esc |
| `[` `]` | 折叠左/右槽 | 无 |
| `g` 然后字母 | 跳步骤（可选后期） | 无 |
| `?` | 快捷键帮助 | 无 |

不要把 Vim 式和弦做进首发。首发：Esc、Ctrl+S（有草稿的页面）、lightbox 缩放键。

工作流画布的快捷键留在工作流内，不提升为全局除非不与输入框冲突。

---

## 6. 加载 / 空 / 错误 / 部分成功 / 离线 / 后台任务

| 状态 | 现状 | 目标 |
| --- | --- | --- |
| 全页加载 | `.full-loading`、`.loading-panel` | 骨架对齐对应模板，禁止只有转圈+英文过程 |
| 段内加载 | 分镜「正在展开格子脚本…」、生成候选空卡 `aria-hidden` | 统一「正在读取…」+ 骨架 |
| 空 | `.asset-empty`、`.empty-project` | 标题 14/serif + 下一步按钮；辅助 ≥12 |
| 错误 | `.error-panel`、`.form-error` | 块级 `role="alert"` + 重试；字段级绑定 |
| 部分成功 | 生成页 stale banner 是好例子（`:1309`） | 升为 `Callout` 变体：warning/danger |
| 离线/API 不可达 | 首页「无法连接 MangaFlow API」 | 工作台进入时同样要有，而不是白屏 |
| 后台任务 | QueueDock + 任务中心 | dock 只显示等待/失败计数与最近一条；详情回任务中心。不要在每页复制任务表 |

离线：产品是本机单用户，没有「云同步掉线」。离线 = API 进程没起来。用错误模板，不用假的灰色只读模式。

---

## 7. 长列表、历史候选、大图

原则（实现 Issue 执行，本审计不定库）：

1. **缩略图**：列表与网格一律 `thumbnail/640` 或更小；lightbox 才用原图。`publicUrl` 已做 content→thumbnail 改写，生成/库/资产预览必须继续走 `thumbnailUrl` 优先。禁止网格 `eager` 全部。
2. **分页**：素材库 cursor 30 批次保留。任务中心按状态分组已有；组内超过 ~50 行再分页或虚拟化。
3. **虚拟化**：生成页候选超过 24、库单页缩略图超过 60、任务行超过 80 时用窗口化。首发可用 CSS 内容可见性 `content-visibility: auto` 作过渡，V02-52 再测。
4. **历史候选**：生成页批次切换保留；不要在检查器里无限堆原图。导演对比（V02-43）再加「原/结果」双槽，依赖该功能。
5. **大图**：lightbox 保持 dialog；补 Esc、焦点陷阱、滚轮缩放（transform only）。

供应商列表的套娃滚动（基线 `.provider-list { max-height: 920px }`）由 V02-11A 处理；本系统只要求控制台主栏跟随页滚，不再套第二根滚动条。

---

## 8. 动效

允许：`transform`、`opacity`。禁止：`top/left/width/height` 动画、大面积 `filter: brightness`、诊断刷新按钮 `rotate(-4deg)`（`:1351`）这类装饰。

时长：150–200ms 进入，120ms 退出；`cubic-bezier(.2,.75,.2,1)` 已在步骤导航使用，可升为 `--ease-out`。

`prefers-reduced-motion`：基线有 **四段** 重复规则（`:1264,:1269,:1359,:1379`）。应合并为一条全局：所有 transition/animation 0.01ms，spinner 可停。hover 位移一并取消。

创建抽屉 `transform: translateX(102%)`（`:149`）合格。Queue 不要用高度动画。

---

## 9. 不选型桌面壳

V02-53 才比较 Tauri 2 与 Electron。本系统假定 **现在就是本机浏览器窗口**（`127.0.0.1` + 桌面尺寸）。token 与模板不得依赖原生标题栏、流量侧栏或多窗口。将来壳只包同一套 DOM。

不要在本文写安装包、托盘、自动更新。

---

## 10. 与并行任务的边界

| 任务 | 本审计如何相处 |
| --- | --- |
| V02-11A 供应商 UI（已冻结 `21eec4a`） | 不改供应商组件树、不重写其 CSS。设置页模板与 token 对所有控制台卡片生效 |
| V02-50 文案 | 本审计不删「嘀咕」；token 改变字号后文案更短更好，清单仍归 V02-50 |
| V02-31A 分镜画布 | 画布交互归 31A；本审计只给画布页槽位 |
| V02-21 场景资产工作区 | 未完成：资产模板预留「场景」子导航，不画字段 |
| V02-23 角色模型包 | 未完成：人物子页保持现结构，包版本 UI 后接同一检查器槽 |
| V02-41 导演 | 未完成：步骤/模式预留，无假输入 |
| V02-10 Vertex 平权 | 首页 Vertex 卡退出后，侧栏改连接聚合；本审计标明，不在此实现 |
| V02-52 性能门禁 | 本审计给虚拟化/缩略图原则，不跑 FPS |

---

## 11. 设计 token 建议（实现时一次落地）

写入 `globals.css :root`（或后续 `tokens.css`），组件只引用：

```
color: ink, ink-soft, paper, paper-deep, white, line, line-dark,
       vermillion, green, amber, danger, muted,
       success-bg, warning-bg, danger-bg, focus
font: sans, serif, mono
text: display, title, body, ui, meta
space: 4,8,12,16,24,32,48
z: base, sticky, dock, overlay, dialog, lightbox
radius: 0（保持直角纸面）或 3px 按钮
shadow: card, pop
ease: out
duration: fast 120, base 180
```

圆角保持接近 0–3px，不要改成现代 12px 卡片，以免和已印刷的纸面语言冲突。

实现 Issue 应 **先加 token 再删魔法数字**，按页面切片，避免一次 1500 行 CSS 重写无法审查。

---

## 12. 组件边界

建议（后续实现，不在本任务创建文件）：

| 组件 | 职责 |
| --- | --- |
| `AppShell` / `GlobalNav` | 已有；删未用 `.rail` 或真正启用，二选一 |
| `PageTemplate` A/B/C | 槽：top / left / main / right / bottom |
| `AppCard` | 控制台卡片外壳 |
| `Callout` | 空/错/警告/部分成功 |
| `DockSlot` | 折叠、宽、localStorage |
| `QueueDock` | 已有；只消费统一任务摘要 |
| `ImageLightbox` | 已有；补键盘与焦点 |
| `CandidateGrid` | 缩略图 + 窗口化参数 |

工作流工作室继续用自己的 `workflow-editor.module.css`，但必须服从全局焦点环、reduced motion、字体级谱。

---

## 13. 测试矩阵

本审计不跑浏览器。后续视觉系统 PR：

| ID | 场景 | 期望 |
| --- | --- | --- |
| U1 | ≥1280 工作台 | 左导航停靠，主栏不被底栏遮住内容（已有 padding 70px，回归） |
| U2 | 900–1279 工作台 | 不进 760 汉堡；检查器为抽屉而非消失 |
| U3 | ≥1280 设置 | 主+侧；token 字号 ≥12 控件 |
| U4 | 900–1279 设置 | 单列，诊断不粘死挡住主卡 |
| U5 | 焦点环 | Tab 到全局 nav、步骤、主按钮均为 3px vermillion |
| U6 | reduced motion | 无 translateY hover；spinner 可停 |
| U7 | lightbox Esc | 关闭并焦点回触发图 |
| U8 | queue-dock 隐藏恢复 | localStorage 往返 |
| U9 | 侧栏拖宽 | 188–360 与现测试同类 |
| U10 | 未加载 Noto 时 | 回退栈仍可读（系统字体），正式 PR 应加载字体 |

不测：Lighthouse、FPS、真实供应商、Tauri。

---

## 14. 建议实现切片

1. **Token 与焦点/reduced-motion 收口**：`:root` 补齐，合并四段 reduced-motion，统一 focus-visible。不改布局。
2. **模板 C 设置壳**：900–1279 单列。不碰供应商内部（V02-11A）。
3. **模板 B 工作台槽**：左导航图标轨、通用右槽 API、底栏不变。
4. **lightbox + 候选网格缩略图/窗口化策略**：为 V02-52 做夹具，不跑性能窗口。
5. **首页侧栏去 Vertex 专属**（依赖 V02-10 退出条件）：改连接聚合。
6. **导演/场景/模型包槽位**：仅导航空位，功能 PR 填入。

---

## 15. 本审计未做的验证

- 未运行 `npm run check`、Playwright、Lighthouse、FPS、浏览器实机。
- 未读凭据、未调用真实供应商、未连 PostgreSQL/Redis。
- 未打开 V02-11A 工作区文件；供应商路径描述以基线 `provider-management.tsx` 与 `globals.css` 供应商段为准。
- 未选型 Tauri/Electron。
- 行号相对 `2613d978f64a2faee0282c6250501a453c13df0f`。
