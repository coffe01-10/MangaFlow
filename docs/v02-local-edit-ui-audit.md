# V02-43A 局部选择、候选比较与采用：只读 UX 审计

- 任务：Issue #58 / `[0.2.0][V02-43A]` Design local image editing and candidate comparison UX
- 基线：`2613d978f64a2faee0282c6250501a453c13df0f`
- 性质：L2 UI 只读设计。不实现代码，**不定数据库 schema**
- 产品意图（路线图 V02-43）：点图进局部修改；画笔/框选 mask、羽化、撤销；原图/结果对比；同区多次抽卡；收藏与采用；采用后只让受影响检查待复核
- 后端血缘与能力矩阵：V02-42 / V02-44（Issue 所称 V02-42A / V02-44A）。本文只列依赖，不发明表结构
- 未批准：V02-31A 画布、V02-41A 导演台、V02-51A 桌面壳均未复审，不得当契约

---

## 1. 当前界面（≥15 处证据）

局部编辑 **不存在**。现有表面是「整页候选网格 + 放大灯箱 + 分类修复」。

### 1.1 预览

1. 候选图点击只放大：`CandidateArtwork` `aria-label`「放大查看」，`onOpen(fullUrl, label)`（`apps/web/components/project-workspace/shared.tsx:67-78`）。
2. 灯箱 `ImageLightbox`：`role="dialog"`，背景点击关闭，工具栏 50%–250% 步进缩放，`transform: scale`（`workspace-chrome.tsx:86-95`，`globals.css:1547-1556`）。无并排、无滑块、无格叠加、无画笔。
3. 缩略图走 `publicUrl` → `/thumbnail/640`（`api.ts:761-767`）；灯箱用 `content_url`。网格 `sizes="(max-width: 900px) 46vw, 280px"`（`shared.tsx:73`）。
4. 无图时占位「等待 Worker 生成」（`shared.tsx:75`）。失败占位在资产管线另有 `candidate-placeholder failed`，生成台卡片仍用同一 Artwork。

### 1.2 候选、采用、下载、重试

5. 生成主路径永远整页 1K：`generateCandidate(batch, model, "1K", storyboard_version, references)`（`api.ts:988-992`，`use-generation-workspace.ts:183-196`，按钮 `generate-section.tsx:139`）。
6. 卡片操作：收藏、人工校对并暂选、视觉检查、升 2K/4K、删除（`generate-section.tsx:142`）。暂选 `window.confirm` 校对文字；已暂选按钮 disabled。删除亦 confirm，已暂选不可删。
7. 暂选 API `selectCandidate(..., manualTextConfirmed, acceptStale)`（`api.ts:999-1003`）。`is_selected` 与 `is_favorite` 分离（卡片 class `selected` / `favorited`）。
8. 版本：`version_state` CURRENT/STALE/STALE_ACCEPTED/LEGACY_UNKNOWN（`api.ts:480-494`）。Stale 横幅「沿用并重新检查」vs「按当前 V 重新生成」（`generate-section.tsx:103`）。
9. 下载：生产通过后单页 PNG 链 `selectedPagePngUrl`（`generate-section.tsx:164`，`api.ts:1065`）——下载的是 **当前采用页**，不是正在看的历史候选。
10. 重试：任务中心 FAILED → `retryJob`（`jobs-section.tsx:78`，`api.ts:1025`）。生成台没有「用同一参数再出一张局部」。升清是新 job（`upscaleCandidate` `api.ts:1054-1057`）。
11. 取消：活动任务 `cancelJob`（`jobs-section.tsx:78`，`api.ts:1024`）。生成按钮 pending 时禁用（`generate-section.tsx:139`），不能从卡片取消。

### 1.3 「修复」不是用户 mask

