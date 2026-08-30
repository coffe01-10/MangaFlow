# V02-30A 视觉分镜布局数据契约设计

- 任务：Issue #48 / `[0.2.0][V02-30A] Design the visual storyboard layout data contract`
- 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（`origin/master`）
- 工作分支：`codex/v02-30a-storyboard-layout-contract`（worktree `D:\自媒体\漫画工作流-deepseek-v02-30a`）
- 任务性质：L3 数据契约设计草案（DeepSeek 起草，不拥有最终批准权；最终方案由 lead 集中复审确认）
- 约束：本文件只冻结设计契约，**不实现代码、迁移、测试；不编辑 `docs/roadmap.md` / `docs/development-progress.md` / `plan.md`；不决定具体 React 画布库**
- 修订记录：（由 lead 接管收口时填写）

---

## 1. 执行摘要

当前分镜几何只有两个自由 JSON：`Panel.bounds`（`{x, y, width, height}`，0–1 归一化、4 位小数、`content_workflow.py:429-495` 的 `japanese_panel_layout` 生成）与 `Dialogue.region`（默认 `{"preferred": "upper_inner"}` 的语义锚点，`schemas.py:548`）。分镜编辑路径中 **`PanelUpdate` 不含 `bounds`**（`schemas.py:527-541`）——用户无法移动/缩放格子，只能靠 `PATCH /pages/{id}/layout`（panel_count + layout_mode）整体重建。`MangaPage.storyboard_version` 由 `mark_storyboard_changed` 递增（`services/editor.py:8-10`），旧候选经 `PageCandidate.based_on_storyboard_version` 失效——这是既有版本化基础。

本设计把布局几何升级为**规范化的结构契约**：页面画布（物理尺寸 + 出血/安全区）+ 归一化几何（格子矩形/未来多边形 + z-order + 阅读顺序）+ 结构化气泡（矩形/锚点/尾巴/文字区域/旋转）。**关键原则：几何坐标保持 0–1 归一化（分辨率无关），画布物理尺寸独立表达，方向（RTL/LTR）作为页面属性在渲染/导出层应用而非存进几何**。旧 `bounds`/`region` 全部无损保留为兜底，迁移零重写，消费函数优先读新结构。

**红线**：迁移不重写任何既有 `bounds`/`region` 值；不新建命令历史表（撤销/重做为 V02-31 客户端交互）；`Panel.bounds`/`Dialogue.region` 列保持 JSON 列型，新结构是其**内部子结构**而非新表。后端调度与候选版本机制（`storyboard_version`）零改动，仅扩展 schema 与校验。

---

## 2. 当前 0–1 坐标、页面、格子、气泡和工作流数据

### 2.1 坐标现状

`japanese_panel_layout`（`content_workflow.py:429-495`）返回 `list[dict]`：

```json
{ "x": 0.012, "y": 0.012, "width": 0.976, "height": 0.448 }
```

- **0–1 归一化**、`round(..., 4)`（1/10000 精度）、内边距 `gap=0.012`。
- dynamic/balanced 两种模式；**RTL 镜像在生成时实现**：`page_number % 2 == 0` 时 `values = [(1-x-width, y, width, height)]` 翻转 x（`content_workflow.py:485-486`）——方向翻转逻辑藏在布局生成里，不是规范数据。
- 该结果直接写入 `Panel.bounds`（`content_workflow.py:708`）。

### 2.2 页面/格子/气泡/工作流数据

