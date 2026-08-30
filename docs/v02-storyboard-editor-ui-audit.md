# V02-31A 分镜编辑器：只读审计与视觉画布工作流

- 任务：Issue #45 / `[0.2.0][V02-31A]` Audit the storyboard editor and design the visual canvas workflow
- 基线：`2613d978f64a2faee0282c6250501a453c13df0f`（`origin/master`，分支 `codex/v02-31a-storyboard-editor-ui-audit`）
- 性质：L2 UI 只读审计与信息架构设计，不实现功能，不改服务端 schema
- 产品入口：项目工作台 `section === "storyboard"`，路由 `/projects/{id}/storyboard`
- 后续实现：V02-31；数据契约：V02-30；可用性与 100 节点性能门禁：V02-32

本文只约束分镜视觉画布的信息架构、操作流、状态、组件拆分与测试。不决定数据库字段，不覆盖全站视觉系统（那是 V02-51A），不授权真实供应商或浏览器性能窗口。

---

## 1. 当前结构与摩擦点

### 1.1 页面、入口与文件角色

| 表面 | 文件 | 实际职责 |
| --- | --- | --- |
| 工作台分镜段 | `apps/web/components/project-workspace/storyboard-section.tsx` | 章节选择、旧分页警告、空状态；动态加载 `StoryboardEditor` |
| 分镜编辑器 | `apps/web/components/storyboard-editor.tsx`（277 行单文件） | 页条、格数/版式、联系表预览、属性检查器、气泡表单 |
| 对应测试 | `apps/web/components/storyboard-editor.test.tsx` | 仅 1 个用例：检查器宽度拖拽与 `localStorage` |
| 工作台挂载 | `project-workspace.tsx:289-301` | `section === "storyboard"`；query `page`、`character` 深链 |
| 导航文案 | `project-workspace/labels.ts:19` | 「分页与分镜 / 场景切页、格子脚本」 |
| 生成页回跳 | `generate-section.tsx:102` | 剧本或分镜已改 →「检查分镜」 |
| 生产门禁深链 | `production-readiness.tsx:33` | `/storyboard?page=&character=&edit=outfit` |
| 工作流节点 | `workflow_engine/catalog.py:91,241` | `director.storyboard`「分页与分镜」是 DAG 规划节点，不是画布 |
| API | `apps/web/lib/api.ts:346-385,893-897,980` | `Storyboard` / `StoryboardPanel` / `PanelDialogue`；`updatePanel` **不含 bounds** |
| 后端路由 | `apps/api/app/api/routes/workflow/storyboard.py` | GET storyboard、PATCH layout、PATCH panel、气泡 CRUD；409 乐观锁 |
| 布局算法 | `content_workflow.py:429-495` | `japanese_panel_layout`：归一化矩形，右至左，偶页镜像 |
| 样式 | `globals.css:466-566`、`:1216-1234`、`:1292-1376`、`:1527-1566` | 联系表 3:4、检查器、气泡卡；1024/1440/900 断点 |

`apps/web/app/providers.tsx` 仍是 Query 根，与分镜无关。工作流工作室里的 `director.storyboard` 节点调用规划服务，不打开本编辑器。

### 1.2 当前组件树（按渲染顺序）

```
ProjectWorkspace                         project-workspace.tsx
└─ StoryboardSection                     storyboard-section.tsx:13
   ├─ canvas-header 章节选择 / 页数
   ├─ 旧分页警告（缺剧本来源）           :45
   ├─ 空状态「尚未生成分页分镜」         :46
   └─ StoryboardEditor                   storyboard-editor.tsx:59
      ├─ 保存状态条「已保存 / 保存中 / 保存失败」+ 版本 +「画布专注」  :241
      ├─ 窄屏页选择 <select>             :242  （≥1024 隐藏，见 CSS :1298/:1371）
      ├─ 页缩略条 P.00N                  :243
      ├─ 状态行：字数/气泡配额 +「从本页重新计算」 :244
      ├─ 格数 3/4/5 + 动态错落/均衡网格   :245
      ├─ notice / form-error
      └─ storyboard-worktable            :248
         ├─ panel-contact-sheet          :249  绝对定位矩形按钮，不是画布
         ├─ panel-inspector-resizer      :252  唯一指针拖拽
         └─ panel-inspector              :253
            ├─ 只读 readout 或 panel-edit-form（景别/镜头/人物/出血）
            └─ dialogue-editor 气泡卡片   :270-272
```

「画布专注」(`focusMode`，`:96,:241,:1305-1308`) 只隐藏页条/状态/版式控件，把联系表 `min-height` 拉到 `78vh`。它不提供缩放、平移、适配或复位。

