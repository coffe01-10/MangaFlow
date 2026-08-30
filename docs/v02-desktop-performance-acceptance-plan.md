# V02-52A 桌面端性能验收计划与确定性度量规范

- 任务：Issue #55 / `[0.2.0][V02-52A] Define deterministic desktop performance acceptance`
- 统一基线：`2613d978f64a2faee0282c6250501a453c13df0f`
- 分支：`codex/v02-52a-desktop-performance-plan`
- worktree：`D:\自媒体\漫画工作流-gemini-v02-52a`
- 性质：L1 性能验收计划与规范制定，不运行重型压测，不修改任何业务代码
- 关联规划：`docs/roadmap.md`（M5 桌面端与端到端交付 V02-52 / V02-53）、`AGENTS.md`（性能测量规则）

---

## 1. 执行摘要与目标范围

MangaFlow 作为面向专业创作者的连续漫画生产工具，对界面流畅度、大图渲染性能以及低延迟交互有极高要求。为防止以往性能评估中出现的“选择性挑选最佳单次结果（Cherry-picking）”、“测试环境混杂开发态开销”、“孤儿进程残存干扰测量”等问题，本规范建立了**严格确定性、可独立复现、全样本保留的桌面端性能验收方案**。

### 1.1 核心原则
1. **禁止只选最佳结果**：每项测试必须连续运行 $N=5$ 轮，强制保留所有原始运行样本（包括失败与异常轮次），报告 P50（中位数）与 P95（95 分位值）。
2. **清理失败即测试失败（Failure Propagation）**：测试生命周期结束时，若端口释放、子进程树清理或临时目录回收发生任何异常，该次测试直接判定为 `FAILED`，严禁隐瞒。
3. **环境形态明确隔离**：严格区分 `Web-Dev`（开发态）、`Web-Prod`（生产构建，当前准入基准）与 `Desktop-Shell`（未来桌面壳，待 V02-53A 交付）。
4. **只读计划声明**：本文件为纯规范与执行标准，所有性能数据在此阶段统一标记为 `NOT RUN`。

---

## 2. 测试环境与基础设施所有权规范

### 2.1 三层运行环境定义

| 环境形态 | 构建与启动方式 | 适用阶段 | 性能基准用途 |
| --- | --- | --- | --- |
| **Web-Dev** | `npm run dev` (Next.js dev + FastAPI reload) | 开发自测 | 仅供日常逻辑调试，**禁止**作为性能准入数据 |
| **Web-Prod (当前基准)** | `npm run build && npm run start` (Next.js standalone + uvicorn prod) | 0.2.0 发布门禁 | **当前正式验收基准** |
| **Desktop-Shell** | Electron / Tauri 本地桌面打包包 | 待 V02-53A 交付 | 桌面原生离线形态验收基准（待 V02-53A 激活） |

### 2.2 端口、PID 与进程树管理机制

为保证测试环境纯净，性能测试套件必须遵循以下所有权与隔离规则：

1. **固定独立端口分配**：
   - 前端 Web 端口：`3100`（避免与常规 3000 冲突）
   - 后端 API 端口：`8100`（避免与常规 8000 冲突）
   - 本地 Redis 端口：`6479`
2. **进程树追踪（Process Tree Tracking）**：
   - 启动测试 runner 时，记录根启动 PID；
   - 在 Windows 环境下，必须递归查询并记录所有子进程（`Node.js`, `Next Server`, `uvicorn.exe`, `python.exe`, `Playwright/Chromium`）；
   - 测试结束时，调用统一的 `taskkill /F /T /PID <pid>` 进程树清理脚本。
3. **数据目录隔离与清理**：
   - 每次性能测试必须使用独立的临时 SQLite 数据库与临时静态资源目录（如 `.temp/perf_run_<uuid>/`）；
   - 测试完成后执行物理删除。若文件被占用导致删除失败，必须将异常向上传播为测试失败。

---

## 3. 标准化基准测试数据集（Standard Benchmark Datasets）

性能测试必须在固定规模的合成/固化数据集上运行，严禁在空数据库或随意变动的数据集上测试：