- `MangaPage`（`models.py:253-280`）：`reading_direction`（默认 "rtl"）、`page_function`、`panel_count`、`scene_ids`/`beat_ids`、`storyboard_version`（页级整数）、`selected_candidate_id`/`selected_candidate_ack_version`、`locked_fields`。**无画布物理尺寸字段**。
- `Panel`（`models.py:283-309`）：`reading_order`（`(page_id, reading_order)` 唯一约束）、`bounds`（JSON）、`bleed`（bool）、`borderless`（bool）、`bubble_regions`（JSON list）、`sound_effects`（JSON list）、`version`（乐观锁）。
- `Dialogue`（`models.py:312-323`）：`panel_id`、`speaker_character_id`、`target_text`、`reading_order`（`(panel_id, reading_order)` 唯一）、`text_direction`（vertical/horizontal）、`region`（JSON，默认 `{"preferred":"upper_inner"}`）、`rewrite_forbidden`。**无 version 字段**（气泡编辑靠父格 `panel_version` 乐观锁）。
- 工作流数据：分镜 API（`routes/workflow/storyboard.py`）——`GET /pages/{id}/storyboard`、`PATCH /pages/{id}/layout`（panel_count 3–5 + layout_mode）、`PATCH /panels/{id}`（**无 bounds**）、`POST/PATCH/DELETE /panels/{id}/dialogues`（`panel_version` 乐观锁）。所有编辑调用 `mark_storyboard_changed(page)`（`storyboard.py:169,210,241,264`）+ `mark_pages_for_review`。
- `StoryboardRead`（`schemas.py:521-524`）= `page + panels + candidate_count`。

### 2.3 分镜版本机制

`mark_storyboard_changed`（`editor.py:8-10`）：`storyboard_version += 1` 并清 `selected_candidate_ack_version`。候选创建时记录 `based_on_storyboard_version`（`schemas.py:599`）；生产门禁比对候选版本与当前页版本（`routes/projects.py:207-213`）——**布局改动使旧候选失效**是本设计复用的既有事实基础。

---

## 3. 画布物理尺寸、单位、方向和分辨率无关坐标

**原则：几何用 0–1 归一化坐标（分辨率无关），画布物理尺寸独立表达，方向是页面属性。**

### 3.1 `MangaPage.canvas`（新增 JSON 列）

```json
{
  "width_mm": 182, "height_mm": 257,     // 物理尺寸（B5 竖版默认；按 page_ratio 映射）
  "bleed_mm": 3,                          // 出血框（导出裁切）
  "safe_mm": 5,                           // 安全区（内容不越界）
  "unit": "mm"                            // 唯一单位，禁止混用 px
}
```

- `canvas` 缺省时由消费函数按 `page_ratio` 生成默认（**惰性默认，零数据写入**）：B5 竖版 → `182×257`，出血 `3mm`，安全 `5mm`。
- 几何坐标与画布的关系：几何 `x∈[0,1]` 映射到 `[0, width_mm]`，渲染时 `(x * width_mm, y * height_mm)`。归一化空间**不含出血延伸**（格子矩形在 `[0,1]` 内，出血格用 `bleed:true` 标志延伸渲染，见 §7）。

### 3.2 方向

- `reading_direction`（rtl/ltr）保留在 `MangaPage` 层；`Panel.reading_order`/`Dialogue.reading_order` 是**逻辑阅读序**（1,2,3…），与视觉位置解耦。
- **几何存储不翻转**：`bounds` 始终用左→右抽象坐标（`x=0` 为左缘）。RTL 时阅读顺序编号在渲染/编辑器覆盖层从右到左展示（V02-31 的「阅读顺序编号覆盖层」），导出按方向翻转。现状 `content_workflow.py:485-486` 的生成时翻转收敛到渲染层，**不迁移既有 bounds 值**。

---

## 4. 矩形和未来多边形格子

`Panel.bounds` 升级为内部子结构 `PanelGeometry`（列型仍是 JSON）：

```json
{
  "type": "rect" | "polygon",
  "rect": { "x": 0.1, "y": 0.1, "width": 0.4, "height": 0.4 },   // type=rect 时必填
  "polygon": [ {"x":0.1,"y":0.1}, {"x":0.4,"y":0.08}, ... ],     // type=polygon 时必填
  "rotation": 0,                                                  // 度，-360..360，默认 0
  "z_order": 1                                                    // 绘制顺序（§5）
}
```