### 1.3 当前数据形状（只读，不改 schema）

格子 `Panel`（`models.py:283-308`）：

| 字段 | 现状 | UI 是否编辑 |
| --- | --- | --- |
| `reading_order` | 页内唯一，规划时按模板序号 | 只展示「格 01」，不能拖改顺序 |
| `bounds` | JSON `{x,y,width,height}`，0–1 | 只用于 CSS `left/top/width/height` 百分比；**PATCH 载荷没有该字段**（`PanelUpdate` `schemas.py:527-541`；前端 `api.ts:893`） |
| `shot_type` / `camera_*` / `actions` / `background` / `props` / `sound_effects` | 文本与枚举 | 检查器表单 |
| `characters` + `character_presence` | VISIBLE / OFFSCREEN / MENTIONED | 表单 |
| `outfits` / `expressions` | 按角色 | 表单 |
| `bleed` / `borderless` | bool | 两个 checkbox（`:267`），画布上无出血框/安全区 |
| `bubble_regions` | 规划时写 `[]`（`content_workflow.py:727`） | 不渲染、不编辑 |
| `locked_fields` | 后端 `ensure_unlocked` | UI 不展示、不切换 |
| `version` | 每次 PATCH/气泡变更 +1 | 随请求带上；409 原文「分镜格已被更新，请刷新后重试」 |

气泡 `Dialogue`（`models.py:312-323`）：

| 字段 | 现状 | UI 是否编辑 |
| --- | --- | --- |
| `target_text` / `speaker_character_id` / `text_direction` / `rewrite_forbidden` | 表单 | 是 |
| `reading_order` | 创建时 `max+1` | 列表顺序即阅读序，不能画布重排 |
| `region` | 默认 `{"preferred": "upper_inner"}`（`schemas.py:548`，规划 `content_workflow.py:761`） | 前端创建/更新 **不发送** region（`api.ts:895-897`）；画布不画气泡 |

页 `MangaPage`：`storyboard_version` 在格子/气泡变更时递增（`mark_storyboard_changed`，`editor.py:8-10`），并使后续页 `NEEDS_REVIEW`。改格数会 **删除该页全部 Panel 与 Dialogue 再重建**（`update_page_layout` `:818-829`）。API 将格数限制在 3–5（`PageLayoutUpdate` `schemas.py:603-605`），与规划器内部 6/7 格模板（`content_workflow.py:466-492`）不一致。

联系表比例写死 `aspect-ratio: 3/4`（`globals.css:486`），与项目 `page_ratio` 无绑定。坐标空间是「页矩形 0–1」，没有像素画布尺寸、出血毫米/px、安全区 inset、z-index、多边形顶点、气泡锚点或尾巴向量。

### 1.4 摩擦点（文件:行号）

**发现与入口**

1. 工作台第四步叫「分页与分镜」，主标题是「内容有多少，页面就有多少」（`storyboard-section.tsx:44`）。用户进来先看到分页产能，后看到格子脚本，没有「在页上摆格子和气泡」的心理模型。
2. 工作流 `director.storyboard`（`catalog.py:241`）与本页是两条入口：一条自动规划，一条表单编辑。视觉画布必须仍挂在 `/projects/{id}/storyboard`，不要新开 `/canvas`。
3. 生成页与生产门禁会深链到本页（`generate-section.tsx:102`，`production-readiness.tsx:33`）。`?character=` 会自动打开缺服装的格（`storyboard-editor.tsx:181-219`），但 `edit=outfit` 查询串被忽略。

**「画布」其实是联系表**

4. `panel-contact-sheet` 用绝对定位按钮模拟页（`:249`）。格子不能拖、不能 resize、没有 handle、没有命中测试分层。点击只选中并打开检查器。
5. `bounds` 已存在却不能 round-trip：前端不读多边形，后端 `PanelUpdate` 拒绝未知字段且未列 `bounds`。任何「拖格子」实现若只改 CSS，刷新即丢失。
6. 重叠格子没有 z 序。规划模板有意错落，但 DOM 顺序 = `reading_order`，后绘制的格永远在上，与漫画阅读序不一定相同。
7. 联系表 `overflow: hidden`（`globals.css:486`）。出血格若画到页外会被裁切；bleed checkbox 不改变预览几何。
8. 「画布专注」名不副实：无缩放、无平移、无适配窗口、无复位。检查器仍占 `--inspector-width`。

**格子与版式**