12. 检查失败可「修复气泡区域 / 单格 / 整页」，类型由类别硬映射（`display.ts:3-6`，`labels.ts:62-65`）。点击立即 `repairCandidate.mutate(inspection)`（`inspection-panel.tsx:34`）。
13. payload 含 `repair_type` 与 `target_regions: inspection.regions`（`use-generation-workspace.ts:239-247`，`api.ts:1040-1053`）。用户从未看见或编辑这些 regions。
14. 后端把空 `target_regions` 回填为检查结果 regions（`inspection.py:125-126`）。仍无画笔 UI。
15. 模型选择器文案已是「仅显示支持图片编辑的已启用模型」（`generate-section.tsx:117`，`shared.tsx:17-21`），但按下的动作仍是整页候选。能力过滤与交互粒度不一致。

### 1.4 比较与辨识

16. 候选网格并排多张卡（`globals.css:651`），靠 `selected` 边框和「已暂选」区分采用。没有原图|采用|新结果三槽，也没有滑块。
17. 灯箱一次一张。任务行点击结果再开灯箱（`jobs-section.tsx:70-73`）。历史批次切换（`generate-section.tsx:107-111`）一次看一个 batch，不能把 batch N 与采用图叠在一起。
18. 素材库 `is-selected` 样式（`library-section.tsx:98`）同样只标采用，不比较。

### 1.5 无障碍与输入装置缺口

19. 灯箱无 Esc 绑定、无焦点陷阱（`workspace-chrome.tsx:95` 只说明点击背景关闭）。
20. 缩放只有按钮，无滚轮/触控板 pan（stage `overflow: auto` 可滚，不是抓手平移）（`globals.css:1554`）。
21. `prefers-reduced-motion` 关掉 `.candidate-zoom` transition（`globals.css:1266`），但灯箱 img 仍 `transition: transform .16s`（`:1555`）。

---

## 2. 目标：点击进入局部修改

入口：生成台候选图（或灯箱内「局部修改」）。不从空白页进。必须已有一张 **源图**（通常为当前查看候选；采用图若与查看不同要明示）。

```
┌ 源：候选 03（已暂选）· 第 12 页 · V4 ─────────────────────────┐
│ 工具：矩形 | 画笔 | 擦除 | 羽化[──] | 撤销 | 重做 | 清空        │
│ ┌ 画布（可缩放平移）─────────────┐ ┌ 比较（推荐并排）──────┐ │
│ │ 源图 + mask 红叠层 + 格虚线    │ │ 左：源  右：新候选/空 │ │
│ └────────────────────────────────┘ └─────────────────────┘ │
│ 作用范围：本图  □格03（可选吸附）  模型 [ ]  费用 [估算|不可]   │
│ [取消]  [生成局部候选]     不会整页重生 · 不自动暂选           │
└ 状态：空闲 / 生成中 / 失败 / 能力不足 ────────────────────────────────┘
```

关闭局部编辑回到生成台网格，新候选出现在 **同一批次或 V02-42 规定的派生链** 上，源图仍在。

---

## 3. 选择工具

| 工具 | 行为 |
| --- | --- |
| 矩形 | 拖出轴对齐框，可再拖边。Shift 正方形 |
| 画笔 | 直径可调，在图上涂 mask |
| 擦除 | 从 mask 减去 |
| 羽化 | 作用于当前 mask 边缘，预览叠层变软；生成前写入参数（schema 归 V02-42） |
| 缩放平移 | 滚轮缩放，空格/中键/双指平移；不改变 mask 图坐标 |
| 撤销重做 | 只作用于 mask 笔画，至少 20 步本地栈 |
| 清空 | 清空 mask，不关编辑器 |

约束：

- mask 相对 **源图像素**，不是视口。
- 可选「吸附当前格」：用分镜 `bounds` 裁剪 mask（画布未落地时用归一化框换算）。无格数据则禁用吸附。
- 空 mask 禁止生成。
- Esc：若有未生成笔画，先确认丢弃。

首发不要求多边形套索、磁力、语义分割。

---

## 4. 三图身份（必须始终可辨识）