- **兼容**：旧 `bounds={x,y,width,height}` 由 `_read_geometry` 在读取时归一化为 `{type:"rect", rect:{x,y,width,height}, rotation:0, z_order:reading_order}`（**读取期映射，不写库**）；写入时若旧结构（无 type 键）则按 §10 无损升级为规范结构。
- **约束**：rect 的 x/width 与 y/height 均在 `[0,1]`；`width/height ≥ 0.03`（≈5mm）；polygon 顶点 `3 ≤ n ≤ 32` 且所有顶点在 `[0,1]` 内；`rotation` 对 rect 可用、对 polygon 首版禁用（V02-31 不做旋转 UI，schema 预留）。
- 多边形为**未来能力预留**：V02-31 首版只实现 rect 编辑；`type="polygon"` 只在读取期兼容保留、不提供写入 UI。

---

## 5. 层级、z-order、裁切和重叠

- **`z_order`**（整数 ≥1）：格子重叠时的绘制顺序（值大者在上）。`z_order` 不与 `reading_order` 绑定——允许"后读的格压住先读的格"这类漫画惯例。
- **重叠规则**：
  - 普通格不得相互重叠（服务端校验：两 rect 相交 → 409，除非一方 `bleed=true` 或 `borderless=true`）。
  - 出血/无边框格可与邻格重叠，靠 `z_order` 决定覆盖关系。
  - 重叠格之间**不做内容裁切**（各格独立渲染，绘制序决定遮挡）。
- **裁切**：格子边界裁切内容由渲染层按形状实现（`clip-path`/canvas clip），数据层不存裁切参数。
- **默认**：读取期旧 bounds → `z_order=reading_order`（顺序布局天然无重叠）。

---

## 6. 阅读顺序与 RTL

- `reading_order` 是**逻辑阅读序**，`(page_id, reading_order)` 唯一约束保留。
- **调整顺序**：新增 `PATCH /pages/{page_id}/reading-order`，载荷 `{ order: [panel_id, ...] }`（页内全部格子新序），服务端**单事务**内重排 `reading_order` 并处理唯一约束（临时 +1 偏移再落位，或先置负再落位——由实现按 SQLite/PostgreSQL 差异固化）。交换后 `mark_storyboard_changed`。
- **RTL 展示**：编辑器「阅读顺序编号覆盖层」（V02-31）在 `reading_direction=rtl` 时把编号 1 渲染在页面右侧；几何坐标不翻转（§3.2）。

---

## 7. 气泡矩形、锚点、尾巴目标、文字区域和旋转

`Dialogue` 新增 `bubble`（JSON 列，nullable）。`region` 保留为旧数据兜底（对齐 `Scene.location` 兜底模式）：

```json
{
  "type": "rect" | "ellipse",
  "rect": { "x": 0.50, "y": 0.08, "width": 0.20, "height": 0.14 },  // 气泡主体（归一化）
  "anchor": { "x": 0.54, "y": 0.15 },     // 气泡锚点（尾巴起点，格内或邻近）
  "tail_target": { "x": 0.58, "y": 0.30 },// 尾巴指向（说话角色嘴部/指定位置）
  "rotation": 0,                           // 度，默认 0
  "text_region": { "x": 0.52, "y": 0.10, "width": 0.16, "height": 0.10 },  // 文字区域
  "mapped_from_legacy": false              // 读取期旧 region 映射标记
}
```

- 所有坐标为 0–1 归一化（与格子同一空间）。`tail_target`/`anchor` 允许略越出所属格（尾巴跨格是漫画惯例）；气泡主体 `rect` 不得越出页面 `[0,1]`。
- `text_region` 必须在气泡 `rect` 内（默认按 `padding` 计算；显式给定时校验包含关系）。
- **兼容**：旧 `region`（`{"preferred":"upper_inner"}` 等语义锚点）由 `_read_bubble` 映射为几何：`preferred` 键查内置锚点表（`upper_inner`→页右上 1/4 区、`lower_outer`→页左下 1/4 区等，映射表为只读常量），生成 `{rect, anchor, tail_target, rotation:0}` 且 `mapped_from_legacy:true`；无 `preferred` 的旧 dict 原样兜底（消费函数读 `bubble` 缺失时回退 `region`）。
- 气泡编辑乐观锁沿用父格 `panel_version`（§8.2）。