9. 改 3/4/5 格或版式会毁掉本页全部格子与气泡再从剧本映射（`content_workflow.py:818-829`）。UI 说明「内容仍从已确认剧本与原文区间重新映射」（`:245`），但没有不可逆确认层。这与「拖动微调几何、保留对白」的目标冲突。
10. UI 与 `PageLayoutUpdate` 锁死 3–5 格；规划器 6/7 格模板无法在本页出现。视觉编辑若允许任意格数，必须由 V02-30 决定 API 上限，而不是前端先放开。
11. 阅读顺序只是按钮左上角「格 01」（`:249`）和 CSS `::after " · 当前选中"`（`:1304`）。没有覆盖层开关，不能按阅读序播放，不能把视觉 z 与阅读序分开。
12. `locked_fields` 在保存时会 409，检查器既不展示锁，也不能上锁。

**气泡**

13. 气泡只活在右侧卡片栈。联系表只写 `N 气泡`（`:250`），页上没有气泡框、没有尾巴、没有锚点。
14. `Dialogue.region` 与 `Panel.bubble_regions` 都是自由 JSON。UI 不读不写。默认 `preferred: upper_inner` 对画布毫无几何意义。
15. 删除气泡用 `window.confirm`（`:271`）。与供应商设置审计已否定的原生 confirm 同类。
16. 气泡没有画布级选中；多气泡时只能在检查器里纵向滚动。不能把气泡拖到另一格（后端气泡绑定 `panel_id`）。

**导航与几何工具**

17. 无缩放（滚轮/手势/按钮）、无平移、无适配窗口、无 100% 复位。大格数时小格按钮里的 7–11px 文案不可读（`globals.css:490-495`）。
18. 无吸附、无对齐线、无键盘微调格子。唯一键盘几何是检查器分隔条左右箭头（`:252`），改的是面板宽度，不是格子。
19. 无多选、无 Shift/框选、无图层列表。选中模型是「当前页唯一 `panelId`，否则第一格」（`:91`）。

**状态、撤销、离开**

20. 保存条区分保存中/失败/已保存（`:237-241`），`aria-live="polite"`。失败只是底部 `form-error` 显示 `error.message`。409 没有「放弃草稿并重新加载」。
21. 检查器草稿（`panelDraft` / `dialogueDrafts` / `newDialogue`）与服务端分叉时，切页（`:242-243`）或点另一格（`:249`）会丢掉未保存格子草稿（`setEditingPanel(false); setPanelDraft(null)`）。气泡草稿对象按 id 留在 state 里，但切页后不提示。
22. 工作流编辑器有 `beforeunload`（`workflow-studio.tsx:314`）。分镜编辑器没有离开保护，浏览器刷新/工作台切到生成页会丢草稿。
23. 无撤销/重做栈。保存即提交；失败后可改表单再试，但不能回到上一几何或上一则对白。
24. 改格数后 `setPanelId("")`（`:151`），选中回到第一格，用户失去上下文。

**键盘、焦点、读屏、动效**

25. 格子是 `<button>`，可 Tab，但绝对定位顺序是 DOM/`reading_order`，不是空间阅读序（RTL 页上左上角可能不是第一格）。
26. 新气泡 `autoFocus`（`:49`）会从画布/格子抢走焦点。
27. 检查器大量控件缺 `aria-label`（景别、背景、道具、拟声词、出血）。气泡文字/说话人有 label。分隔条有 `role="separator"` 与 valuemin/max/now。
28. `prefers-reduced-motion` 关掉 `.panel-proof` transition（`:1266`），但 `.dialogue-card` 仍 `transform: translateY(-1px)`（`:531-533`）。专注模式没有独立 reduced-motion 路径。
29. 屏幕阅读器在联系表上听到「格 01」+ 动作摘要，听不到几何、出血、气泡位置。没有 live region 宣布「已选中第 3 格」。

**窄桌面**

30. `max-width: 1023.98px`：页条消失改下拉，检查器叠到联系表下方，分隔条 `display:none`（`:1370-1376`）。900–1279px 正是桌面主适配面，此时「画布 + 检查器」变成「先预览后长表单」，气泡编辑完全离开页。
31. `min-width: 1440px` 才把页条收到左侧（`:1363-1367,:1537-1544`）。1280–1439 仍是顶栏横滑页条 + 联系表/检查器，搜索不到画布工具栏的位置。
32. 联系表 `width: min(100%, 560px)`（`:1527-1530`）在宽屏被居中缩小，浪费主栏。视觉画布应吃满主栏高度，而不是 560px 名片。

**测试缺口**