| 身份 | 定义 | UI |
| --- | --- | --- |
| 原图 / 源 | 进入局部编辑时锁定的那张候选 | 徽章「源 · 候选 03」 |
| 当前采用 | 页 `is_selected` 那张，可能与源不同 | 徽章「已暂选」；若源≠采用，比较区提供第三开关 |
| 新候选 | 本次局部 job 的结果 | 徽章「局部 · 候选 07」；生成中骨架 |

禁止只靠位置记忆。色：源=墨色描边，采用=绿 inset（沿用 `.candidate-card.selected` 绿体系），新=朱红。色盲再加文字徽章，不只靠色。

下载默认当前查看图；「下载采用页 PNG」继续走现有导出，标签写清。

---

## 5. 比较：推荐首发并排

| 方案 | 优点 | 缺点 | 首发 |
| --- | --- | --- | --- |
| **并排** | 同时看见源与新；读屏可两图两 alt；高 DPI 清晰 | 窄窗要堆叠 | **是** |
| 滑块 | 对齐像素 | 遮挡一半；触控易误；对比度差 | 第二切片 |
| 放大灯箱来回切 | 已有代码 | 无对照 | 仅作「单独看大图」 |

900–1279：并排改上下堆叠，上源下新，中间滑条可选后期。不要在窄桌面用滑块当唯一比较。

切换「仅源 / 仅新 / 并排」。默认并排。新候选未出时右侧空状态「生成后在此对照」。

---

## 6. 禁止整页重生

局部生成必须走 V02-42 的派生候选 API（父候选、mask、作用格）。在该 API 落地前，UI 可以做编辑器壳，**生成按钮 disabled**，说明等待契约。

不得把局部确认接到现有 `generateCandidate`（整页）或把「修复 PAGE」当默认降级。

若 V02-44 判定模型 **仅支持整图**：

- 预览写：「当前模型不能按选区重绘。可选：① 换支持 mask/edit 的模型；② 取消；③ **明确确认**整格/整页重绘（会丢掉未选区域的像素保证）。」
- ③ 必须单独 confirm，文案含「不是局部编辑」。
- 禁止静默调用整页 POST。

「修复单格」现有按钮（`inspection-panel.tsx:34`）应在实现后改为：打开本编辑器并预填检查 regions（若有几何）；没有几何则整格矩形。这是后续合流，不在本审计改代码。

---

## 7. 作业状态

| 状态 | UI |
| --- | --- |
| 生成中 | 右槽骨架 + 进度来自 Job；画笔锁定；可取消 → `cancelJob` |
| 取消 | mask 保留；无新候选；可再生成 |
| 失败 | alert 错误码+脱敏 message；mask 保留；重试同 payload |
| 未知/晚到 | 刷新后按 job_id 认领结果；已离开编辑器则在网格角标「局部结果已到」 |
| 离线 | API 不可达：生成禁用，说明启动 API，不装作成图 |
| 刷新 | mask 若未上服务端会丢——首发在生成前把 mask 随预检 POST 暂存（字段由 V02-42 定）；仅本地的编辑器要 beforeunload |
| 重复提交 | 进行中禁用生成；连点不排队两次 |

成功：新候选 `is_selected=false`。用户回到网格再暂选。采用后检查范围由 V02-42 规定「只使受影响类别待复核」；UI 展示「需复查：角色/连续性」而不是整页红灯（若 API 给得出）。

---

## 8. 键盘、触控板、高 DPI、对比度、读屏