---

## 8. 出血框、安全区和打印边距

- 画布参数在 `MangaPage.canvas`（§3.1）：`bleed_mm`、`safe_mm` 定义出血/安全物理尺寸。
- **出血格**：`Panel.bleed=true`（既有字段）表示该格延伸到出血框；渲染/导出时格子几何从归一化空间外扩 `bleed_mm` 到画布边缘。数据层不存外扩坐标（归一化空间不含出血，保持分辨率无关）。
- **安全区**：渲染层约束普通内容在 `safe_mm` 内；出血格可越过安全区到出血框。
- **打印边距**：导出应用（`ExportBundle` 流程）读 `canvas` 计算裁切，本契约不定义导出参数。

---

## 9. resize、移动、吸附和约束

### 9.1 数据契约约束（服务端强制校验）

| 对象 | 规则 |
| --- | --- |
| 格子 rect | 0–1 内；`width/height ≥ 0.03`；普通格不得与邻格重叠（§5）；`bleed` 格可越界到出血框（几何仍在 0–1，越界由渲染外扩） |
| 格子 polygon | 顶点 3–32，均在 0–1 内 |
| 气泡 rect | 在页面 0–1 内；`text_region ⊂ rect` |
| 气泡 anchor/tail | 允许跨格（尾巴惯例） |
| 坐标精度 | 所有坐标统一 **4 位小数**（round 4，对齐现状） |

### 9.2 吸附与对齐（V02-31 UI 行为，不持久化）

- 吸附是对齐到网格（1/2、1/3）、相邻格边、出血/安全线——**吸附结果是坐标值，落库即普通坐标**，不存"吸附到 X"的语义。
- 约束（snap 后的最终校验）由服务端 §9.1 执行；UI 拖动时预览可用性（变红表示将 409），保存才发请求。

---

## 10. 编辑版本、乐观并发和命令幂等

### 10.1 版本与乐观并发

- 格子：`Panel.version`（既有乐观锁）。V02-30 实现时 `PanelUpdate` 增加 `bounds` 字段（当前无），走既有 `panel.version != payload.version → 409`。
- 气泡：**沿用父格 `panel_version`**（既有 `DialogueCreate/Update/Delete` 的乐观锁，`schemas.py:544-564`），不单独给 `Dialogue` 加 version 列——气泡与所属格在同一画布会话内编辑，父格版本足够，避免 schema 复杂度与双版本漂移。
- 页级一致性锚点：`MangaPage.storyboard_version`。**批量保存前校验**：客户端提交的 `storyboard_version` 必须等于服务端当前值，否则 409（并发画布会话检测）。

### 10.2 命令幂等

- 编辑 API 语义是**目标状态覆盖**（"设置 bounds 为 X"），重复执行结果一致——无需命令 id 或命令表。幂等靠 `exclude_unset` 的 PATCH 语义 + 乐观锁（409 时客户端刷新重放）。
- 不新增「命令队列/命令表」表：批量几何保存（§10.3）整包原子覆盖。

### 10.3 保存接口

- **单对象微调**：`PATCH /panels/{id}`（扩展 `bounds`）、`PATCH /dialogues/{id}`（扩展 `bubble`）、`PATCH /pages/{page_id}/reading-order`（重排顺序）。
- **画布整包保存**：新增 `PUT /pages/{page_id}/storyboard-geometry`，载荷 `{ storyboard_version, panels: [{panel_id, bounds, reading_order}], dialogues: [{dialogue_id, bubble, reading_order}] }`，**单事务**原子落库 + `mark_storyboard_changed`，返回 `StoryboardRead`。用于 V02-31 一次拖动/调整会话后的批量落盘。