33. 现测只覆盖检查器 resize。不覆盖：选格、编辑保存、409、切页丢草稿、版式重建确认、气泡 CRUD、深链 `?page=`/`?character=`、专注模式、键盘、reduced motion、空/加载/错误。更没有画布几何用例。

### 1.5 范围外记录（不改）

- 运行设置、供应商平台、资产工作区、生成台抽卡本身。
- 工作流 DAG 画布（`workflow-editor`）的节点拖拽；可借鉴交互，但不共用坐标系。
- V02-30 的 Alembic 形态、默认映射公式、回滚。
- V02-32 的真实 100 节点采样窗口、触控板 FPS。
- V02-40/41 导演命令与局部重抽卡；画布点击应预留「选区上下文」，但不在本设计实现命令栏。
- `plan.md` / `docs/roadmap.md` / `docs/development-progress.md`。

---

## 2. 目标信息架构与完整操作流

### 2.1 目标分层

`/projects/{id}/storyboard` 仍是唯一用户入口。主区从「联系表 + 表单」升级为 **页画布 + 检查器**。不新开路由。

```
L0  画布工具栏     页选择 · 缩放 · 适配 · 复位 · 吸附 · 覆盖层 · 撤销/重做 · 专注
L1  页画布          页矩形、格子轮廓、气泡、对齐线、出血/安全区、阅读序号
L2  选择与变换      选中框、resize handle、尾巴手柄；单选默认，Shift 加选
L3  检查器          沿用现有分镜/气泡字段；几何数值只读回显，不靠表单当唯一入口
```

几何操作写 **画布**；叙事字段写 **检查器**。禁止再把拖动伪装成「改 3/4/5 格模板」。

### 2.2 坐标系与对象（UI 合同，schema 由 V02-30 冻结）

画布显示空间：

- 一页 = 一个页面矩形。宽高比取项目 `page_ratio`（当前联系表写死 3:4 是缺陷）。
- 现有扁平 `bounds.{x,y,width,height}` 永久保持兼容；扩展 shape/rotation/z 读取 `geometry`。矩形拖拽同时更新 bounds 与 geometry.rect，二者必须一致。
- 视口变换（缩放、平移、适配、复位）是纯前端 `viewTransform`，不入库。
- 多边形、非轴对齐边、圆角、出血毫米、安全区 inset、气泡锚点/尾巴、图层 z 为 **V02-30 字段**。UI 必须预留渲染分支，但在契约返回前：
  - 多边形格：按 AABB 渲染，轮廓用虚线标注「非矩形，待契约」；**禁止**用矩形 resize 静默把多边形打成四元组。
  - 无安全区字段时：安全区开关禁用并说明，不画假框。

对象：

| 对象 | 用户能做（目标） | 契约依赖 |
| --- | --- | --- |
| 页 | 适配、复位、看出血/安全区 | 页像素尺寸、出血、安全区：V02-30 |
| 格子 | 选中、拖、resize、键盘微调、锁定、阅读序号 | 矩形 bounds 与 geometry 进入 V02-30 整包 PUT；单对象 PATCH 仅供检查器字段 |
| 气泡 | 选中、拖、缩放框、尾巴终点、锚点到格边 | `region` 形状：V02-30；过渡期禁止假装写入 `upper_inner` |
| 对齐参考 | 吸附页边、格边、中线 | 纯前端，可先做 |
| 覆盖层 | 阅读序、出血、安全区、对齐 | 开关纯前端；几何数据部分等 V02-30 |

### 2.3 完整操作流

**A. 打开一页并浏览**

1. 进入 `/projects/{id}/storyboard`（可带 `?page=`）。
2. 画布适配窗口（contain），页居中。工具栏显示缩放百分比。
3. 格子按 `bounds` 绘制；阅读序号默认开。气泡在契约可用前可显示为格内占位芯片（计数量），不可拖。
4. 单击格：选中 + 检查器只读；双击或「编辑本格」进入字段编辑（保持现有保存语义）。

**B. 拖动 / resize 矩形格（依赖 V02-30 整包保存契约）**

1. 拖动格体移动；边缘/角 handle resize。
2. Shift 锁定比例；Alt 从中心；吸附开时贴齐相邻边与页边，显示对齐线。
3. 拖动期间只改本地草稿；显式保存时通过 `PUT /pages/{page_id}/storyboard-geometry` 一次提交完整快照与 request_id。单对象 PATCH 只供检查器的独立字段编辑。
4. 409：保留本地几何为草稿，提示「分镜格已在别处更新」，提供放弃并重新加载。
5. 重叠允许；不自动改 `reading_order`。

**C. 多边形格（必须等 V02-30）**