- 工具热键：V 矩形、B 画笔、E 擦除、`[` `]` 笔径、Ctrl+Z/Y 撤销重做、Delete 清空需确认、Space 平移、Ctrl+滚轮缩放。
- 输入框内热键不抢。
- 触控板：双指缩放/平移；画笔用主按钮。首发不做压感。
- 高 DPI：mask 按图像像素；叠层用矢量/canvas backing store 为 devicePixelRatio。比较并排不要拉伸变形（object-fit contain，棋盘对齐可选）。
- 对比：mask 叠层不作为唯一信息，旁注「已选 12% 面积」。焦点环用全局 3px vermillion。
- 读屏：工具 `aria-pressed`；画布 `aria-label="局部选区画布"`；生成中 polite；结果图 alt「局部候选 07，相对源候选 03」。滑块若后做必须有数值与双图替代。
- reduced motion：缩放瞬时；禁止 mask 闪烁动画。

---

## 9. 对 V02-42A / V02-44A 的依赖（不定义 schema）

**V02-42（局部重抽卡血缘）必须回答 UI 才能真正生成：**

- 父候选 id、mask 存储（blob vs PNG vs 矢量）、羽化参数位置
- 作用对象：整图 vs 单格 vs 气泡周边
- 派生候选如何进 batch/ordinal；是否新 batch
- 取消、晚到 Worker、租约
- 采用后哪些检查重开、哪些页连续性受影响
- 重复提交语义

**V02-44（能力与失败）必须回答：**

- 模型：原生 mask / 仅 image edit / 仅整图
- UI 如何读到能力（连接 capability，而不是硬编码供应商名）
- 降级是否允许、如何计量费用

无这两项时：允许落地 **只读编辑器 + 并排空槽 + disabled 生成**，验收标明 BLOCKED。不得假成功。

V02-41 导演「在选区编辑」只负责带入源候选与可选格 id。V02-03 命令 schema 不在此冻结。

---

## 10. 测试矩阵（可拆实施）

| ID | 场景 | 期望 |
| --- | --- | --- |
| L1 | 点候选「局部修改」 | 进入编辑器，源徽章正确 |
| L2 | 空 mask 生成 | 按钮禁用 |
| L3 | 矩形+画笔+擦除+撤销 | 叠层与栈符合；清空需确认 |
| L4 | 缩放平移 | mask 不漂；复位后位置正确 |
| L5 | 并排 | 源与新同时可见，徽章不同 |
| L6 | 源≠已暂选 | 可切换查看采用图，不混淆下载 |
| L7 | 生成中取消 | cancelJob；无新候选；mask 在 |
| L8 | 失败 | 错误可见；可重试；不暂选 |
| L9 | 刷新晚到 | 结果挂到父候选链（API 有则测） |
| L10 | 连点生成 | 第二次不发 |
| L11 | 模型无局部 | 不发整页；解释+可选显式降级 |
| L12 | V02-42 未落地 | 生成 disabled + 说明 |
| L13 | Esc / beforeunload | 有未提交 mask 则确认 |
| L14 | 键盘 V/B/E、焦点环 | 工具切换，Tab 可达生成 |
| L15 | 900px | 上下比较，工具不溢出 |
| L16 | reduced motion | 无位移动画 |
| L17 | 成功 | 网格出现新卡，源仍未取消暂选 |
| L18 | 读屏 alt | 含源/新身份 |

不测：真实供应商、付费图、PG/Redis、Lighthouse、画笔压感。

---

## 11. 建议切片

1. 局部编辑壳：从灯箱/卡片进入、源徽章、矩形选、缩放平移、disabled 生成。
2. 画笔/擦除/羽化/撤销栈 + beforeunload。
3. 并排比较槽（无结果时的空态）。
4. 接 V02-42 生成/取消/失败/晚到。
5. 接 V02-44 能力解释与显式降级。
6. 检查「修复单格」改走编辑器（独立 PR）。

---

## 12. 未验证边界

- NOT RUN：浏览器、真实供应商、PostgreSQL/Redis、Lighthouse/FPS、`npm run check`
- BLOCKED：V02-42 血缘 API、V02-44 能力矩阵、V02-03 命令 schema
- 未把 V02-31A/41A/51A 当已批准契约
- 行号相对 `2613d978f64a2faee0282c6250501a453c13df0f`