| 数据集标识 | 规模定义 | 具体内容构成 | 适用测试场景 |
| --- | --- | --- | --- |
| **Dataset-S (小型)** | 1 项目 · 5 页面 | 5 页分镜、10 张 1K 候选图、2 个角色、2 套服装、1 个场景 | 冷/热启动、基础响应时间 |
| **Dataset-M (标准生产)** | 3 项目 · 30 页面 | 30 页分镜、150 张 1K/2K 候选图、8 个角色、15 套服装、10 个场景 | **核心交互默认基准**（拖拽/滚动/切换） |
| **Dataset-L (高压负载)** | 10 项目 · 120 页面 | 120 页分镜、1,000 张候选图 (含 4K 大图)、30 个角色、50 套服装、30 个场景 | 虚拟滚动、长列表、内存泄漏与高压测试 |

---

## 4. 九大确定性核心测试场景（Test Scenarios）

### 场景 1：服务冷启动与首屏加载 (Cold Start & FCP)
- **前置条件**：完全关闭浏览器与服务，释放所有端口与缓存。
- **测试动作**：冷启动 Web-Prod 服务，无缓存打开首页 `/`。
- **测量指标**：服务就绪耗时、First Contentful Paint (FCP)、Largest Contentful Paint (LCP)。

### 场景 2：工作区热导航 (Warm Workspace Navigation)
- **前置条件**：服务与浏览器处于预热就绪态。
- **测试动作**：从首页点击进入拥有 30 页漫画的 Dataset-M 项目工作区 `/projects/{id}`。
- **测量指标**：路由切换耗时 (Route Transition Time)、Interaction to Next Paint (INP)、DOM 节点稳定耗时。

### 场景 3：大量缩略图画廊渲染 (Thumbnail Gallery Virtualization)
- **前置条件**：进入包含 150 张历史候选图的素材库（`library-section`）。
- **测试动作**：以 800px/s 速度向下快速滚动素材列表。
- **测量指标**：掉帧率 (Dropped Frame Rate)、滚动 FPS（目标 ≥ 55 fps）、图片解码导致的主线程卡顿时间。

### 场景 4：分镜格子拖拽重排 (Storyboard Panel Drag & Drop)
- **前置条件**：打开分镜编辑器，页面包含 8 个分镜格子。
- **测试动作**：使用鼠标拖拽第 1 格移动至第 6 格，连续重复 5 次。
- **测量指标**：拖拽交互响应延迟 (Drag Input Latency ≤ 16ms)、重排重排重绘耗时 (Reflow Time)、无闪烁与无错位。

### 场景 5：窗口响应式缩放 (Viewport Resize & Relayout)
- **前置条件**：处于工作区主界面。
- **测试动作**：将视口宽度在 1440px 与 960px 之间往复缩放 10 次。
- **测量指标**：Cumulative Layout Shift (CLS ≤ 0.05)、重排长任务 (Long Task > 50ms) 计数。

### 场景 6：局部重绘 Mask 画笔绘制 (Canvas Mask Painting)
- **前置条件**：打开单页候选局部重绘工作台，加载 2K 画面。
- **测试动作**：在 Canvas 上进行连续笔刷涂抹（500 个点轨迹）。
- **测量指标**：画笔笔触渲染帧率 (Stroke Rendering FPS ≥ 58 fps)、Pointer Event 延迟 (≤ 12ms)。

### 场景 7：单页候选秒级快速切换 (Candidate Flip & Preview)
- **前置条件**：当前页面存在 10 个已生成的 2K 候选。
- **测试动作**：按键盘方向键 `Left`/`Right` 连续快速翻阅 10 张候选。
- **测量指标**：单图解码与上屏延迟 (Image Decode to Display Time ≤ 80ms)、内存无突增溢出。

### 场景 8：连续长页面平滑滚动 (Continuous Page Scrolling)
- **前置条件**：打开整章全景漫游视图（连续排列 20 个完整物理页）。
- **测试动作**：平滑滚动浏览整章（速度 400px/s）。
- **测量指标**：平均 FPS (≥ 55 fps)、掉帧数 ≤ 3、GC 暂停耗时。

### 场景 9：4K 高清大图解码与平移缩放 (4K Pan & Zoom)
- **前置条件**：打开 4K (3840x3840) 最终页面放大查看模态框。
- **测试动作**：执行鼠标滚轮缩放 (100% -> 400%) 与鼠标拖拽平移。
- **测量指标**：GPU 显存占用峰值、Canvas/CSS Transform 帧率 (≥ 50 fps)。

---

## 5. 采样规则、统计方法与度量标准（SLA）