### 10.4 撤销/重做需要保存什么

**结论：不保存命令历史，不新增服务端 undo 表。** 撤销/重做是 V02-31 画布内的客户端交互：

- 客户端维护本地 undo/redo 栈（每次几何操作的 `{target, before, after}`），undo 时重放历史坐标并调保存 API 覆盖为目标状态（服务端幂等接受）。
- 服务端只需保证：(a) 每个保存操作原子、幂等（§10.2）；(b) 保存失败不造成状态分叉——客户端保留本地栈与草稿直到保存成功（V02-32 门禁）；(c) 批量保存后 `storyboard_version` 递增使旧候选失效（既有机制）。
- 页面刷新后撤销历史丢失（可接受：撤销历史是交互态，不是持久数据）；持久化的是**最终几何状态 + storyboard_version**。
- 若 lead 要求跨刷新撤销，再评估服务端命令历史表（本契约默认不做）。

---

## 11. 旧分镜无损默认映射

| 旧数据 | 映射（读取期，不写库） |
| --- | --- |
| `Panel.bounds={x,y,width,height}` | `{type:"rect", rect:{x,y,width,height}, rotation:0, z_order:reading_order}` |
| `Dialogue.region={"preferred":"upper_inner"}` | `bubble` 几何（锚点表映射）+ `mapped_from_legacy:true` |
| `Dialogue.region` 其他自由 dict | `region` 原样兜底（`bubble` 缺失时消费） |
| `MangaPage` 无 canvas | 消费函数按 `page_ratio` 生成默认画布 |
| `Panel.sound_effects` 旧字符串元素 | 读取期包装为 `{text, x, y, rotation:0, size:null}`（§12） |

**无损保证**：以上全部是**读取期规范化**，库内 `bounds`/`region` 原值零触碰；消费函数（`_read_geometry`/`_read_bubble`）优先读规范结构，缺失时读旧字段。生成/导出路径在读取时获得规范几何，历史候选的 `prompt_snapshot` 不变。

---

## 12. 拟声词（SoundEffect）结构化（加分项，纳入契约）

`Panel.sound_effects` 元素结构化（列仍为 JSON）：

```json
{ "text": "ドンッ", "x": 0.3, "y": 0.5, "rotation": -15, "size": 0.12 }
```

- `x/y` 为元素中心（归一化）、`rotation` 度、`size` 归一化字号；旧字符串元素读取期包装为 `{text, x:null, y:null, rotation:0, size:null}`（null 由渲染层布局兜底）。
- 服务端校验：`rotation ∈ [-360, 360]`；`x/y/size` 若给值须在 `[0,1]`。

---

## 13. upgrade/backfill/downgrade

迁移命名以 lead 合并顺序协调（V02-20A 与 V02-30A 均自 `20260830_19` 出发；合并冲突时由 lead 定号，建议本迁移编号 `20260830_21` 并保持 `down_revision` 指向实际合并后的前一个 head）。内容：

```text
upgrade:
  manga_pages  ADD COLUMN canvas  JSON nullable      # 不写入数据（惰性默认）
  dialogues    ADD COLUMN bubble  JSON nullable      # 不写入数据（region 兜底）
  # 不触碰 panel.bounds / dialogue.region / sound_effects 原值
backfill:
  无数据写入。旧结构由读取期规范化（§11）提供等价几何；
  canvas 由消费函数按 page_ratio 生成默认（§3.1）。
downgrade:
  dialogues  DROP COLUMN bubble
  manga_pages DROP COLUMN canvas
```

- 迁移内禁止 import `Settings`、禁止对 `panels`/`dialogues`/`manga_pages` 的任何 `UPDATE`（对齐 V02-02 M7 禁止条款）。
- **SQLite 与 PostgreSQL 差异**：均为纯加列/删列 DDL；SQLite 无需 batch_alter（`ADD COLUMN nullable` 直接支持，对齐 `20260717_11` 的 `batch_alter_table` 惯例也可，实现二选一）；无新约束、无索引、无数据迁移，两库行为一致。
- 真实 PostgreSQL 升降级验收：**NOT RUN**（沿用项目既有真实 PG 边界；SQLite 往返不替代）。