- 有顶点数组后：选中显示顶点，拖顶点，禁止矩形 handle 覆盖多边形。
- 契约未定时：只渲染 AABB，resize 禁用，检查器说明「此格为多边形，等待布局契约」。

**D. 气泡几何（必须等 V02-30 的 region/tail/anchor）**

1. 在格内拖气泡框；缩放改变框；尾巴终点可拖到说话人一侧。
2. 松开更新本地 `bubble` 草稿；保存时进入同一整包 PUT。旧 region 只读兜底，不再承载新几何。
3. 首发不改变 dialogue.panel_id；拖到格外时回弹并提示「气泡仍属于本格」。跨格移动另立端点与成员校验后再开放。
4. 文字内容、说话人、横竖排、锁定改写仍在检查器，不在画布上直接排字（排字预览可后做）。

**E. 画布导航**

- 滚轮 / Ctrl+滚轮缩放，空格或中键平移，触控板双指平移。
- 「适配窗口」= contain 整页（含当前出血开关所揭示的范围）。
- 「复位」= 100% 且页左上对齐视口原点（或居中，见开放问题；推荐适配=contain 居中，复位=100% 居中）。
- 缩放不改变入库坐标。

**F. 吸附、对齐、键盘**

- 吸附默认开，工具栏可关。阈值按屏幕像素（建议 6px），不按归一化。
- 对齐线：与页中线、其它格边重合时出现。
- 方向键：选中对象按 1px 视口增量微调；Shift+方向 = 10px。转换为归一化后写入。
- Esc 取消拖拽，恢复 drag-start 几何。

**G. 阅读顺序覆盖层**

- 开关默认开。每个格中心或左上（RTL 时右上）显示阅读序号。
- 本层 **不** 用拖序号来改 `reading_order`，除非 V02-30 把阅读序从布局序号里拆出来。过渡期检查器只读显示序号；改序列为后续 Issue。

**H. 出血框与安全区**

- 两个独立开关。出血：页外一圈；安全区：页内 inset。
- 无字段时开关 disabled，说明「等待 V02-30 出血/安全区尺寸」。不要用 CSS 硬编码假 3mm。
- `Panel.bleed === true` 的格轮廓用不同描边；这与页级出血框不是同一控件。

**I. 图层、选中、锁定、多选**

- 图层列表（检查器底部或左侧窄列）：页 → 格（按阅读序）→ 格内气泡。点击同步画布选中。
- 锁定：调用现有 `locked_fields` 语义之前，UI 只能本地锁变换（不发 PATCH）。持久锁等 V02-30 确认锁的是几何还是叙事字段。
- 多选：Shift 加选格。批量移动允许；批量 resize 不允许（避免非矩形）。气泡与格不能混选。

**J. 撤销 / 重做 / 保存 / 离开**

- 本地命令栈：几何与检查器字段在「保存」前都是草稿。Undo 先撤销未保存命令。
- 画布几何统一通过 `PUT /pages/{page_id}/storyboard-geometry` 保存完整快照；工具栏统一为「保存本页」。检查器中不属于画布快照的独立叙事字段仍可使用单对象 PATCH。
- 保存中：画布可继续看，变换 handle 禁用当前对象。
- 失败：命令留在栈顶，错误绑到检查器；几何不回滚除非用户撤销。
- 409：不覆盖本地栈；「放弃草稿并重新加载」清空该页命令栈。
- 离开保护：`beforeunload` + 工作台切 section 时若有未保存草稿则对话框。工作流工作室已有 `beforeunload` 先例，分镜应对齐。

**K. 改格数 / 版式（危险操作，保留但降级）**

- 仍提供「重建本页版式」，但移出主工具栏到「页菜单」。
- 确认文案必须写清：将删除本页全部格子与气泡并按剧本重映射；历史候选会过期。这不是视觉拖拽的替代。

### 2.4 非目标

- 不在画布上直接绘制成品图（生成结果仍在单页生成）。可选底图「显示已采用候选」为后续切片，默认关。
- 不在本页做导演自然语言栏（V02-41）。画布选中态要能被导演当上下文读取。
- 不在本页做局部 mask / 重抽卡（V02-42/43）。
- 不把工作流节点编辑器嵌进来。

---

## 3. 桌面端文字线框与状态

### 3.1 宽桌面（≥1280px）

工作台仍是「左导航 + 主栏」。分镜主栏：