### 5.1 采样与统计规则
1. **预热规则**：每组测试先执行 1 次预热运行（Warm-up Run），数据丢弃不计入统计。
2. **正式样本**：连续采集 $N=5$ 轮完整数据。
3. **度量汇总**：
   - 报告：$Min$、$Max$、$P50$（中位数）、$P95$（第 95 百分位值）；
   - 若 5 轮中出现任何 1 轮失败或报错，整项测试判定为未通过，严禁剔除异常值。

### 5.2 核心性能指标与门禁基准（SLA Targets）

| 度量维度 | 指标名称 | 优秀门禁 (Pass) | 警告阈值 (Warn) | 失败阈值 (Fail) |
| --- | --- | --- | --- | --- |
| **首屏与加载** | FCP (首内容绘制) | $\le 800	ext{ ms}$ | $800 \sim 1200	ext{ ms}$ | $> 1200	ext{ ms}$ |
| | LCP (最大内容绘制) | $\le 1500	ext{ ms}$ | $1500 \sim 2500	ext{ ms}$ | $> 2500	ext{ ms}$ |
| **交互响应** | INP (下次绘制交互延迟) | $\le 100	ext{ ms}$ | $100 \sim 200	ext{ ms}$ | $> 200	ext{ ms}$ |
| | 拖拽/画笔输入延迟 | $\le 16	ext{ ms}$ | $16 \sim 30	ext{ ms}$ | $> 30	ext{ ms}$ |
| **视觉与帧率** | 滚动与动画平均 FPS | $\ge 55	ext{ fps}$ | $45 \sim 54	ext{ fps}$ | $< 45	ext{ fps}$ |
| | 掉帧率 (Dropped Frames) | $\le 3\%$ | $3\% \sim 8\%$ | $> 8\%$ |
| | 长任务 (Long Task > 50ms) 计数 | $\le 2	ext{ 个}$ | $3 \sim 5	ext{ 个}$ | $> 5	ext{ 个}$ |
| **布局与稳定性** | CLS (累计布局偏移) | $\le 0.02$ | $0.02 \sim 0.05$ | $> 0.05$ |
| **内存与资源** | 前端 JS Heap 峰值 (Dataset-M) | $\le 200	ext{ MB}$ | $200 \sim 350	ext{ MB}$ | $> 350	ext{ MB}$ |
| | 50 次翻页后内存增量 (内存泄漏) | $\le 15	ext{ MB}$ | $15 \sim 30	ext{ MB}$ | $> 30	ext{ MB}$ |

---

## 6. 自动化性能验收表模板（待执行 Issue 直接复用）

后续负责性能执行的代理必须直接填写以下标准表，并将实测数据与日志归档：

| 场景编号 | 场景名称 | 数据集 | 目标 SLA (P95) | 实测 P50 | 实测 P95 | Min / Max | 掉帧率 / 内存 | 判定结果 |
| --- | --- | --- | --- | :---: | :---: | :---: | :---: | :---: |
| `SCN-01` | 服务冷启动与首页加载 | Dataset-S | LCP $\le 1.5	ext{ s}$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |
| `SCN-02` | 工作区项目打开与热导航 | Dataset-M | 耗时 $\le 600	ext{ ms}$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |
| `SCN-03` | 缩略图画廊虚拟列表滚动 | Dataset-M | FPS $\ge 55	ext{ fps}$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |
| `SCN-04` | 分镜格子连续拖拽重排 | Dataset-M | 延迟 $\le 16	ext{ ms}$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |
| `SCN-05` | 视口多级响应式缩放 | Dataset-M | CLS $\le 0.02$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |
| `SCN-06` | 2K 局部重绘 Mask 笔刷 | Dataset-M | FPS $\ge 58	ext{ fps}$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |
| `SCN-07` | 单页 2K 候选连续快速翻阅 | Dataset-M | 延迟 $\le 80	ext{ ms}$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |
| `SCN-08` | 20 物理页整章平滑漫游 | Dataset-M | FPS $\ge 55	ext{ fps}$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |
| `SCN-09` | 4K 大图缩放平移查看 | Dataset-L | 显存 $\le 500	ext{ MB}$ | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` | `NOT RUN` |

---

## 7. 未验证边界与环境说明

- `NOT RUN`：本任务为性能测试计划与验收标准制定，**未执行**任何实际浏览器性能压测与实机跑分。
- `NOT RUN`：未启动多进程并发负载与重型数据集压力测试。
- `NOT RUN`：未调用真实外部 AI 供应商网络与计费接口。
- `待激活说明`：`Desktop-Shell`（桌面打包形态）性能基准需在 `V02-53A` 桌面端外壳方案交付并构建后方可正式纳入实测。

---