---

## 14. API schema 与最大节点/坐标精度限制

### 14.1 API 变更

| 端点 | 变更 |
| --- | --- |
| `PATCH /panels/{id}` | `PanelUpdate` 新增 `bounds`（`PanelGeometry` 结构，§4） |
| `PATCH /dialogues/{id}` | `DialogueUpdate` 新增 `bubble`（§7） |
| `PATCH /pages/{page_id}/reading-order` | 新增：整页 `reading_order` 重排 |
| `PUT /pages/{page_id}/storyboard-geometry` | 新增：整包几何保存（`storyboard_version` + panels + dialogues） |
| `GET /pages/{id}/storyboard` | `PanelRead.bounds`/`DialogueRead` 返回规范结构；`PageRead` 新增 `canvas` |
| `PATCH /pages/{id}/layout` | `PageLayoutUpdate.panel_count` 上限 3–5 → **3–8**（对齐 V02-32「3–8 格」门禁）；`layout_mode` 保留 |

`PanelGeometry`/`BubbleGeometry`/`SoundEffect` 为新增 Pydantic schema，`extra="forbid"`。

### 14.2 上限与精度

| 项 | 值 |
| --- | --- |
| 每页格数 | 3–8 |
| 每页气泡数 | ≤ 8/格（沿用 data-model 既有每页最多 8 气泡的硬上限上限语义，按页校验） |
| 每页拟声词 | ≤ 32 |
| **单页几何节点上限** | **128**（8 格 + 120 气泡/拟声 的容限；对齐 V02-32 的 100 节点压力场景） |
| 坐标精度 | 4 位小数（1/10000） |
| polygon 顶点 | 3–32 |
| rotation | -360..360 |
| `StoryboardRead` 100 节点响应体积 | ≤ ~200KB（8 格 + 120 气泡 × ~1.2KB）；超限拒绝（防超大 payload） |

---

## 15. 100 节点性能数据边界

- V02-32 压力场景（100 节点 = 8 格 + 若干气泡/拟声）的**数据边界**由 §14.2 固定：单页 ≤128 节点、4 位小数、响应 ≤200KB。
- **性能门禁属 V02-32**：固定采样窗口测画布拖拽/缩放 FPS、保存往返延迟；**不挑最好结果**（roadmap V02-32 明确）。本契约只保证数据形态可支撑 100 节点，不承诺具体渲染帧率。
- 保存失败不得造成画布与服务端状态分叉：服务端整包保存**原子提交**（失败返回 409/500，客户端保留本地栈重试）；乐观锁冲突时客户端以 `storyboard_version` 检测并提示刷新，不静默覆盖。
- **不决定具体 React 画布库**：数据契约只约束服务端 schema 与约束；canvas 渲染库（Konva/Fabric 等）由 V02-31 实现时按需求另行评估，本文件不选型。

---

## 16. 测试矩阵（拆 V02-30 / V02-31 / V02-32）

### 16.1 V02-30（后端数据契约，L3）