```
┌ 分页与分镜                              第 012 页 · V4 · 已保存 ─┐
│ [P.011][P.012][P.013]   [-] 87% [+]  [适配] [复位] [吸附]        │
│                         [阅读序] [出血] [安全区]  [撤销][重做]    │
│ ┌─页画布─────────────────────────────┐ ┌ 检查器 ─────────────┐ │
│ │  安全区…                          │ │ P.012 / 格 03        │ │
│ │   ┌ 02 ┐┌ 01 ┐                    │ │ 叙事字段…            │ │
│ │   └────┘└────┘                    │ │ 气泡列表…            │ │
│ │   ┌ 03 ─────────┐                 │ │ 图层：格03 / 气泡2   │ │
│ │   └─────────────┘                 │ └─────────────────────┘ │
│ └───────────────────────────────────┘                         │
└ 吸附：格03 顶边 ↔ 格01 底边                                      ┘
```

画布吃满主栏剩余高度；检查器沿用可拖宽度（现 320–620，`:99-108`）。页条在 ≥1440 已在左侧；1280–1439 把页条收进工具栏下的横向条，不要挡住画布高度。

### 3.2 窄桌面（900–1279px）

不要等到 1024 才把检查器砸到下面。建议：

| 宽度 | 行为 |
| --- | --- |
| ≥1440 | 左侧页条 + 画布 + 检查器 |
| 1280–1439 | 顶/工具栏页条 + 画布 + 检查器 |
| 1100–1279 | 画布优先；检查器改为可折叠右侧抽屉，默认打开 |
| 900–1099 | 画布全宽；检查器以底部抽屉打开（选中后自动升起）；页用 select |
| <900 | 非本设计主面；保持可编辑但不保证 handle 精度 |

窄桌面必须仍能：选格、打开检查器、缩放适配、看到阅读序号。拖拽 handle 最小 12px 热区，触控不作为本 Issue 验收。

### 3.3 平台级状态

| 状态 | UI |
| --- | --- |
| 无分页 | 现有空状态保留 |
| 旧分页不可用 | 现有警告保留 |
| 画布加载 | 页矩形骨架，不说「正在展开格子脚本…」这种过程黑话；改为「正在读取本页分镜」 |
| 画布错误 | 画布区 alert + 重试；检查器留空 |
| 无格 | 「本页没有格子。从剧本重新计算，或等待 V02-30 允许手工建格。」 |
| 保存中 | 工具栏状态「保存中」；当前对象 handle 禁用 |
| 保存失败 | 检查器 `role="alert"` + 字段关联 |
| 冲突 409 | 「分镜格已在别处更新，请重新加载」+ 放弃草稿 |
| 重建版式中 | 整页 busy，完成后选中第一格并宣布 |

---

## 4. 100 节点压力的渲染策略（不跑门禁）

V02-32 会用固定窗口测 100 节点。本审计只定策略，禁止在本任务跑 Lighthouse/FPS。

定义：1 节点 = 1 个可命中对象。产品持久数据遵守 3–8 格、每页最多 8 气泡；100 节点压力用不落库的合成渲染 fixture，不得构造违反 API 门禁的 12 格 × 6 气泡页面。

原则：

1. **不要** 把 100 个对象都做成带阴影、transition、内部排版的 DOM 按钮（当前 `.panel-proof` 即此模式）。
2. 建议分层：
   - 静态层：页底、网格、已采用候选缩略图（若开启）→ 一张图或一个 canvas。
   - 矢量层：格轮廓、气泡框、尾巴、对齐线 → SVG 或 canvas。
   - 交互层：仅 **选中对象** 挂 DOM handle（8 个 resize + 尾巴点）。未选中对象用命中测试，不挂节点。
3. 阅读序号用一个 overlay 列表，viewport culling：视口外不渲染文字。
4. 拖动期间：只更新选中轮廓的 transform，不对整页做 React 重渲染；mouseup 再写入 state。
5. 检查器与画布拆开，避免每次 pointermove 刷新气泡表单。
6. 100 节点场景的实现 Issue 必须准备固定夹具（格数、每格气泡数），测量完整窗口，不挑最好一次。本审计不规定阈值。

当前联系表每个格是完整 `<button>` 树（`:249-250`），5 格已嫌挤；按此 DOM 模型线性扩到 100 会失败。这是架构约束，不是后期优化。

---

## 5. 键盘、焦点、读屏、reduced motion