| 编号 | 场景 | 类型 |
| --- | --- | --- |
| L1 | 迁移：空库与含旧分镜库 `upgrade→downgrade→upgrade` 往返；`bounds`/`region`/`sound_effects` 原值逐字段不变；`canvas`/`bubble` 列新增后为 NULL | 新增 pytest（对齐 `20260717_11` 列级迁移风格） |
| L2 | 读取期映射：旧 bounds → `{type:rect, z_order:reading_order}`；旧 region preferred → bubble 几何 + `mapped_from_legacy`；`canvas` 缺省 → 按 page_ratio 默认 | 新增 pytest |
| L3 | 几何校验：rect 越界/最小尺寸/重叠（普通格 409，bleed 格豁免）；polygon 顶点 3–32；坐标精度 round 4；bubble text_region ⊄ rect → 422 | 新增 pytest |
| L4 | API：`PATCH /panels/{id}` bounds 乐观锁 409；`PATCH /dialogues/{id}` bubble 走 panel_version 409；`PUT storyboard-geometry` 原子整包 + storyboard_version 不匹配 409 | 新增 pytest |
| L5 | 阅读顺序重排：`PATCH reading-order` 单事务交换不破坏 `(page_id, reading_order)` 唯一约束；RTL 几何不翻转（bounds 原值不变） | 新增 pytest |
| L6 | 上限：格数 3–8、节点 ≤128、响应 >200KB 拒绝；`extra="forbid"` 拒绝未知几何键 | 新增 pytest |
| L7 | 版本失效：几何保存后 `storyboard_version +1`、`selected_candidate_ack_version=NULL`、旧候选基于旧版本不通过生产门禁（既有 `mark_storyboard_changed` 行为回归） | 扩展既有测试 |
| L8 | 拟声词：旧字符串读取期包装；新结构化元素校验 rotation/x/y/size | 新增 pytest |

### 16.2 V02-31（视觉画布编辑器，L2，Grok UI/浏览器优先）

| 编号 | 场景 | 类型 |
| --- | --- | --- |
| U1 | 拖动格子、resize、移动：边界吸附/对齐预览、越界禁用、保存后 bounds 更新 | 组件测试 + 浏览器 |
| U2 | 气泡拖动/缩放、锚点/尾巴调整、旋转；text_region 跟随 | 组件测试 |
| U3 | 画布缩放/平移、出血框/安全区开关、阅读顺序编号覆盖层（RTL 时 1 在右） | 组件测试 |
| U4 | 撤销/重做：本地栈重放坐标并保存；保存失败保留草稿与栈、不丢编辑 | 组件测试 |
| U5 | 保存状态：保存中/失败/409 乐观并发提示刷新；离开页面保护 | 组件测试 |
| U6 | 键盘操作：Tab 序、箭头微调、Enter/Space、删除确认 | 组件测试 + 浏览器 |
| U7 | `npm run check` 全绿；`git diff --check` 通过 | 门禁 |

### 16.3 V02-32（可用性与性能门禁，L2 验收）

| 编号 | 场景 | 类型 |
| --- | --- | --- |
| P1 | 常见页面尺寸 + 3–8 格回归：布局生成、保存、读取往返 | 浏览器回归 |
| P2 | 重叠/越界：普通格重叠 409、出血格重叠合法、z_order 绘制序 | 浏览器回归 |
| P3 | RTL 阅读顺序：编号覆盖层方向正确、几何不翻转 | 浏览器回归 |
| P4 | 键盘可达性与触控板：拖动/缩放无焦点丢失、无位移漂移 | 浏览器回归 |
| P5 | **100 节点压力**：固定采样窗口测拖拽/缩放 FPS 与保存延迟；记录完整运行不挑最好结果 | 浏览器性能门禁 |
| P6 | 保存失败分叉：断网/500 后画布与服务端状态一致、可重试 | 浏览器回归 |

---

## 17. 未验证边界（NOT RUN / NOT VERIFIED）

1. **真实 PostgreSQL 升降级未运行**：L1 的 PG 变体需独立环境，SQLite 往返不替代（沿用项目既有真实 PG 边界）。
2. **真实供应商/浏览器性能未验证**：不调用真实供应商；Lighthouse/FPS/Playwright 全量需独立性能窗口（对齐 `architecture.md:131`），本任务不运行。
3. **多边形格子的编辑器 UI 未设计**：`type="polygon"` 仅为数据预留，V02-31 首版不做多边形编辑，验证留给后续版本。
4. **服务端 undo 命令历史表未设计**：本契约默认撤销/重做在客户端；若 lead 裁定需要跨刷新撤销，需另行设计命令历史表（本契约不预建）。