- Tab 顺序：工具栏 → 页条 → 画布（一个 tab stop，方向键在对象间移动）→ 检查器字段。不要让每个格都进 Tab 环（100 节点会毁键盘）。
- 画布角色：`role="application"` 需克制；更稳妥是 `role="group"` + `aria-label="页画布"`，选中对象 `aria-activedescendant`。
- 方向键微调；Delete 删除气泡（有确认）；格子删除不提供，除非 V02-30 定义孤立格。
- Enter 打开检查器编辑；Esc 取消拖拽或退出编辑（有未保存则先确认）。
- 读屏：选中时 live polite「第 3 格，阅读序 3，出血」。覆盖层开关要有 `aria-pressed`。
- reduced motion：对齐线出现/消失、选中框、抽屉，只用 opacity/transform 且 duration≤150ms；开关关闭时瞬时切状态。现有 `.dialogue-card:hover { transform }` 必须纳入同一策略。
- 焦点必须可见；不要靠 7px 红字当焦点环。

---

## 6. 文案：保留 / 精炼 / 删除

保留：出血格、无边框、锁定文字、从本页重新计算、已保存/保存中/保存失败、新增气泡。

精炼：

- 「画布专注」→「专注模式」（仍只是藏铬，真正的适配是另一按钮）。
- 「正在展开格子脚本…」→「正在读取本页分镜」。
- 「分页与分镜」导航可留；画布标题用「页画布」，避免再强调产能。

删除或移出主路径：

- 版式说明长句「格子数量与版式只重排…」移到重建确认对话框。
- 联系表上的动作/背景全文；画布只保留序号与选中轮廓，细节进检查器。

新增：适配窗口、复位、吸附、阅读序、安全区、放弃草稿并重新加载、离开未保存确认、多边形只读说明。

---

## 7. 组件拆分建议

目标：拆开 277 行单文件，画布可测，检查器可独立保存。仍用现有 `storyboard-*` / `panel-*` class，不引入新设计系统。

建议目录 `apps/web/components/storyboard-editor/`：

| 模块 | 职责 |
| --- | --- |
| `storyboard-editor.tsx` | 查询、页状态、命令栈、离开保护 |
| `storyboard-toolbar.tsx` | 缩放、覆盖层、撤销 |
| `page-canvas.tsx` | 视口变换、命中测试、绘制页 |
| `panel-node.tsx` | 单格轮廓与选中 |
| `bubble-node.tsx` | 契约后的气泡框/尾巴；契约前占位芯片 |
| `transform-handles.tsx` | resize / 尾巴手柄 |
| `guides-overlay.tsx` | 对齐线、阅读序、出血/安全区 |
| `panel-inspector.tsx` | 从现文件 `:253-274` 迁出 |
| `dialogue-card.tsx` | 现 `DialogueCard` |
| `layout-rebuild-dialog.tsx` | 危险的格数/版式重建 |
| `geometry.ts` | 归一化 ↔ 视口、吸附、AABB |
| `command-stack.ts` | undo/redo |
| `storyboard-copy.ts` | 中文标签 |

`StoryboardSection` 继续当入口。不要把画布塞进 `workflow-editor`。

检查器字段保持现有 mutation；画布会话通过 V02-30 的整包 PUT 写入 bounds/geometry/bubble。保存前统一使用 storyboard_version，响应丢失按 request_id 安全重放。

---

## 8. 测试矩阵

保留现有检查器宽度用例。下列为后续实现 Issue 的验收，本审计不执行。

| ID | 场景 | 期望 |
| --- | --- | --- |
| S1 | 单击格 | 选中轮廓 + 检查器该格 |
| S2 | 拖矩形格并保存 | 一次整包 PUT（request_id + storyboard_version）；刷新后位置仍在，响应丢失重试不重复递增版本 |
| S3 | 拖动中 Esc | 不发请求，几何回 drag-start |
| S4 | 多边形格 | 无矩形 handle；说明可见 |
| S5 | 滚轮缩放 / 适配 / 复位 | 不发 PATCH；百分比更新 |
| S6 | 吸附 | 近边出对齐线；关闭吸附则无 |
| S7 | 方向键 | 归一化 bounds 改变并保存策略符合实现 Issue（即时或失焦） |
| S8 | 阅读序开关 | 序号出现/消失；不改 reading_order |
| S9 | 出血/安全区无字段 | 控件 disabled + 说明 |
| S10 | 多选两格拖动 | 相对位移；不改各自尺寸 |
| S11 | Undo 未保存拖拽 | 回到上一几何 |
| S12 | 保存 409 | 中文冲突；草稿保留；可放弃 |
| S13 | 切到生成页有草稿 | 拦截确认 |
| S14 | 重建 3→5 格 | 确认后才调用 layout；取消不调用 |
| S15 | `?page=` `?character=` | 仍定位；不丢 |
| S16 | 键盘：画布一个 tab stop | 方向键换格 |
| S17 | reduced motion | 无位移动画 |
| S18 | 900px | 检查器抽屉，画布仍可见 |
| S19 | ≥1280px | 画布与检查器并排 |
| S20 | 气泡几何 | bubble 拖动/缩放/尾巴进入整包 PUT；region 旧值只读兜底；跨格首发回弹 |

不测：真实供应商、付费图、100 节点 FPS（V02-32）、PostgreSQL。

---

## 9. 依赖 V02-30 才能决定的问题

本审计现以 lead 收口后的 V02-30 为唯一数据契约：扁平 bounds 保留、geometry/bubble 新增、坐标为最终页面坐标、重叠合法且由 z_order 决定、整包 PUT 为画布保存主路径。以下旧问题列表仅作历史记录，不再允许实现自行选择答案。

1. **页的像素/物理尺寸从哪来？** 项目 `page_ratio`、导出 DPI、还是分镜自带 `canvas_width/height`？没有它，出血/安全区只能是归一化 inset。
2. **多边形如何存？** 顶点归一化列表、是否允许洞、是否与 AABB 并存。未定前 UI 不得用矩形 resize 覆盖。
3. **`reading_order` 能否与绘制 z 分离？** 现 UniqueConstraint `(page_id, reading_order)`。改序是否重排约束、是否需要独立 `z_index`。
4. **气泡 `region` 的目标形状？** 现 `preferred: upper_inner`。需要：框 `{x,y,width,height}`（相对格还是页）、锚点边、尾巴终点、是否曲线。
5. **`bubble_regions` 与 `Dialogue.region` 谁是事实来源？** 规划写空数组，对话写 preferred。画布只能有一份。
6. **出血与安全区的单位与默认值？** 页级还是项目级；旧页如何无损默认（V02-30 原文要求）。
7. **`PanelUpdate` 是否包含 `bounds`、z、shape？** 今天不含 bounds，前端无法持久化拖拽。
8. **跨格移动气泡是否允许？** 涉及 `panel_id` 与阅读序重排。
9. **锁定几何 vs 锁定叙事？** 现 `locked_fields` 是字段名列表。变换锁是否同一列。
10. **手工新建格/气泡几何是否允许空剧本页？** 现 layout 重建要求 `ranges`/`beat_ids`/`scene_ids`（`content_workflow.py:776-778`）。
11. **6–7 格页的编辑政策？** 规划器能生成，设置 API 只接受 3–5。画布是只读那些页，还是放开上限。
12. **RTL 阅读序覆盖层的锚点？** 现偶页镜像模板（`content_workflow.py:485-486`）。序号放左上还是右上由阅读方向决定，需契约确认。

在这些问题回答前，任何「视觉编辑器已完成」的实现 PR 只能声称视口与检查器改进，不能声称格子/气泡几何已持久化。

---

## 10. 建议实现切片（供后续 PR，本任务不执行）

每片独立 Issue，禁止与 V02-30 迁移、V02-32 性能窗口、V02-11 供应商 UI 混提。

1. **检查器与状态加固（可不改 schema）**：离开保护、409 放弃草稿、切格/切页确认、重建版式对话框、深链 `edit=`、reduced motion、保存条与字段错误关联。覆盖 S12–S15、S17。
2. **视口与画布壳（可不改 schema）**：用页矩形替换联系表按钮树；缩放/平移/适配/复位；阅读序 CSS 覆盖层；选中轮廓。几何仍只读。覆盖 S1、S5、S8、S16、S18、S19。
3. **矩形 bounds 写入（依赖 V02-30 整包 API）**：拖、resize、吸附、键盘微调、undo，保存为完整页面快照；多边形只读。覆盖 S2、S3、S4、S6、S7、S11。
4. **气泡几何**：按 V02-30 bubble 契约实现节点、尾巴和整包保存；首发不跨格。覆盖 S20。
5. **出血/安全区/图层/多选（必须 V02-30 尺寸与 z）**：开关真正画框；图层列表；Shift 多选。覆盖 S9、S10。
6. **V02-32**：100 节点固定夹具、键盘/RTL/触控板、保存失败不分叉。不在本审计执行。

---

## 11. 本审计未做的验证

- 未运行 `npm run check`、Vitest、Playwright、浏览器实机、Lighthouse、FPS。
- 未读凭据、未调用真实供应商、未连 PostgreSQL/Redis。
- 行号相对基线 `2613d978f64a2faee0282c6250501a453c13df0f`。V02-11A 供应商 UI 改动在另一 worktree，本文件不引用那些行号。
- 已按 V02-30 lead 收口结果同步；实现不得回退到 region 写新气泡几何或逐对象保存整次画布会话。
