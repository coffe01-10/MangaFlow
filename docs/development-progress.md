# MangaFlow AI 开发进度

更新时间：2026-09-04

本文件记录修订版 MVP 计划的实际完成度。

## V02-54B 实现完成：桌面日志导出 + 安全本地文件选择（2026-09-04，Linux box，待审阅验收）

- **对应**：Issue #114 续作 / 分支 `glm/v02-54b-desktop-ux`（基线 `70639a2` / PR #115 合并树）。实现提交：`3d5ebfb`（日志导出 + 本地文件选择）。范围仍限于 `apps/desktop/` 与 docs，`apps/api`/`apps/web` 业务代码零改动；契约 `docs/adr/v02-desktop-shell-evaluation.md` **仍为草案、选型未批准**。
- **①统一日志目录 + 日志导出（ADR §4.5「统一写 logs、按进程分文件、可导出」）**：新增 `shell-core/src/logs.rs`（目录布局 + RunLog + 导出）与 `src/ziparch.rs`（零依赖 store-only ZIP 写入器 + CRC32 已知向量测试）。`handshake.rs` spawn 时把 helper stderr 重定向到 `<user-data>/logs/helper-<token>.stderr.log`（桌面形态下即 API/uvicorn/LOCAL_EXECUTOR Worker 输出；替换 V02-54 之前的 `Stdio::inherit()`——GUI 壳无控制台可继承），壳里程碑（spawn/ready_verified/go_sent/healthy/stopped，仅身份字段）写 `logs/shell-<token>.log`；退出路径 `stop_helper` 记 `stopped`。invoke `desktop_export_logs`（无参 → rfd 原生保存对话框；带 `destination` 走同一校验）产出 ZIP + `manifest.json`，先写 `*.pending` 再原子 rename。**双向路径安全**：成员只收 logs 根内常规文件（符号链接跳过不跟随、逐成员 canonical 包含校验、单文件 64 MiB 上限跳过并记录；**日志轮转未实现**，此为唯一体积护栏）；导出目标须绝对、无 `.`/`..`、父目录存在非链接、canonical 后**不得位于用户数据根之内**（防覆盖用户数据、防归档自我包含）。
- **②本地文件/目录选择**：新增 `shell-core/src/picker.rs` + src-tauri 命令 `desktop_pick_file(kind)` / `desktop_pick_directory` / `desktop_read_picked_file(path)`（rfd 0.17 原生对话框；路径不经 WebView 表单，穿越无输入入口）。策略对齐既有上传边界：原作 TXT/Markdown（同 `sources.py` 后缀集）、素材 PNG/JPEG/WebP（同 `uploads.py REFERENCE_IMAGE_TYPES`），均 ≤ 20 MiB（同 `max_upload_bytes`）；像素/解压炸弹校验仍由 API 服务端执行，壳侧只做前置对齐。校验拒绝 `.`/`..` 成分、拒绝符号链接（含最终成分换名检测）、要求常规文件/目录；通过者进入会话级 `PickedRegistry`（canonical 路径为键的能力表），`desktop_read_picked_file` 每次重查能力表并重跑完整校验，未注册路径一律拒绝；页面取字节（base64）后仍走既有 `/uploads`、`/sources` 接口，服务端边界零改动。触发面为壳自带 `shell/shell-tools.html` 工具页（`build-frontend-static.sh` 随静态导出拷入 `dist/frontend/`，`withGlobalTauri` 已启用）。
- **顺带修复**：V02-54 进度记录声称已入库的 `dist/frontend/index.html` 占位实际不在 `70639a2`（根 `.gitignore` 的 `dist/` 规则整树吞掉 `apps/desktop/dist/`，`apps/desktop/.gitignore` 的 `!dist/frontend/index.html` 反选因父目录被排除而无效），干净 clone 上 src-tauri Windows 编译门禁因此失败（`frontendDist` 不存在）；本轮补根 `.gitignore` `!apps/desktop/dist/` 放行并入库 `index.html` 与 `shell-tools.html` 两个占位。
- **门禁（Linux 可跑部分全绿，2026-09-04）**：shell-core `cargo test` **26 项**全过（协议 5 + ziparch 3 + logs 2 + delivery contract 3 + `tests/log_export.rs` 3 + `tests/picker_policy.rs` 5 + 启动协议集成 5）；日志导出归档另经 **python3 `zipfile` 外部校验**（`testzip()` CRC + namelist + manifest，非写者自证）；`cargo check --target x86_64-pc-windows-msvc` 于 shell-core 与 src-tauri 均过（rustup stable 1.98.1 + llvm-rc 19 用户态解包，新增 rfd 0.17 依赖进锁文件）；`apps/desktop/scripts/run-sidecar-e2e.sh` 假闭环 1 passed（4.45s）；`ruff check apps/desktop` 通过；`tests/test_pytest_collection_gate.py` 通过。
- **NOT RUN / 边界**：rfd 原生对话框 Windows 实机行为（COM 线程模型、与 WebView2 交互）——src-tauri 仅 Windows 目标编译门禁；WebView2 内工具页 invoke 实机验证；日志轮转（ADR D6 记录在案）；Windows 实机 Job Object/WebView2/MSI/NSIS 安装器/签名/自动更新/单实例多开；真实 Redis/RQ 桌面形态；V02-52A N=20；真实供应商。**父项 V02-54 与本子项 V02-54B 均保持未勾**（实现已落地，勾选待 lead 审阅验收）；不勾 V02-55 / V02-52B。

## V02-54B 窗口开启：第一轮已合并（PR #115 / `70639a2`），本轮补日志导出与本地文件选择（2026-09-04，Linux box）

- **合并记录**：V02-54 第一轮（Issue #114 / 分支 `glm/v02-54-desktop-app`）已由 PR #115 以合并提交 `70639a2` 合入 master；该轮交付（`apps/desktop/` 目录策略 A 提升、Windows `CREATE_SUSPENDED`+assign+resume 所有权、用户数据安装/升级/卸载安全契约）与门禁、NOT RUN 边界见下节。**父项 V02-54 保持未勾**；不勾 V02-55 / V02-52B。
- **本轮范围（分支 `glm/v02-54b-desktop-ux`，基线 `70639a2`）**：只补第一轮 NOT RUN 中可在 Linux 落地的两块——①**日志导出**：统一日志目录（壳 + helper/API/Worker 日志，落 user-data `logs/`）由壳侧归档（ZIP）为用户可选路径，归档内容与目标路径均不得穿越用户数据根；②**本地文件选择**：安全文件/目录选择（打开原作/素材等）走壳 API，规范化路径校验 + 会话内已选路径能力表，策略对齐既有上传/原文边界（引用图 PNG/JPEG/WebP ≤ 20 MiB、原文 TXT/Markdown ≤ 20 MiB UTF-8），不引入任意路径遍历。实现与门禁证据见上节（2026-09-04 收尾补记）。
- **沿袭边界**：不杀端口清未知进程；安装/升级/卸载不删用户 DB/素材/凭据；ADR 仍为草案、选型未批准。Windows 实机 Job Object/WebView2、MSI/NSIS 安装器、签名、自动更新、真实 Redis/RQ 桌面形态、V02-52A N=20、真实供应商均 NOT RUN。

## V02-54 桌面交付推进：PoC 提升为 `apps/desktop/`、Windows 挂起创建所有权、用户数据契约（2026-09-04，Linux box）

- **对应**：Issue #114 / 分支 `glm/v02-54-desktop-app`（基线 `804244d`）。契约：`docs/adr/v02-desktop-shell-evaluation.md`（**仍为草案**；本 PR 已按 V02-53B D1–D9 补充 PoC 输入——PoC 建议继续 Tauri、否决条件 3 拿到工作台静态导出风险输入——**选型未批准，待 lead 终批**）。`apps/api`/`apps/web` 业务代码零改动。
- **交付目录策略（二选一中的 A：提升为正式路径）**：`apps/desktop-poc/` 整体 `git mv` 为 `apps/desktop/`，crate 更名 `mangaflow-desktop-shell-core` / `mangaflow-desktop-shell`，协议标识同步由 poc 更名 desktop（runtime 目录 `mangaflow-desktop-<token>/`、env `MANGAFLOW_DESKTOP_*`、tauri productName `MangaFlow` / identifier `com.mangaflow.desktop`）；可回滚性 = 删除 `apps/desktop/` 整目录，业务树零改动（原 PoC 目录不再保留，git 历史可溯 PR #111）。
- **进程所有权（本轮核心修复）**：`apps/desktop/shell-core/src/ownership.rs` Windows 路径按 `scripts/owned_processes.py` `start_python` 纪律重写，替换 V02-53B compile-only 骨架：`CREATE_SUSPENDED|CREATE_NO_WINDOW` 挂起创建 → 建 `KILL_ON_JOB_CLOSE` 根 Job Object → `AssignProcessToJobObject`（子进程仍挂起、未执行任何指令）→ Toolhelp 快照枚举初始线程后 `ResumeThread`；job 创建/assign/resume 任一步失败即终止仍挂起的子进程（fail-closed），V02-53B 的 spawn→assign 竞争窗口收口。壳侧 `RuntimeLayout::create` 在 spawn 之前原子写入归属 journal（`version/token/state=created/shell_pid/created_at`，仅身份字段），比 owned_processes 的「resume 前落盘」更早，可证明运行目录归属。**Windows 实机运行 NOT RUN**：该路径只有 Windows 目标编译门禁证据，实机 D3 复验仍欠。
- **其余必须项**：启动握手（原子绑定 `127.0.0.1:0`、owner token、journal readiness、stdin GO 门控、运行时 API origin 注入——非 `NEXT_PUBLIC_*`、非 Next rewrite）沿用 PoC 冻结协议并在更名后全量复跑；单实例（`tauri-plugin-single-instance`，实机多开 NOT RUN）、启动/退出清理、崩溃清树（Unix PDEATHSIG 链实测 / Windows Job `KILL_ON_JOB_CLOSE` 编译验证）、禁止「杀端口」清理均保持；用户数据安全契约（升级只换程序文件 + Alembic 原地迁移、卸载默认与契约均不触碰 `%LOCALAPPDATA%` 下的 DB/素材/凭据、禁止 NSIS installer hooks 删除、identifier 固定防孤儿化）写入 `apps/desktop/README.md` §5 并由 `shell-core/tests/delivery_contract.rs`（3 项）冻结。
- **门禁（本沙箱可跑部分全绿，2026-09-04）**：shell-core `cargo test` 13 项（5 单元含新增「spawn 前归属 journal」+ 5 集成 + 3 契约）；`cargo check --target x86_64-pc-windows-msvc` 于 shell-core 与 src-tauri 均过（rustup stable 1.98.1 + llvm-rc 用户态解包；另补齐此前未入库的 `dist/frontend/index.html` 占位，Windows 编译门禁现可从干净 clone 复现）；`apps/desktop/scripts/run-sidecar-e2e.sh` 真实 API + 假通道闭环 1 passed（4.43s：28 个 Alembic 迁移 + 生成→候选→采用→PNG 落盘可取，零供应商外呼）；`ruff check apps/desktop` 通过；`tests/test_pytest_collection_gate.py` 通过（默认 pytest 收集不进 `apps/desktop`）。
- **NOT RUN / 未完成（V02-54 保持不勾的原因）**：Windows 实机 Job Object 行为（挂起创建路径仅编译验证）、WebView2 Evergreen 渲染兼容与缺失安装、MSI/NSIS 安装/升级/卸载实机（含数据保留实测）、代码签名/SmartScreen、自动更新签名服务器、真实 Redis/RQ 桌面 worker 形态、V02-52A N=20 性能门禁、真实供应商（假通道零外呼）；roadmap V02-54 要求中的「日志导出、本地文件选择」UI 尚未实现，留待后续轮次。

## PR #113 zcode-audit 生产审计修复合并（2026-09-04）

- **合并记录**：分支 `zcode-audit` / 合并提交 `804244d`；分支内先与 PR #112（`b97dc80`）汇合（merge `d4a1ebe`）。以 merge commit 合入（非 squash），未 force-push。
- **修复方向（节选，详见 PR #113 描述）**：工作流/候选不变量、审批门槛与 lineage（repair/upscale）；Job lease 回收、CLI slot 与 adapter 未分类异常收敛；本地信任边界（host allowlist、codex 路径钉扎、图像上限）；版本检查原子化、遗留唯一约束清理、草稿 flush/采纳失败可见性；场景表单、源编辑器与本地编辑的隐藏失败暴露（`742cd2a`）；两轮审查修复收口（`a7ba210`）。第三轮大审查因额度收窄未做。

## PR #112 inspect/parse/cancel 完整性加固合并（2026-09-04）

- **合并记录**：合并提交 `b97dc80`（PR #112）。
- **行为修复（详见 PR #112 描述）**：PAGE_INSPECT 失败不再把已采纳 READY 候选盖章为 FAILED/STALE，失败 inspect 任务可重试；分页完成后再解析被拒绝（HTTP 409）；工作流解析复用 READY 剧本而不再清空 Scene 行；SOURCE_PARSE 活跃期间 parse/plan/源修订互斥；工作流取消先认领 CANCELLED 再按 `workflow_run_id` 取消迟到的 inspect 任务；keep-selected 语义为「待检查」而非「审查失败」；任务实际上传参考图期间禁止删除剧本。
- **门禁**：`npm run check` 通过（pytest 662 passed / 34 skipped、Vitest 418、Next 生产构建）。

## V02-53B 桌面壳可丢弃 PoC 完成：Tauri 2 先验、启动协议/进程归属/假闭环实测（2026-09-04，Linux box）

- **合并记录（2026-09-04）**：Issue #110 / PR #111 / 分支 `glm/v02-53b-desktop-shell-poc` / 实现 head `3909729` / 合并提交 `203efad`；审查 P2 硬化随 PR #111 收口后合并。

- **对应**：Issue #110 / 分支 `glm/v02-53b-desktop-shell-poc`（基线 `6f8a40d`，PoC head `3909729`）。契约 `docs/adr/v02-desktop-shell-evaluation.md`（V02-53A，**仍为草案**）。PoC 全部落于 `apps/desktop-poc/`（可丢弃目录），`apps/api`/`apps/web` 业务代码零改动；前端改动仅以可丢弃补丁（`patches/web-static-export.patch`）在一次性 worktree 验证，不合并。**不构成选型批准**，D1–D9 矩阵与复现命令见 `apps/desktop-poc/README.md`。
- **启动协议（ADR §4.2/§4.4 冻结项）**：壳生成 32 位 owner token + `runtime/mangaflow-poc-<token>/` 运行目录，直接 spawn helper；helper 原子绑定 `127.0.0.1:0`（无 TOCTOU）→ `alembic upgrade head` → 原子写 journal（仅 token/pid/port/origin/状态身份字段）→ stdout `MANGAFLOW_READY`；壳校验 token/PID/journal/回环 origin 后才发 `MANGAFLOW_GO`（错误 token exit 75；GO 前 socket 零字节服务，有断言）；WebView 在握手全部通过后才创建，origin 以初始化脚本同步写 `window.__MANGAFLOW_API_ORIGIN__` + invoke `poc_get_api_origin` 双通道注入，不依赖 `NEXT_PUBLIC_*` 与 Next rewrite。
- **证据（Linux 沙箱可跑部分全 RUN）**：① shell-core `cargo test` 9/9（协议校验 4 + 集成 5：握手全链、错误 GO 拒绝、并发双 helper 端口必异、`shell-sim` SIGABRT 崩溃后 helper+孙进程经 PDEATHSIG 链全灭、无协作者 SIGTERM→SIGKILL 升级）；② src-tauri 过 `cargo check --target x86_64-pc-windows-msvc`（llvm-rc 经 apt download+用户态解包提供；Job Object `KILL_ON_JOB_CLOSE` 代码编译验证）；③ 真实 API 假模型闭环 e2e 4.2s：`app.main:app` + 28 个 Alembic 迁移 + SQLite 落 user-data + 无 Redis 时 API 内 LOCAL_EXECUTOR（安装版默认形态）+ 假通道（复用 `app.worker_tasks._adapter` 验收缝，目录种子走生产 `credential_crypto` AES-GCM+文件主密钥路径），经 HTTP 完成 项目→导入→解析→角色锁定→参考/服装/画风→analyze→调色板→complete-sheet→候选 READY（approve-reference 200 探针）→新资产 PNG 可下载，`/jobs/{id}/model-call-attempts` 留痕，零真实供应商外呼；④ PyInstaller onedir 冻结 sidecar（116MB）冒烟全过（包内 Alembic→握手→GO→健康→dashboard API 200→优雅退出；`alembic.ini`/`migrations` 必须入 `_internal/` 是打包形态硬约束证据）；⑤ D5 浏览器级（Chromium）：静态导出页加载 + 注入 origin + 仪表盘直连动态端口 API（`/api/v1/projects/dashboard` 200，CORS 按桌面 origin 放行），静态服务器 `/api/*` 零命中、零页面错误。
- **D5 核心发现（否决条件 3 输入）**：工作台子树无法只靠 flag 静态导出——`output:"export"` 要求每个动态段 ≥1 预渲染组合（真实项目 id 构建期不可知）且工作台组件树服务端预渲染崩溃；补丁以「poc 桩组合 + notFound stub + 删 3 个仅服务端页」换得壳级页面（仪表盘/设置/帮助）导出。方案 B（捆绑 node 跑 `next start` 保留 rewrites）未在本 PoC 验证。
- **NOT RUN / 边界**：Windows 实机全链路（Job Object 运行时行为——生产壳须按 `owned_processes.py` 的 CREATE_SUSPENDED+assign+resume 收口 spawn→assign 窗口、WebView2 Evergreen 渲染兼容与缺失安装、MSI/NSIS 安装器、SmartScreen/签名、自动更新、单实例多开）；RQ/Redis worker 进程形态与 Independent Worker（按 Issue 约束不装 Redis/Docker/Postgres；PostgreSQL live 沿既有边界）；V02-52A N=20 性能门禁/LH/FPS（归 V02-52B，PoC 仅记录参考值 e2e 全程 4.2s ≪ 15s 冷启动建议线，非固定窗口测量）；真实供应商、真实凭据、Electron 对比壳（未构建镜像实现，体积/内存对比无从测起）。ADR §3.1 否决条件逐项核查表在 `apps/desktop-poc/README.md` §4——**均不足以当场否决或放行，最终选型由 lead 复核决定**。

## V02-51B 桌面视觉系统已实现：token/焦点/动效收口 + 模板 B/C 壳 + U1–U10 矩阵（2026-09-03，Linux box）

- **合并记录（2026-09-04）**：Issue #108 / PR #109 / 分支 `glm/v02-51b-desktop-visual-system` / 合并提交 `6f8a40d`；父项 V02-51（A/B）随本合并完成并在 roadmap 勾选。
- **对应**：Issue #108 / 分支 `glm/v02-51b-desktop-visual-system`（基线 `d472f6e`，实现 head `7c344db`）。契约 `docs/v02-desktop-workspace-ux-audit.md` §3/§4/§8/§11/§13/§14；不改 V02-11 供应商内部（`provider-management.tsx` 零改动），QueueDock 行为不变，相对基线 diff 仅 `apps/web` 与 `docs`。
- **切片 1（token 与焦点/动效收口，§11）**：`globals.css :root` 补齐设计 token——状态色 `--danger/--muted/--success-bg/--warning-bg/--danger-bg/--focus`（其中 `--muted`、`--mono` 此前被组件引用但从未定义，本切片修复）、`--mono` 等宽族、字号级谱 `--text-display/title/body/ui/meta/micro`（最低 11px）、4px 间距谱、z 刻度（base/sticky/dock/overlay/dialog/lightbox）、`--radius: 3px`、`--shadow-card/pop`、`--ease-out`、`--duration-fast/base`；稳定化地板（body 14px、控件 12px/40px、按钮 44px）与卡片阴影、步骤缓动、sticky/dock 层级、按钮圆角改为同值 token 引用，不改布局语义。散落的四段（连同 V02-31B 段共五段）`prefers-reduced-motion` 合并为一条全局契约：动画/过渡 .01ms、iteration 1（spinner 可停）、hover 位移 `transform: none` 清单覆盖按钮图标/工作台步骤/对话卡/设置页悬停，并删除审计点名的诊断刷新按钮 `rotate(-4deg)` 装饰。`focus-visible` 统一为唯一 3px vermillion 焦点环：删除工作台步骤 1px inset、`pkg-slot-card` 2px ink、director/local-edit `outline-offset: 2px` 变体、usage 表格行 `outline: none`。
- **切片 2（模板 C 设置壳，§4.3）**：`.settings-board`/`.project-settings-grid`/`.checks-section` 单列断点由 900px 提到 1279.98px，诊断/存储侧栏改随页滚动（`position: static; max-height: none; overflow: visible`），不再粘滞挤压主卡；≥1280 保持主栏+粘滞侧栏双列。设置控件字号 ≥12 由全局地板（`--text-meta`）保证。
- **切片 3（模板 B 工作台槽位，§4.2/§5）**：左导航新增 48px 图标轨折叠——顶栏 `workspace-nav-collapse` 切换按钮（仅 >760px 显示，不进 760 汉堡），`mangaflow.project-sidebar-collapsed` 持久化，rail 下隐藏标题/文字/拖宽把手、保留图标与当前步骤高亮；新增通用右槽 API `components/project-workspace/workspace-slots.tsx`（`WorkspaceInspectorSlot`：≥1280 以 `has-inspector` 三列网格停靠、900–1279 右抽屉、<900 底部抽屉，抽屉关闭即消失只发生在非停靠区）；侧栏拖宽边界收口为 `lib/workspace-layout.ts`（188–360，默认 214）供拖宽与存储恢复共用。storyboard 检查器既有抽屉断点（1100–1279 右抽屉 / 900–1099 底部抽屉）保持并被回归断言。
- **切片 4（lightbox/缩略图/窗口化夹具，§2.4/§7）**：`ImageLightbox` 补 Esc 关闭、＋/− 键缩放（50%–250%）、Tab 焦点陷阱、打开时焦点进入对话框并在关闭后归还触发缩略图；新增 `originUrl`（`lib/api.ts`）使 lightbox 一律取原图 content，网格/列表继续走 `publicUrl`（改写 `/thumbnail/640`）——修复了此前 lightbox 与网格同样只拿到 640 缩略图的偏差；落实入口共五处并通过清扫守卫防再犯：`CandidateArtwork`（shared.tsx）、`asset-production-panel.tsx` 的 `CandidatePreview`（full=originUrl / thumbnail=publicUrl）、`generate-section.tsx` 场景继承参考图、`scene-workspace.tsx` 主空间与变体参考图两处、审查后续扫出的 `jobs-section.tsx` 任务结果入口；源码守卫断言任何组件不得把 `publicUrl` 结果直接交给 `openPreview/onOpen`，另有 jobs-section 组件级回归（点击「查看结果」断言 lightbox 收到 `/content` 原图而非缩略图）。候选卡加 `content-visibility: auto`（`contain-intrinsic-size` 460px）作 V02-52 前的低成本窗口化过渡；`lib/windowing-rules.ts` 固化阈值接缝（候选 24 / 库缩略图 60 / 任务行 80），本轮无消费方、不构成行为变化。
- **门禁（实现 head `7c344db` + 审查修复至 `5df067e` 之后，Linux 等价 `npm run check`）**：Web Vitest 43 文件 413 项全过（新增 `app/desktop-visual-system.test.ts` 与 `components/project-workspace/workspace-chrome.test.tsx`，更新 storyboard S17 至合并后的单块 reduced-motion 契约，保证不弱化；审查轮补充 reduced-motion 交互态守卫、lightbox 原图入口清扫、jobs-section 原图组件回归与模板 B 图标轨+右槽组合规则断言）；ESLint、`tsc --noEmit`、Ruff（`apps/api tests`）、供应商平权扫描（`VERTEX_NATIVE`/`vertex-ai`/`vertex_configured` 三模式 git grep vs allowlist）0 违规、Next.js 生产构建通过。全量 pytest 588 passed / 70 skipped，10 failed + 10 errors 为既有 Linux 平台基线（Job Object、junction/reparse、PowerShell 包装、CLI 可执行文件更新等，集合与 V02-44B 记录一致）；本分支未改动任何 Python 文件，失败集合由基线树决定。
- **U1–U10 矩阵（§13）证据**：
  - **U1 RUN**：`desktop-visual-system.test.ts`「U1 ≥1280 工作台」断言 `.workspace-canvas` 保留 `padding: 28px 30px 70px`、`.queue-dock` 高 48px 且 `z-index: var(--z-dock)`（主栏不被底栏遮挡的既有回归）；左导航停靠为默认，可折叠图标轨持久化断言同文件。
  - **U2 RUN**：断言汉堡抽屉规则（`.workspace-left { position: fixed`）仅存在于 `@media (max-width: 760px)`，761px/1280px/900–1279 各块均无；storyboard 检查器 1100–1279 右抽屉与 900–1099 底部抽屉的 `.drawer-open`/`:not(.drawer-open) { display: none }` 契约、通用右槽同断点降级均有断言（900–1279 检查器是抽屉而非消失）。
  - **U3 RUN**：断言 ≥1280 `.settings-board` 双列基础规则与 `.settings-secondary` sticky；`--text-meta: 12px` 与最终地板 `button, input, select, textarea { … font-size: var(--text-meta) !important }`。
  - **U4 RUN**：断言 `@media (max-width: 1279.98px)` 内 `.settings-board { grid-template-columns: 1fr }` 且 `.settings-secondary { position: static; max-height: none; overflow: visible }`（诊断不粘死）。
  - **U5 RUN**：断言全表唯一一条 `:focus-visible` outline 规则 = `outline: 3px solid var(--vermillion); outline-offset: 3px`，无 1px/2px 变体、无 `outline: none` 吞键盘焦点；全局导航/步骤/主按钮均落在该规则（`globals-style.test.ts` 既有断言继续通过）。
  - **U6 RUN**：断言 `prefers-reduced-motion` 全表仅一段；`.01ms` 冻结 + iteration 1（spinner 可停）；交互态位移取消用 `transform: none !important` 覆盖全部 26 个 translate 源——hover（含 `.button:hover:not(:disabled)` 与 tactile 块的 `button:hover:not(:disabled)` / `a.button:hover` 两个变体，该块位于合并块之后，无 `!important` 会按优先级反超）、按压（`button:active:not(:disabled)` / `a.button:active` / 工作台步骤 active）、拖拽（`.upload-stage.drag-active`）；U6 断言以行首精确匹配区分元素/class 变体（class 变体不能顺带满足元素断言），并有解析器守卫测试逐个核对全表 `:hover`/`:active`/`drag-active` + translate 选择器 ⊆ 覆盖清单；`rotate(-4deg)` 全表消失。
  - **U7 RUN**：`workspace-chrome.test.tsx` 组件测试——Esc 关闭 lightbox、关闭后焦点回到触发按钮、＋/− 缩放至 50%–250%、Tab 焦点陷阱、`局部修改` 候选入口保留。
  - **U8 RUN**：QueueDock 组件测试——隐藏写入 `mangaflow.queue-dock-hidden`，隐藏态重挂载仅显示恢复按钮，点击恢复并回写 `false`。
  - **U9 RUN（纯函数级）**：`clampSidebarWidth`/`storedSidebarWidth` 边界测试（188–360、非法/越界回退 214）+ 源码断言 `project-workspace.tsx` 拖宽与恢复消费同一实现。指针拖拽的 PointerEvent capture 序列 jsdom 不可行，未做浏览器级拖拽。
  - **U10 RUN（回退栈断言级）**：断言 `--sans/--serif/--mono` 均以通用族（`sans-serif`/`serif`/`monospace`）收尾，未加载 Noto 时回退系统字体可读。正式字体加载（next/font/@font-face）本切片未做，见 NOT RUN。
- **NOT RUN / 边界**：Playwright/Axe 浏览器 E2E（官方 owned 控制器依赖 Windows Job Object，本 worktree 亦无既往 Linux 等价 harness，未自行搭设）；真实视口截图对比/视觉快照回归；next/font 正式字体加载与实机字体回退观察（本沙箱无字体源，避免破坏离线 `next build`）；Lighthouse/FPS/V02-52A N=20（按约不做）；真实供应商、真实 Key、付费图片冒烟；PostgreSQL live、Redis、Docker、Independent Worker；Tauri/Electron 选型（V02-53）。窗口化阈值本轮仅固化接缝，未在真实数据集上启用（留给 V02-52 门禁决策）。

## V02-11C 设置页验收已审阅合并（2026-09-03）

- **合并记录**：Issue #106 / PR #107 / 分支 `glm/v02-11c-settings-acceptance` / 合并提交 `d472f6e`（验收 head `3335fa6`；`d472f6e` 相对 `3335fa6` 仅增加文档与合并提交，apps/tests/scripts 代码树一致）。父项 V02-11（A/B/C）随本合并全部完成。
- **验收证据**：沿用下节「V02-11C 设置页验收完成」记录——Issue #40 §8 P/C/F/V/A/S/L/N 矩阵逐行映射到 `provider-management.test.tsx` 72 项定向回归（head `3335fa6` 行号覆盖表见下节）；完整 Web Vitest 41 文件 381 项全过；ESLint、`tsc --noEmit`、Ruff、供应商平权扫描 0 违规与 Next.js 生产构建通过。
- **假供应商设置页 E2E（RUN）**：2026-09-02 Windows owned Playwright 17/17（`98b93a0`）与本分支 2026-09-03 Linux 等价 harness 重跑 17/17（head `3335fa6`，run id `fd48ff8c0995416ead5efae033afd312`）均含「设置页用离线假供应商完成创建、手工模型与展示显隐」与含 `/settings` 的 Axe 检查。
- **NOT RUN / 边界**：真实供应商、真实 Key、付费图片冒烟、PostgreSQL live、Lighthouse/FPS、V02-51B 视觉回归。

## V02-11C 设置页验收完成：Issue #40 矩阵逐项核对 + 假供应商 E2E 全绿（2026-09-03，Linux box）

- **对应**：Issue #106 / 分支 `glm/v02-11c-settings-acceptance`（基线 `071ae2d`，验收 head `3335fa6`）。对照 `docs/v02-provider-settings-ui-audit.md` §8 把 P/C/F/V/A/S/L/N 每一行映射到 `apps/web/components/provider-management.test.tsx` 定向回归，全部有证据；审计 §8 要求保留的三条既有用例（健康错误可见、创建失败/刷新、凭据失败不回显）在 `:207/:216/:241`。
- **缺口补齐（本轮新增，全部假供应商，不调真实 API）**：C2 补「连接测试完成 · 12 ms」中文成功状态含延迟、`CREDENTIALS` 仅调用一次且不触发 discover 断言（`:259`）；新增 V3「已隐藏的已验证模型不出现在默认列表，仅已验证也不带回，开启显示已隐藏才可见且标已隐藏」（`:768`）；新增 S5「服务端脱敏格式 `••••9876` 原样显示、完整密钥不出现」（`:804`）。设置页定向回归由 70 项增至 72 项。
- **矩阵覆盖表（head `3335fa6` 行号，`provider-management.test.tsx`）**：P1–P13＝`:835/:847/:867/:883/:923/:938/:954/:968/:988/:995/:1013/:1030/:1047`（连接启停带版本 `:488`）；C1＝`CONNECTION_KEY` 密钥表单（`:787` 夹具）＋`ENV_SERVICE_ACCOUNT`/`CLI_SESSION` 按 `credential_source` 渲染、协议字符串不参与判定（`:298/:313`）；C2＝`:259`；C3＝`:241`；C4＝`:525`＋`:667`；C5＝`:511`；C6＝`:684`；C7＝取消不发请求 `:716`＋确认后 `acknowledge_cost: true` `:536`；C8＝`:731`；C9–C13＝`:1415/:1427/:1442/:1451/:1468`（纯函数 `:1479`）；F1–F9＝`:1132/:1142/:1149/:1156/:1164/:1173/:1188/:1199/:1213`，F6b＝`:1122`（搜索/筛选纯函数 `:1227`）；V1＝`:598`，V2＝`:629`，V3＝`:768`，V4＝`:745`；A1–A9＝`:1274`–`:1346`；S1–S4＝`:787`（错误 DOM 无明文另有 C3 `:241`），S5＝`:787`＋`:804`；L1–L5＝`:1368/:1376/:1386/:1396/:1404`；N1–N4＝`:1499/:1507/:1516/:1523`（按审计 §8 前言允许的 class/CSS 源断言，非真实视口渲染）。另有设置页无 Vertex 专属查询/卡片/硬编码模型（`:660`）与 V02-14A/B/C CLI 凭据源用例（`:335/:409/:450`）。
- **门禁（Linux 等价 `npm run check`，head `3335fa6`）**：Web Vitest 41 文件 381 项全过；ESLint、`tsc --noEmit`、Ruff（`apps/api tests`）、Next.js 生产构建通过；供应商平权扫描（`VERTEX_NATIVE`/`vertex-ai`/`vertex_configured` 三模式 git grep vs allowlist 10 路径）0 违规。本分支未触及 API 代码，未重跑 Python 全量 pytest（既有记录中的 Linux 平台基线失败与本分支无关）。
- **假供应商设置页 E2E（RUN）**：本分支 head `3335fa6` 以与 V02-10D 相同的 Linux 等价 harness 独占端口/一次性 runtime 重跑全套 Playwright **17/17 全绿**（run id `fd48ff8c0995416ead5efae033afd312`，52.0s；platform-v2 5/5，含「设置页用离线假供应商完成创建、手工模型与展示显隐」——断言 verify/discover/balance 零调用——与含 `/settings` 的 Axe serious/critical 零违规；critical-behaviors 4/4、scene-workspace 1/1、usage-dashboard 7/7）；runtime 目录已删除、端口 3000/8000 复查释放。Windows 完整形态 owned E2E 本轮未重跑，引用既有 `98b93a0`（`LAPTOP-TV9KT8RC`，17/17，platform-v2 5/5 含同一设置页用例）。
- **NOT RUN / 边界**：真实供应商、真实 Key、付费图片冒烟、PostgreSQL live、Lighthouse/FPS、V02-51B 视觉回归。

## V02-10D M14 离线浏览器 E2E 已全绿，V02-10D 勾选（2026-09-03，Linux box）

- **合并记录**：Issue #104 / PR #105 / `glm/v02-10d-m14-acceptance` / 合并提交 `071ae2d`（验收 head `23faaf0`；`071ae2d` 相对 `23faaf0` 仅增加文档与合并提交，apps/tests/scripts 代码树一致）。
- **入口与代码状态**：官方控制器 `scripts/run_e2e_owned.py` 依赖 Windows Job Objects（`scripts/owned_processes.py` 在非 Windows 直接拒绝），本机为 Linux box，故按 Issue #104 采用 **Linux 等价入口**：`.venv/bin/python output/playwright/linux-m14/m14_linux_harness.py run`（gitignore 的未跟踪 harness，未改仓库脚本），逐项复刻 owned 控制器契约：独占端口 3000/8000 预检、一次性 runtime 目录（新 32 位 run id + `runtime-owner.json` 标记，canonical 校验后 API 才写库、拒绝复用已有数据库）、`MANGAFLOW_DISABLE_DOTENV=1`、`NODE_OPTIONS --require scripts/e2e_node_bootstrap.cjs`、隔离 SQLite 由 Alembic 升到 `20260903_28` + 假模型种子（`MANGAFLOW_E2E_SEED=1` → `seed_gate_projects`）、`QUEUE_ENABLED=false`；命令与控制器一致（`next build apps/web` → 全套 Playwright，Chrome channel，自建 web/API 服务器，`reuseExistingServer: false`）。
- **两轮结果**：①首轮（head `2ca22d6`，run id `eb58868707c34c239010bc730eb45cf8`）16/17，唯一失败为 Axe `color-contrast`（serious）：`/projects/{id}/assets/characters` 的 `.pkg-create-row > small`（`character-package-workspace.tsx:551`；`globals.css:1870`）`#756f65`/`#f4f1e9` = 4.41:1 < 4.5:1，确定性失败；该元素随 V02-23B（PR #86）合入、晚于上一轮 Windows E2E 证据 SHA `98b93a0`。②经测试总管 review 授权，`23faaf0` 仅将该一处选择器改为 P1-3 基线安全灰 `#66604f`（约 5.55:1），重跑全套 **17/17 全绿**（head `23faaf0`，run id `9814af94c60244ae9e311c113360d032`，51.2s，`platform-v2` 5/5、critical-behaviors 4/4、usage-dashboard 7/7、scene-workspace 1/1；Axe 用例 8.7s 通过）。
- **配套证据（两轮均复核）**：T7 别名夹具 `generate-section.test.tsx` Vitest 15/15 通过（Phase C/D 后仍可解析）；供应商平权扫描（`git grep -n -I -F` 三模式 vs `scripts/provider-neutrality-allowlist.txt` 10 路径）**0 违规**、无新增产品偏置；前端无 `vertex-status`/`verifyVertex` 产品请求（唯一命中是 `provider-management.test.tsx:660` 的 `not.toContain("verifyVertex")` 负向断言）；`/settings/vertex/status|verify` 仅后端兼容端点保留，与 Phase E 记录一致；ESLint、`tsc --noEmit`、Ruff 通过。
- **清理**：两轮 runtime 目录与隔离 SQLite/种子数据均已删除（`runtime_removed: true`）；端口 3000/8000 复查已释放；退出收尾只停止经 `/proc/<pid>/environ` run id 归属校验的本 run 监听，非本 run 资源只记录不触碰；日志与 trace 保留在 `output/playwright/linux-m14/`（gitignore）。
- **NOT RUN / 边界**：真实供应商、Lighthouse/FPS、真实 PostgreSQL；Windows 完整形态 `npm run test:e2e`（PowerShell/Job Object 入口）本机不可运行，本次为 Linux 等价入口（差异仅为进程树收容方式，测试与服务器契约不变）；M14 契约中的 C/D 各一轮要求，D 轮以本轮两轮记录为准，C 轮沿用既有 Windows `98b93a0` 记录。

## V02-44B 能力验收 fail-closed 门禁已审阅合并（2026-09-03）

- Issue #102 / PR #103 / `glm/v02-44b-capability-acceptance` / 合并提交 `e2126a1` / head `84b5dc7`。新增 `apps/api/app/services/model_capabilities.py`：`accepts_explicit_mask`、`supports_instruction_region_edit`、`preserves_outside_region`、`whole_image_reference_only` 四位统一 fail-closed 读取（缺失/UNKNOWN 一律按不支持，绝不猜成 true），带 `DECLARED / DISCOVERED / VERIFIED` 来源语义与区域编辑表面分类；`GET /api/v1/models` 与 `ModelCapabilityRead` schema 同步暴露四位布尔与来源。
- 预设诚实声明：Vertex 原生两张图片模型与三个 CLI 图片模型按适配器现状只声明 `whole_image_reference_only`，不假装有 mask/instruction 请求面；发现/手工模型的未知能力位保持不声明。区域请求打到不匹配表面在调用前确定性 `UNSUPPORTED_CAPABILITY`：导演 `regenerate_region` accept 路径无 Job/派生候选/mask 资产/attempt/付费；Worker 侧 `page_generate` 在 `_invoke_provider` 前重查 `accepts_explicit_mask`，不自动换模型/provider、不降级整页 `generate`；前端局部编辑器屏蔽态如实列出已启用编辑模型的声明表面（中文标签）并保留取消出口，不隐式整页 POST。
- 门禁（`84b5dc7`，Linux 隔离）：新增 `tests/test_capability_acceptance.py` 7 项（/models 序列化、预设逐位、导演表面拒绝零副作用、Worker 能力失败无 attempt/产物、正向对照、取消）；全量 588 passed / 70 skipped（10 failed + 10 error 为既有 Linux 平台基线，已在干净基线 `047571b` 复现，非本分支引入）；Vitest 41 文件 379 passed；ESLint、`tsc --noEmit`、Ruff、Next.js 生产构建通过。
- NOT RUN：真实供应商调用与 `VERIFIED` 来源实测、真实 mask/inpaint、计费核对、M5 未决 attempt 补偿、M6 双 Worker 租约、PostgreSQL live（本分支无迁移）、Playwright E2E / Lighthouse / FPS（本轮留给 V02-10D M14）。

## V02-43B 局部选区 UI 与并排比较已审阅合并（2026-09-03）

- Issue #100 / PR #101 / `glm/v02-43b-local-edit-ui` / 合并提交 `047571b` / head `517191f`。生成台新增局部编辑工作区（`apps/web/components/project-workspace/local-edit-workspace.tsx` + 纯规则模块 `apps/web/lib/local-edit-rules.ts`）：mask 选区绘制并只构造 `regenerate_region` 结构化 envelope 走 propose→accept，绝不静默调用 `generateCandidate` 整页重生（Vitest 断言 propose-only）；派生候选按 `REGION_REGENERATED` 批次与 `prompt_snapshot.lineage` 与父候选并排比较。
- 空 mask 调用前门禁；模型目录缺 `accepts_explicit_mask` 一律 fail-closed（选择器只列声明能力且已启用的 `image_edit` 模型，不匹配时禁用 + 中文原因 + 取消出口）；父候选当前采用保持不变。
- NOT RUN：真实供应商、真实 mask/inpaint、Playwright、图像相似度。

## V02-42B 局部重抽卡候选链已审阅合并（2026-09-03）

- Issue #98 / PR #99 / `glm/v02-42b-candidate-lineage` / 合并提交 `436f47d` / head `4009c56`。新增 `candidate_lineage` 血缘表（迁移 `20260903_28`，历史 REPAIR/UPSCALE 候选只读回填血缘行）；`regenerate_region` accept 在同一事务创建服务端 mask 资产、`REGION_REGENERATED` 派生候选与 `PAGE_REGION_REGENERATE` Job，enqueue 在事务提交后执行；propose 预览全程 savepoint 内回滚、绝不入队。
- 父候选零改动：不覆盖原图、不整页重生，当前采用候选不变，派生候选仍走既有 select-candidate 人工采用门槛；prompt_snapshot 只增补 `lineage` 段，历史 provider/model ID 不改写。无 mask 的 `REGION_REGENERATED` 调用前 422；目录模型缺 `accepts_explicit_mask` 能力位返回确定性 `UNSUPPORTED_CAPABILITY`；缺父候选/父已删/父无图/未识别模型/分辨率不支持全部调用前失败，无 Job、无付费。
- NOT RUN：真实供应商、真实 mask/inpaint、PostgreSQL live（迁移回填仅 SQLite 验证）、Playwright。

## V02-41B 生成台导演模式已审阅合并（2026-09-03）

- Issue #96 / PR #97 / `glm/v02-41b-director-workspace` / 合并提交 `39cf6e4` / head `36437df`。生成台新增导演模式：作用域芯片、命令栏、结构化 diff 预览卡与命令历史（`apps/web/components/project-workspace/director-workspace.tsx` + `use-director-workspace.ts`），前端规则桩 `apps/web/lib/director-rules.ts` 把自然语言输入转为 V02-40 结构化命令并接 `/director` API；「在选区编辑」mask 入口保持禁用，导演台不静默整页重绘。
- NOT RUN：真实 LLM 解析（propose 只接受结构化 `commands[]`）、mask 选区绘制、真实供应商。

## 2026-09-02 Windows owned Playwright E2E 与性能门禁

- 机器：Windows 笔记本 `LAPTOP-TV9KT8RC`。SHA：`98b93a0`（分支 `fix/pr82-remaining-package-p1s`；该 SHA 是 `master`/`f961774` 的祖先；E2E/perf **未**跑在 `c93e1b3`）。官方控制器：`scripts/run_e2e_owned.py`。Windows 上无产品代码改动。未跟踪的 `output/` 原样保留。未安装 Windows 服务。
- **Playwright E2E PASS**：17 passed / 0 failed / 0 skipped，102.95s（19:30:35–19:32:18 CST）。套件：critical-behaviors 4、platform-v2 5、scene-workspace 1、usage-dashboard 7。日志：`D:\自媒体\漫画工作流\output\playwright\owned-1f37aee8cc2b40719cffa6e18d6731de\`。
- **Performance PASS**（脚本 2 轮，**不是** V02-52A N=20）：`run_e2e_owned.py performance` 146.063s（19:33:41–19:36:07 CST）。4/4 门禁（LH1、LH2、FPS1、FPS2）。FPS round1 avg 143.75 / 1%low 140.85；round2 avg 143.85 / 1%low 140.85。Lighthouse 路由 `/`、storyboard、generate、workflow、settings：perf 89–98、a11y 92–100、BP 96–100。日志：`output/playwright/owned-349cb52c1fa94502803935fc849ae8ac/` 与 `output/playwright/phase2/349cb52c1fa94502803935fc849ae8ac/`。
- **Independent Worker live BLOCKED**：pytest 未启动。笔记本无 Docker/PostgreSQL/Redis；55432/56379 关闭。用户明确不安装。7 项 `test_live_independent_worker_*` 仍为 **NOT RUN**（不是假 skip）。

## 2026-09-02 Linux 隔离 PostgreSQL / Redis 验收

- 起始 SHA：`f961774`（`origin/master`，已含 PR #82 / #83 / V02-22B）。工作分支 `env/postgres-redis-acceptance`。
- 隔离服务（未改 `docker-compose.yml` 的 5432/6379）：PostgreSQL 16 `127.0.0.1:55432` / `mangaflow_acceptance`；Redis 7 `127.0.0.1:56379` DB 15，口令 `mangaflow-dev`。驱动：生产依赖新增 `psycopg[binary]==3.3.5`。
- Live 入口：`pytest tests/integration --run-live-integration --pg-url postgresql+psycopg://mangaflow:mangaflow@127.0.0.1:55432/mangaflow_acceptance --redis-url redis://:mangaflow-dev@127.0.0.1:56379/15`。
- **PostgreSQL：18 passed**（约 26.6s）。覆盖 schema/Alembic 邻接清理、用量账本升降级与并发 finalize、对账重叠串行、行锁、批次/候选序号、工作流并发发布与 409 耗尽恢复、事务回滚、关闭批次拦截，以及 PKG-S14 七项（两阶段 FK 往返、已发布包拒绝降级、部分唯一索引与 RESTRICT、`FOR UPDATE` 包锁、并发 publish 单赢家、archive v2 vs activate v2、并发资产绑定单赢家）。
- **Redis/RQ：8 passed / 7 skipped**（约 17.2s）。通过：连接隔离、SimpleWorker 入队执行、P1-9 入队不覆盖 GENERATING、P1-11 槽位延迟、可重试/终态失败、租约过期恢复、取消保护、邻接命名空间清理。7 项 `test_live_independent_worker_*` 在非 win32 明确 skip（Windows Job Object 独立 Worker 进程矩阵仍 NOT RUN）。
- 真实失败修复：OCR 迁移 `ocr_enabled != 0` 在 PostgreSQL boolean 上失败，改为 `Boolean.is_(True)`；`enqueue_job` 仅在 `WAITING` 且无租约时写入 `QUEUED`，已推进/已租约/已取消的行直接返回（P1-9）；Redis 失败回退 LOCAL 只收养仍为 `QUEUED` 的行。SQLite 回归 `test_enqueue_does_not_overwrite_generating_job` 等 6 项通过。全漫画验收辅助跳过已终态的幂等 inspect 任务，避免再依赖“把 COMPLETED 写回 WAITING”。
- Linux 等价门禁（Node v20.19.2，engines 声明 >=22，构建仍成功）：供应商平权 git grep 0 违规；Ruff 通过；默认 pytest 542 passed / 70 skipped，Windows Job Object / oem 编码 / 备份 reparse / CLI 可执行文件更新等平台项失败或 ERROR，不记为本轮 Linux 验收失败、也不改为假 skip；Vitest 32 文件 239 passed；ESLint；Next.js 16.3.3 生产构建通过。本 Linux 轮未跑 Playwright、Lighthouse/FPS（随后由 Windows owned 控制器在 `98b93a0` / `LAPTOP-TV9KT8RC` 跑通，见上一节）、付费供应商、真实 CLI 生图。
- 未改 `docs/architecture.md` / `docs/data-model.md`（无新 schema 或模块边界）。Windows JO 独立 Worker 进程树仍 `BLOCKED`/`NOT RUN`；付费调用仍 NOT RUN。浏览器 E2E 与性能门禁见上一节 Windows owned 记录，不在此重复记为未跑。


## V02-40 导演命令日记与执行器已审阅合并（2026-09-03）

- Issue #94 / PR #95 / `fix/v02-40-director-command-layer` / 合并提交 `01e9c3f` / head `cbe4059`。新增独立 director 路由与迁移 `20260903_27_director_command_journal`：journal 表 `director_command_groups` / `director_commands`，`/projects/{id}/director/command-groups` 提议/列表/读取/丢弃与 command accept/reject/undo/redo。
- propose 以 `command_group_id` 幂等重放；preview 在 savepoint 内执行并回滚，不落业务改动；accept 按 expected_version 校验后应用并记录 `storyboard_version_after`；undo/redo 以 inverse 命令与快照实现，分镜被并发更新时返回 SUPERSEDED；`regenerate_region` 在执行层 fail-closed，不触发付费调用或整页重生。
- NOT RUN：真实供应商、NL 模型解析（propose 只接受结构化 `commands[]`）。原文 NOT RUN 中的「V02-41 导演台 UI」已由 V02-41B 落地，「V02-42 CandidateLineage」已由 V02-42B 落地。

## V02-32 分镜画布可用性与 100 节点夹具已审阅合并（2026-09-03）

- Issue #91 / PR #92 / `glm/v02-32-storyboard-acceptance` / 合并提交 `23f5e28` / head `32487d1`。合成夹具 20 格 × 每格 4 气泡 = 100 个可命中对象，不落库（超出 3–8 格 / 每页 8 气泡 API 门禁）；`?stress=100` 渲染 `StressStoryboardCanvas`，无保存入口。
- 超过 `HIT_TEST_OBJECT_LIMIT` 后未选中对象改 SVG 矢量层 + 页级命中测试，仅选中对象挂 DOM 手柄；拖动期间零 React 重渲染；阅读序号 overlay + viewport culling，RTL 锚点随 `reading_direction`。
- Vitest 覆盖宽高比、3/5/8 格整包 PUT、重叠/越界不分叉、保存失败放弃重载；S1–S20 保持绿。测量脚本 `storyboard-stress-fps-gate.mjs` 已加，本环境未跑真实 FPS。
- NOT RUN：Windows Playwright E2E、Lighthouse、真实触控板 FPS、V02-52A N=20、真实供应商。

## V02-31B 视觉分镜画布已审阅合并（2026-09-03）

- Issue #89 / PR #90 / `glm/v02-31b-storyboard-canvas` / 合并提交 `c4f0fc5` / head `fda5233`。`apps/web/components/storyboard-editor/` 以 DOM 页画布替换联系表：视口缩放/平移/适配/复位、拖拽与 resize、气泡框与尾巴、吸附对齐线、阅读序号、撤销/重做、离开保护与出血/安全区开关（无字段时禁用）。
- 画布几何统一走 `PUT storyboard-geometry` 整包快照 + `request_id` 重放；S1–S20 及 S4b/S4c（多边形格在组拖动与方向键下只读）以 Vitest 覆盖。
- NOT RUN：Playwright、Lighthouse/FPS、真实供应商。

## V02-30B 分镜布局数据/API 已审阅合并（2026-09-03）

- Issue #87 / PR #88 / `glm/v02-30b-storyboard-layout` / 合并提交 `e2a12d2` / head `a22f94d`。按 `docs/v02-storyboard-layout-contract.md` 落地分镜布局数据与 API：`manga_pages` 新增 nullable JSON `canvas` 与 `geometry_save_command`，`panels` 新增 `geometry`，`dialogues` 新增 `bubble`；读路径对旧 `bounds`/`region` 派生规范几何，历史数据零改写。
- API 面：PATCH panels bounds+geometry（两侧矩形事实保持一致）、PATCH dialogues bubble、PATCH reading-order 整页重排、PUT `/pages/{id}/storyboard-geometry` 整包原子保存；layout `panel_count` 上限 3–8。L1–L8 契约测试矩阵以 SQLite 覆盖。
- P1：`request_id` 重放从进程内 LRU 改为持久化到 `manga_pages.geometry_save_command`，响应丢失后跨进程安全重放，不重复递增 `storyboard_version`。
- 真实 PostgreSQL 升降级、Playwright 与 V02-31 画布为 `NOT RUN`。

## V02-23B 角色模型包工作区已审阅合并（2026-09-03）

- Issue #85 / PR #86 / `glm/v02-23b-character-package-workspace` / 合并提交 `6d09e78` / head `cc4de3f`。实现角色模型包工作区、页面绑定与版本 picker，数据契约沿用 V02-22A。
- P1：生成台 packages 未就绪时按 fail-open 处理，不阻塞既有选模路径；P2：包列表分页 200、归档版本 picker、发布后只读矩阵已进 master。
- 真实供应商调用、Playwright 与 PostgreSQL live 为 `NOT RUN`。

## V02-22A 角色模型包数据契约已合并（2026-09-02）

- Issue #78 / PR #79 / `glm/v02-22a-character-package-contract` 已冻结角色模型包数据与生成契约：新增 `docs/v02-character-model-package-contract.md`，并同步 `docs/data-model.md`（ER 图、实体小节、约束、迁移策略）与 `docs/architecture.md`（模块职责、生成边界），两处均标注"契约冻结，V02-22B 实现前不生效"。
- 契约要点：`Character.id` 继续作为唯一兼容锚点，包 API 以 character_id 寻址；包/版本/版本参考图/版本服装四张新表用关系表表达（逻辑槽唯一 `(version_id, role, label)`、每版至多一个默认服装、每包至多一个 DRAFT）；版本状态机 `DRAFT → READY → IN_PRODUCTION → ARCHIVED`，publish/activate/archive/restore 共享包行锁并在锁内重验目标状态与发布指针；迁移为每个既有 Character 建兼容包与 V1 草稿（不发布、不改写既有行、不触碰候选快照），降级按子表优先删表并在存在发布指针/多版本/归档包时拒绝；完整度为服务端确定性计算的建议指标，不进入 `ensure_page_ready` 生产门禁；候选排队把版本与规格冻结进 `prompt_snapshot["character_packages"]`，Worker 只消费排队快照，发布新版本不改变历史候选、不触发页面复核。
- Codex Review 八轮共 27 条意见（11 P1 + 16 P2）全部修复并逐条回复；末轮复审 0 P1，2 条 P2（排队快照保留值、默认解析与归档竞态重验）合并前修复。`git diff --check` 通过；本任务为设计文档，未运行 `npm run check` 充当契约验收，未创建迁移或修改任何业务代码，未调用真实供应商。
- PR #79 已合并为 `6298be1`，Issue #78 自动关闭；主 worktree 已与 `origin/master` 同步，`output/` 等未跟踪文件未触碰。真实 PostgreSQL 升降级与并发（PKG-S14）、Redis/RQ 集成、真实供应商调用与浏览器 E2E 为 `NOT RUN`。V02-22B 实现任务按契约 §12.4 拆分建议另行创建。

## V02-20B 场景一级资产后端已审阅合并（2026-09-01）

- Issue #74 / PR #75 / `codex/v02-20b-scene-assets` 已完成场景一级资产后端：新增 SceneAsset、SceneAssetReference、SceneAssetVariant、SceneAssetVariantReference 及可回滚迁移；Scene 通过可空外键绑定资产与变体，历史 `location` 原样保留且不伪造回填。
- 项目级 API 支持资产、参考图、变体、确认、软删除与恢复；绑定校验覆盖项目归属、变体归属和软删除状态。统一 `resolve_scene_background` 按结构化字段与变体覆盖、描述、历史 `Scene.location` 的顺序解析背景。
- 生成链路将场景资产版本、变体和覆盖字段冻结到 `PageCandidate.prompt_snapshot` 与 `GenerationRecord.input_versions`，并通过 `JobAssetReference` 租约资产级和变体级参考图；资产编辑会把相关页面标记为需要复查。
- Codex 自动 review 的冻结队列快照、变体不变量、不可哈希覆盖值和 canonical 降级版本问题已在后续提交修复。定向测试 41 项、全量 Pytest 552 项、Vitest 218 项、ESLint、Ruff、供应商平权扫描和 Next.js 生产构建通过。
- PR #75 已合并为 `6453a40`；`docs/architecture.md` 与 `docs/data-model.md` 已同步。真实 PostgreSQL 升降级与所有权校验、真实 Worker/供应商场景图调用、V02-21 前端工作台、浏览器和性能门禁为 `NOT RUN`。

## V02-16B 用量与成本看板已审阅合并（2026-09-01）

- Issue #72 / PR #73 / `glm/v02-16b-usage-dashboard` 以当前实际契约（`GET /usage/summary`、`GET /usage/attempts`、`GET /usage/attempts/{attempt_id}`、`GET/POST /usage/reconciliations`）实现 `/settings/usage` 看板：时间/项目/供应商/模型/通道筛选、KPI、按日趋势（含无障碍数据表）、供应商/模型分解、attempt 明细 keyset 分页与详情抽屉、按币种分行的 CSV 导出和本地预算提醒；系统设置页提供入口。设计旧稿中的 `/usage/calls` 未被伪造。
- 成本语义红线贯穿 UI：billed 与 estimated 永不相加、不同币种不隐式换算（CSV 亦按币种分行）、单次 attempt 金额不猜测（明细接口不返回，抽屉明示）、unknown 不显示为 0、CLI 不显示为免费；UNAVAILABLE 仅用于成功且未返回用量的调用，失败/未决归 UNKNOWN。
- 读取侧最小扩展：`/usage/attempts` 与 `/usage/summary` 新增可选 `model_id` 精确筛选（keyset 分页下客户端过滤会破坏游标语义）；顺带修复 `summarize_usage` 循环变量遮蔽新参数导致无模型过滤时 billed 列表被清空的回归。写入路径、迁移与计量语义未改动。
- e2e 种子新增离线用量账本（两个价格版本、六类 attempt、一条 CNY 对账记录），日期相对当前时间生成。离线 Playwright 16/16 通过（一次性隔离 SQLite、`QUEUE_ENABLED=false`，无真实供应商调用）。
- 首轮 Codex review 的 6 条意见（仅 billed 场景的渲染门槛、预算提醒缺失、attempt 徽标改中性「仅计量」、UNAVAILABLE 归类、翻页失败错误隔离、CSV 公式注入转义）已在 `d614b0a` 修复并逐条回复。
- 完整 `npm run check` 通过：供应商平权、ESLint、Ruff、535 项 Pytest、218 项 Vitest、TypeScript 与 Next.js 生产构建成功。真实供应商账单核对、PostgreSQL、Redis/RQ、真实 CLI 通道与性能门禁为 `NOT RUN`；未读取凭据、未产生供应商费用。PR #73 已合并为 `fec8d7f`，Issue #72 自动关闭；主 worktree 干净并与 `origin/master` 同步。

## V02-15B 模型调用账本已实现（2026-09-01）

- PR #71 / `codex/v02-15b-usage-ledger` 已实现结构化用量账本：`ModelCallAttempt` 计量列迁移与回填（usage_status / usage_source / unit_kind、缓存与图片维度、输出资产二阶段挂接）、`normalize_usage` 归一与 finalize 写入路径、`GET /usage/attempts`（keyset cursor）、`GET /usage/summary` 与 `ProviderUsageReconciliation` 对账 API（幂等键与区间重叠 409）；缺少计量时保留 `unknown`，任何缺失不折算为 0。
- 真实 PostgreSQL 迁移往返与双 Worker 并发、真实供应商账单核对为 `NOT RUN`。

## V02-14C Grok Build CLI 已审阅合并（2026-08-31）

- Issue #67 / PR #68 / `codex/v02-14c-grok-build` 已实现 `CLI_GROK_BUILD`：内置连接和 `grok-build-imagine` 声明模型默认不可调度，设置页保存 `grok` 或原生绝对路径，不显示 API Key，也不安装、更新、登录、退出或从设置页发起付费冒烟。
- 图片生成和最多五张参考图编辑复用公共 controller 与 Windows Job Object runner。每个 run 以审计 ID 绑定 Grok session，只向子进程开放匹配的 `image_gen` 或 `image_edit`；提示词留在 run-owned 文件，streaming JSON 仅采用当前 session 的唯一 typed 图片，完成路径、链接、会话、格式、像素、大小和清理复验，失败不回退 HTTP。
- 针对 Grok Build 会加载外部配置的边界，workspace 使用私有 Git 标记阻断项目指令发现，连接能力探测与每次图片执行前都检查 `inspect --json`；发现任何外部 hook 或无法确认状态时，在图片工具调用前返回 `UNSUPPORTED`，原始 inspect/streaming 输出不落盘。
- Windows 实机只读确认原生 `grok.exe 1.0.13`。本机登录检查返回 `UNAUTHENTICATED`，并检测到全局插件 hooks，安全能力检查按设计返回 `UNSUPPORTED`；因此没有执行真实图片生成/编辑，没有读取或复制凭据，也没有产生供应商费用。
- 精确实现提交为 `750886f`，首轮 review 修复提交为 `b7ac5e4`：预检与媒体进程现在共享一个截止时间，非零退出、超时、取消和无效输出均执行 run-owned Grok session 清理，清理失败以脱敏诊断传播且不误删其他 session。完整 `npm run check` 一次通过：Ruff、ESLint、供应商平权、527 项 Pytest、192 项 Vitest、TypeScript 与 Next.js 生产构建成功，24 项真实集成按环境跳过。真实图片调用、账号等级/费用、真实取消/超时进程树、PostgreSQL、Redis/RQ 和浏览器 E2E 为 `NOT RUN`。PR #68 已合并为 `8479a19`，Issue #67 自动关闭；全部登记 worktree 均干净且其 HEAD 已包含在 `origin/master`，未强制同步或删除任何 worktree。

## V02-14B Antigravity CLI 已审阅合并（2026-08-31）

- Issue #64 / `codex/v02-14b-antigravity-cli` 已接入 `CLI_ANTIGRAVITY`：内置连接和 `antigravity-imagegen` 声明模型默认不可调度，四步只读探测成功后仍需用户显式启用；设置页按协议保存 `agy` 或绝对原生路径，不执行 wrapper、安装、更新、登录、退出或付费冒烟。
- 图片生成和单参考图编辑复用公共 controller 与 Windows Job Object runner。固定 argv 使用 sandbox、禁用 slash 扩展、受控 input 目录、JSON print 和 CLI/进程双重超时；用户 prompt 只进入校验和保护的 request。每次 run 使用私有 HOME，只从受控 Antigravity brain artifact 树采用唯一图片，拒绝链接/junction、越界、歧义、过量、坏格式和超限输出。
- Windows 实机只读确认原生 Google 签名 `agy.exe 1.1.22` 及 `--sandbox`、`--disable-slash-commands`、`--add-dir`、`--output-format`、`--json-schema`、`--print-timeout`、`--print` 参数。`agy models` 明确返回未登录，因此未发起任何模型或图片调用，连接按设计保持 `UNAUTHENTICATED`。
- 最终完整 `npm run check` 通过：供应商平权、ESLint、Ruff、511 项 Python 通过、24 项真实集成跳过、191 项 Vitest 通过，TypeScript 与 Next.js 生产构建成功。真实图片生成/编辑、账号权限与费用、真实取消/超时进程树、PostgreSQL、Redis/RQ 和浏览器 E2E 为 `NOT RUN`；未读取或复制凭据，未产生供应商费用。
- 精确实现提交 `93f8006` 已由 PR #65 合并为 `575b4e0`，Issue #64 自动关闭；本地 `master` 与 `origin/master` 已同步。所有登记 worktree 均干净且无未合并领先提交，未强制同步或删除历史 worktree。

## V02-14A Codex CLI 已审阅合并（2026-08-31）

- Issue #62 / `codex/v02-14a-codex-cli` 已接入 `CLI_CODEX`：内置连接默认禁用，四步探测成功后仍需用户显式启用；设置页可保存 `codex` 或绝对原生路径，但不安装 CLI、不登录账号，也不从设置页发起付费图片冒烟。
- Codex 图片生成/编辑复用 V02-13 公共 controller 与 Windows Job Object runner。用户提示词只进入校验和保护的结构化 request，不进入 argv；参考图和结果使用受控相对路径，调用绑定持久化 `GenerationJob` / `ModelCallAttempt`，失败不回退 HTTP，费用保持 unknown。
- 主审用真实 Codex CLI 发现并修复全局参数误放在 `exec` 后的退出码 2 缺陷；随后以 Job Object 只读探测确认 `codex-cli 0.149.1`、版本和安全自动化参数解析通过。CLI 登录状态真实返回 `Not logged in`，连接按设计派生为 `UNAUTHENTICATED`，探测目录已清理。
- 完整 `npm run check` 通过：供应商平权、ESLint、Ruff、488 项 Python 通过、24 项真实集成跳过、190 项 Vitest 通过，TypeScript 与 Next.js 生产构建成功。真实图片生成/编辑、账号权限/费用、取消或超时的真实子孙进程终止、PostgreSQL、Redis/RQ 和浏览器 E2E 为 `NOT RUN`；未读取凭据内容，未产生供应商费用。
- 修复后的精确实现提交 `2cd3c77` 已由 PR #63 合并为 `ee8fb8e`；Issue #62 已关闭，V02-14A 已进入 `master`。

## PR #61 已复审并合并（2026-08-31）

- PR #61 的 V02-10B/C/D、V02-11B/C 测试切片、V02-12B/C 与 V02-13B 实现已由组长按最终 SHA `75460a0` 复审，并以 `e918b28` 合并到 `master`。
- 复审补修模型展示偏好的条件更新，防止并发请求绕过 version 门禁；CLI 四步探测现在只有全部 `PASSED` 才进入 `AVAILABLE`，`UNKNOWN` 结果保持不可用；Windows 测试显式按 UTF-8 读取结构化请求。
- Windows 环境完整 `npm run check` 通过：供应商平权门禁、ESLint、Ruff、479 项 Python、189 项 Vitest、TypeScript 与 Next.js 生产构建全部通过；24 项真实集成或实机环境测试按条件跳过。
- 真实 PostgreSQL、Redis/RQ、真实供应商、真实 Codex/Antigravity/Grok Build CLI、浏览器 E2E 与 Windows 真实 CLI 进程树仍为 `NOT RUN`；未读取凭据、未产生供应商费用。所有登记 worktree 均干净且提交已包含于 `master`，当前无开放 PR。

## V02-13B CLI 公共执行器已实现（2026-08-31）

- 新增 provider-neutral CLI controller 与 `CLIExecutionRun` 持久状态；每次派发关联唯一 `ModelCallAttempt`，数据库唯一槽位限制单通道并发，迁移存在审计行时拒绝降级。
- 参考图受控复制，prompt 只进入结构化 request；结果只采用已登记且通过路径、大小、像素与格式校验的图片。日志和错误脱敏，费用保持 unknown，失败不会静默改走 HTTP。
- 四步探测写入 `ModelProbe`；`CLI_SESSION` 不显示密钥表单、不读取会话凭据。Windows runner 使用 suspended create + kill-on-close Job Object，先归组和持久登记再恢复进程。
- Ruff、53 项 CLI/迁移/供应商 Python 回归、Web ESLint、TypeScript、26 个文件 189 项 Vitest、Next.js 生产构建与 `git diff --check` 通过。真实 CLI、真实供应商、Windows 实机进程树与 PostgreSQL 均未运行，也未产生费用。

## V02-10D 产品级供应商入口已中性化（2026-08-31）

- 首页删除专属状态请求与验证卡，改为单一 Dashboard 聚合请求和通用 AI 连接摘要；设置、帮助、项目默认模型与工作流节点统一消费凭据来源及模型目录，当前历史绑定仍保留显示。
- 环境账号是否就绪集中由凭据适配层回答；Dashboard、`/models`、readiness 和目录服务不再各自读取供应商专属配置。Worker 拒绝文案、架构协议能力表、平台和数据模型示例已中性化。
- 平权 allowlist 从 15 个路径收紧到 10 个，仅保留原生适配、环境凭据兼容、历史模型 registry/预设、单版本旧健康桥及协议类型。旧 `ProviderHealth` 当前仍服务 `/settings/vertex/*` 兼容端点，产品前端已无读方；待兼容端点退役时一并删除，避免本轮破坏旧客户端。
- Ruff 与 85 项供应商/Dashboard/readiness/原文解析定向 Python 回归通过；Web ESLint、TypeScript、26 个文件 188 项 Vitest 和 Next.js 生产构建通过，`git diff --check` 与 Linux 等价平权扫描通过。M14 与 PowerShell 完整门禁仍需 Windows 环境，未调用真实供应商。

## V02-10C 默认值、路由与 grandfather 已实现（2026-08-31）

- 新项目的文字/图片 legacy 别名改为可空且不再带供应商默认；风格分析、原文解析、视觉检查和默认工作流文字节点统一使用 `auto`。`GenerationRecord.provider` 必须由实际绑定显式写入。
- `image.fast` / `image.quality` 仅在解析时归一化到现行 legacy alias，不重写历史任务；目录 ID、目录模型字段和真实 provider/model ID 优先，旧 registry 只补目录缺失的可选能力。
- Vertex 新安装在环境凭据未就绪时持久化一次性 `auto_enable_pending`，就绪后只启用一次并清标记；旧连接及人工启停保持原值，凭据后来缺失只改变健康态。新预设模型以 `DECLARED` 创建，需模型冒烟后才参与自动路由。
- 新增项目别名可空迁移。SQLite 回归发现并修复 batch 重建 `projects` 触发外键级联的风险；迁移现在窄范围暂停外键、重建后执行 `foreign_key_check`。历史项目升降级逐字段不变，存在 NULL 项目时 downgrade 明确拒绝且数据保留。
- Ruff、ESLint、TypeScript、187 项 Vitest、Next.js 生产构建及排除 Windows 专属模块后的 368 项 Python（47 项集成环境跳过）通过。完整 Python 其余失败/错误来自既有 Windows junction、PowerShell 与 Job Object 边界；真实 PostgreSQL 和完整 PowerShell 门禁 `NOT RUN`，未调用真实供应商。

## V02-11C 设置页验收进行中（2026-08-31）

- Issue #40 的 P/C/F/V/A/S/L/N 组件矩阵已补齐：供应商设置定向回归由 58 项增至 66 项，新增账号型连接降级、手工模型、发现置信度、图片费用取消、密钥脱敏/清空和展示/调用/派生可用性三态测试。
- 三态回归发现并修复派生不可用模型缺少状态的问题：设置页现标为「未就绪」并禁止冒烟测试，仍与持久停用的「不可调用」及展示偏好的「已隐藏」分离。
- 已新增离线假供应商浏览器用例，覆盖 UI 创建连接、手工添加模型及显隐偏好，明确断言未触发 verify/discover/balance；用例通过独立 TypeScript 检查。Web ESLint、TypeScript、25 个文件 187 项 Vitest 和 Next.js 生产构建通过。
- 浏览器执行 `BLOCKED`：仓库验收入口依赖 Windows `SO_EXCLUSIVEADDRUSE` 与 Job Object，当前 Linux 在启动任何服务前安全失败；未绕过进程归属保护。完整 `npm run check` 仍由缺少 PowerShell 阻断。因此 V02-11C 保持未完成，待 Windows 环境运行离线 E2E 与完整门禁；未调用真实供应商。

## V02-12C 下游模型展示偏好已统一（2026-08-31）

- 生成台、资产生成、工作流节点/审批和项目默认文字模型选择器统一只提供 `enabled && display_enabled` 的新选择；当前已选或已绑定的隐藏模型继续保留，项目设置明确标注“已隐藏”。
- 生成候选、素材库和任务记录使用完整图片模型目录解析历史模型名称；目录缺失时回退真实逻辑别名，不因展示偏好丢失历史身份。过滤仅在前端选择器发生，未修改任务创建、自动路由、调用能力、审计或重放。
- 新增共享过滤规则及项目设置回归；Web ESLint、TypeScript、25 个文件 179 项 Vitest 与 Next.js 16.3.3 生产构建通过。浏览器 E2E 与真实供应商未运行。

## V02-11B 统一连接与模型目录已实现（2026-08-30）

- 设置页已使用连接级管理目录和统一连接验证，并按凭据来源及能力渲染发现、余额和模型冒烟；Vertex 专属卡与硬编码模型按钮已删除。
- 已接入单条/批量展示偏好、隐藏筛选、逐项版本与部分失败保留选择，且不修改模型调用开关。Web ESLint、TypeScript、176 项 Vitest 与生产构建通过；真实供应商和浏览器 E2E 未运行。

## V02-12B 模型展示偏好后端已实现（2026-08-30）

- `ai_models.display_enabled` 独立于持久调用开关与派生可用性；既有和新建模型默认展示，隐藏不影响显式调用、自动路由、任务引用或审计 ID。
- 新增连接模型管理列表、单条显隐更新和最多 100 项的批量显隐端点。批量按模型独立提交，返回缺失、孤立连接与版本冲突明细；相同目标值重试保持版本不变。
- 仅修改展示偏好时保留 `source`/`confidence` 且跳过能力校验；与能力字段混合更新时继续沿用原有 `MANUAL` 语义。预设刷新不会覆盖用户偏好。
- 48 项迁移/供应商定向回归、Ruff、Web ESLint、170 项 Vitest 和 Next.js 生产构建通过。Linux 全量 Pytest 为 397 passed / 60 skipped；6 failed / 10 errors 均是仓库既有的 Windows junction、PowerShell/OEM 编码与 Job Object 验收，当前环境无法执行。
- SQLite 已覆盖历史行无损回填、升降级往返与隐藏值降级保护；真实 PostgreSQL、浏览器 E2E 与真实供应商未运行。设置页管理能力已由 V02-11B 完成，下游创作选择器消费已由 V02-12C 完成。

## V02-10B 统一连接健康与验证已实现（2026-08-30）

- 新增协议中立的连接健康与验证端点；`CREDENTIALS` 不生成内容，`MODEL_SMOKE` 使用目录模型并写入 `ModelProbe`，图片测试继续要求费用确认。
- PR 复审补修：旧 `VISION` 请求现在保留 `multimodal_analysis` 操作；每次已确认的图片冒烟只调用一种能力；无可用 Key 时失败探针会把连接从 `CHECKING` 收敛到 `UNCONFIGURED`。文字/视觉分别验证后累积能力置信度。
- 模型发现改由协议能力声明控制；验证与目录同步拆开，不支持发现的连接使用预设或手工目录。预设刷新不再覆盖用户修改过的模型定义、验证结果或启停状态。
- `/settings/vertex/status|verify` 保留一版兼容并转发统一服务；无人调用的 `/models/vertex/*` 兼容别名与死 schema 已删除。诊断页按实际连接动态生成检查项，不再固定写入 Google OAuth/Gemini 模型项。
- Ruff 与 73 项供应商、健康、Dashboard、平台契约定向回归通过。Linux 全量 Pytest 达到 390 passed / 60 skipped；其余失败均来自仓库既有的 Windows Job Object、junction、PowerShell 专用验收，当前环境无法执行。未调用真实供应商、未读取凭据、未产生费用。

## 0.2.0 首批扩展交付已集中验收（2026-08-30）

- 供应商实现切片 V02-10A/V02-11A 与 18 项设计、审计、能力矩阵和验收计划已由组长逐项复审、直接修复并按依赖顺序合入本地 `master`；20 项合并树为 `01fd82a`。缺失的 V02-12A、V02-50 Issue 已补建为 #59、#60，所有交付均有独立 Issue、分支、worktree 与验收边界。
- 已实现：`credential_source` 派生、共享供应商错误分类、PowerShell 5.1 平权门禁、统一连接健康/验证、模型展示偏好后端，以及供应商设置 UI 基础（生命周期、搜索/筛选/排序/折叠、显式凭据来源、连接测试与目录同步、连接启停、加载/错误/空状态和回归测试）。V02-12 下游选模与 V02-11B 设置页统一仍属于后续实现。
- 已冻结：模型展示偏好、CLI controller、用量账本/看板、场景资产、角色模型包工作区、分镜 geometry/bubble/canvas、导演命令血缘、局部重抽卡、模型级图片编辑能力、全局文案、桌面工作区、性能门禁和桌面壳 ADR。TodoList 已将完成的 A 级设计/审计与待实现的 B 级任务分列。
- 合并后在具备 Windows Job Object/监听归属权限的环境执行完整 `npm run check`，退出码 0：供应商平权脚本、ESLint、Ruff、439 项 Python、170 项 Vitest、TypeScript 与 Next.js 16.3.3 生产构建全部通过；23 项真实 PostgreSQL/Redis/RQ 集成因环境未提供而跳过，36 条既有弃用警告保留。
- 首次受限沙箱运行曾因 `.ruff_cache`/`.next/trace` ACL 和 `Get-NetTCPConnection` 权限中断；`ruff --no-cache`、沙箱外 2 项 Windows 归属测试与生产构建分别通过，随后同一沙箱外环境完整门禁一次性通过。该过程记录为环境边界，不修改项目配置或测试断言规避失败。
- 未运行真实供应商、Codex/Antigravity/Grok Build CLI、生图、真实 PostgreSQL/Redis/RQ、浏览器 E2E/性能采样、桌面 PoC、签名、安装或自动更新；未读取或复制凭据，未升级用户数据库。`0.2.0` 版本号继续等待 V02-55 发布门禁，不在本批提前修改。

## PR #11 已合并，首批团队验收完成（2026-08-28）

- P1-5 / Issue #8：Antigravity 实现与两轮返工后，组长复审仍发现真实 UNIQUE 冲突会导致辅助函数回滚并丢失调用方数据，按约定接手，以 `e2d37af` 修复；PR #11 合并提交为 `c405cfa`，Issue #8 已关闭。
- 序号尝试采用数据库锁与真实外层事务内的保存点；失败只撤销本次尝试，保留调用方数据并刷新实体。补齐候选/job/审批状态、原文修订及最终提交失败的回滚保护，校验调用链不再隐式提交。
- PR 分支与合并后的 master 分别独立执行 `npm run check`，均退出 0：250 Python、27 Vitest、Ruff/ESLint、TypeScript、Next.js 生产构建通过；26 项序号专项与 14 项 Dashboard 回归均包含在内，保留 19 条既有 Python 弃用警告。
- 合并后核对两个开发 worktree 均干净、无未合并任务提交，可安全快进；保留现有分支/目录。首批 Issue #8/#9 已完成开发、审阅、返工/接手、合并及主分支验收，不自动启动下一批。
- 无 schema/迁移变更，架构与数据模型事务说明已同步。未做真实 PostgreSQL/Redis、浏览器 E2E、Lighthouse/FPS 或供应商调用；未触碰生产数据、复制凭据或调用付费模型。

## PR #10 已合并（2026-08-28）

- P2-6 首页“可用模型数”统一复用模型目录的可用性判断；兼容协议要求模型、连接、供应商启用，凭据可写且存在未冷却的启用密钥。原生 Vertex、已配置连接数与健康连接数的语义保持不变。
- Dashboard 不触发预设写入；首页代码仍只定义 Dashboard 与 Vertex status 两项请求。新增无密钥、禁用、冷却/过期、凭据只读、Vertex 和多密钥去重等边界回归，Dashboard 专项共 14 项。
- Grok Build 提交 `f62e464`；组长独立审阅及验证通过后，PR #10 以 `1d56fdb` 合并，Issue #9 已关闭。合并树与已验证提交一致。
- PR 分支独立通过 Ruff、ESLint、224 项 Python、27 项 Vitest、TypeScript 与 Next.js 16.3.3 生产构建；合并后主分支 `1d56fdb` 再次独立执行 `npm run check`，退出码 0，结果一致。保留 19 条既有 Python 弃用警告。
- PR #10 合并时 Grok 已快进同步，Antigravity 因未提交改动暂缓同步；后续已安全整合主分支并随 PR #11 完成验收。
- 本轮无 schema 变更，未执行浏览器 E2E、Lighthouse/FPS、真实 PostgreSQL/Redis 或付费模型调用；测试临时目录已清理，未修改生产数据或复制凭据。

## PR #7 已合并（2026-08-28）

- P1-8 / P2-8 审查补修：保存失败后恢复队列；发布时刷新并校验当前草稿；SQLite 真实并发锁竞争受控回滚/重试，耗尽返回 409。
- 修复前 6 项新增用例复现失败；新增 9 项测试并扩充工作台失败后再次发布的组件回归，修复后全部通过。
- `agy` 独立 `npm run check` 退出码 0：212 Python、27 Vitest、ESLint、Ruff、TypeScript 与 Next.js 16.3.3 生产构建通过；`git diff --check` 通过。Python 有 19 条既有弃用警告。
- 修复提交 `c665d2f` 已推送；PR #7 以 `bcc470e` 合并，`master` 已快进同步，合并树与已验证的修复树完全一致，保留 `agy` 分支。
- 主分支 `bcc470e` 独立 `npm run check` 退出码 0：212 Python、27 Vitest、ESLint、Ruff、TypeScript 与 Next.js 16.3.3 生产构建全部通过；最终验收文档提交仅改文档。
- 本轮无 schema 变更。未执行浏览器 E2E、真实 PostgreSQL/Redis/Docker 或付费模型调用；隔离测试未升级用户数据库，未改动业务数据和真实凭据。

## PR #6 已合并（2026-08-27）

- 修复提交 `e6eecab` 已推送至 `agy`；PR #6 以 merge commit `7a1b926` 合入 `master`，本地主分支已快进同步，`agy` 分支保留。

- 已修复 RQ 延迟调度 ID 校验失败，使用原队列/连接和剩余重试策略；LOCAL 等待不投递 Redis。
- 已统一 Worker `.env` / Redis AUTH / 队列名加载入口，启用调度器；Windows 使用 `SpawnWorker`。
- 已按分镜版本隔离五类检查；旧分镜或检查途中失效的结果不能放行当前页面。新增 Alembic `20260827_17`，历史检查保留但未知版本需重新检查。
- 已将上传耗时同步处理移回线程池，保留异步请求体/表单限制并关闭文件。
- 定向回归 34 项已通过，包含真实 RQ Job 创建校验（仅模拟 Redis 持久化）、配置读取、分镜交错、上传线程、迁移往返及同时间戳失败优先。
- `agy` 阶段 `npm run check` 退出码 0：202 项 Python、17 项 Vitest、ESLint、Ruff、TypeScript 和 Next.js 16.3.3 生产构建全部通过；Python 有 19 条既有弃用警告（新增迁移往返增加了 Alembic 警告触发次数）。`git diff --check` 通过。
- 主分支 `7a1b926` 已独立执行 `npm run check`，退出码 0：202 项 Python、17 项 Vitest、ESLint、Ruff、TypeScript 与 Next.js 16.3.3 生产构建全部通过；使用隔离数据库、上传目录和占位凭据，不读写真实业务数据。此次验收结果随本次文档提交同步至 `master`。
- 未执行 `check:full` / 浏览器 E2E、真实 Redis/PostgreSQL/Docker 或付费模型验收，未自动升级用户数据库。

以下引用的审查结论和旧验收记录为历史基线；当前合并状态以上述最新 PR 记录及 TodoList 勾选为准。

> **2026-08-27 安全审查更新：** `master` / `7635cf7` 确认 1 项 P0、3 项 P1、2 项 P2，全部待修复；详细 TodoList 见 [`roadmap.md`](roadmap.md)。新增 P0-3、P1-13～P1-15、P2-9，异常图片复现并入既有 P2-4，不重复计项。先处理 Next.js Windows 漏洞、默认网络边界与上传入口限制；当前版本不应对非信任网络开放。缓存和临时文件已清理，不代表缺陷已修复。
>
> **2026-08-27 深审更新：** `master` / `f085327` 深审确认 6 项 P1 修复与 1 项 P2 改进，全部已列入 [`roadmap.md`](roadmap.md) 的 TodoList（P1-7～P1-12、P2-8），尚未修复。任务租约基础仍已合并，但 P0-1 可靠性闭环重新打开；草稿保存和五类质检门禁也待收口。`cb324e3` 的 `check:full` 历史通过记录（165 Python、17 Vitest、4 Playwright）保留，不代表新增竞态已覆盖。原有序号范围核对、独立性能门禁、首页计数等待办继续保留，真实供应商验收需另行授权。
>
> **2026-08-26 主分支审查说明：** `master` / `6e0a534` 已合并页面生产门禁、旧候选强制复检和工作流输出状态修复；当时仍缺 Worker 租约恢复、Alembic 启动校验、幂等竞态与轮询收口，浏览器 E2E 也有 2 项失败。

| 阶段 | 状态 | 当前结果 |
| --- | --- | --- |
| A. 基础模型修订与任务内核 | 修复已合并，独立集成待验 | P1-7、P1-9～P1-11 的取消保护、入队回写、本地重试和 RQ 槽位调度修复已合并；独立 Redis/RQ 与 PostgreSQL 集成仍待验证 |
| B. 原作、角色与参考资产 | 已完成 | 文本/TXT/Markdown 导入、不可变修订、无损分段、主要姓名/绰号/说话人、人物/服装/风格参考绑定与四视图补图 |
| C. 剧本与动态分页 | 已完成 | AI 逐片段剧本、三种导演模式、来源追溯、容量估算、溢出拆页、受影响页局部重算、3–5 格动态错落或均衡分镜 |
| D. 单页抽卡与素材库 | 已完成 | 页面批次、每次请求单个正式候选、跨模型历史比较、收藏/采用/下一页、按角色/类型/模型/清晰度/日期/收藏筛选 |
| E. 检查、修复与导出 | 版本化门禁已合并 | P1-12 按候选与分镜版本要求五类最新结果完整通过，旧版本及空/缺项结果不放行；同时间戳冲突失败优先 |
| F. 首张真实彩色页面闭环 | 已完成 | 人物出镜状态、页面 readiness、概念草稿确认、彩色色板/测试图、LOCAL Worker、Nano Banana 2 1K 正式页、人工校对采用与 PNG 导出均已落地 |
| G. 多供应商模型平台 | 已完成 | OpenAI/Anthropic 兼容协议、20+ 预设、自定义供应商、加密多密钥、模型发现/能力测试、延迟/余额、自动路由与动态选模 |
| G. 工作台稳定化与核心 UI 重构 | 保存一致性已补强，性能门禁待完成 | 首页聚合、候选语义、资产路由、分镜编辑与任务分组已落地；P1-8 草稿保存/发布与 P2-8 并发发布冲突已修复，独立性能门禁仍待完成 |
| H. 主分支可靠性收口 | 进行中 | PR #6 已合并安全与调度修复；P1-8、P2-8 已落地。P1-5 序号范围已完成；独立性能、队列/数据库和浏览器验收仍待完成 |
| I. 安全边界与资源保护 | 代码已收口，容器运行未验 | P0-3 Next.js 16.3.3；P1-13 回环绑定；P1-14 Compose 端口/Redis AUTH（无 Docker 未运行验证）；P1-15/P2-4 上传限制与炸弹清理；P2-9 元数据有界读取 |

## 验收结果

- [x] Nano Banana 2 与 Nano Banana Pro 在 UI/API 中平级，每个候选显式选择模型。
- [x] 角色使用主要姓名、绰号和参考图；绰号冲突会标记人工确认。
- [x] 原文按来源区间无损分段；覆盖率不足时拒绝生成候选。
- [x] 动态分页不设总页数上限，单页硬上限 180 字、最多 8 个气泡。
- [x] 每次请求只创建一个页面候选，不自动生成后续页。
- [x] 同页可建立多个批次与候选，可收藏多个、采用一个。
- [x] 下一页连续性只引用当前采用版本；换选后标记后续页待复查。
- [x] 素材库按章节、页面、批次组织，可容纳角色、服装、风格和修复素材，并支持计划要求的全部筛选项与软删除。
- [x] 假模型/禁用队列测试覆盖 8 路任务隔离、取消、重试与幂等。
- [x] 分镜将 `VISIBLE/OFFSCREEN/MENTIONED` 与道具独立保存；第一页只有“我”实际出镜，妈妈与爸爸仅被提及，爸爸的灵牌属于道具。
- [x] 页面 readiness 会随着人物、服装、风格和 Worker 条件逐项解除；供应商与模型能力在排队前独立校验。
- [x] 文字/图片模型分目录；未经能力测试的推断模型不能参加自动路由。
- [x] 任务引用素材建立租约，删除/改用途与付费调用之间的竞态已封堵。
- [x] `AUTO/LOCAL/REDIS` 三种队列模式、开发环境本地执行和 API 重启恢复均有自动测试。
- [x] 文字采用唯一门槛为人工校对确认；新任务不再运行文字自动检查或创建文字修复任务，并继续拦截严重角色、服装和连续性问题。
- [x] Alembic 升级到 `20260717_12` 后清理孤立分镜数据，SQLite 外键检查为零，并保留所有有父记录的页面、候选、任务和历史检查。
- [x] Codex 内置浏览器实测当前 11 页规划、第一页 3 格动态分镜、人物/服装引用映射、彩色色板和风格测试通过。
- [x] 页面生产门禁已接入人工暂选、分镜确认和视觉检查汇总状态；旧候选需重新确认并复检，下一页及导出共用门禁入口。
- [x] P1-12 五类最新检查完整性与失败阻断已合并，按分镜版本隔离；下一页、导出与工作流输出继续共用生产门禁。
- [x] 默认 DAG 以单页成品结束，整章导出使用独立流程并要求章节全部页面生产通过；无实际出镜人物的场景页可正常生成。
- [x] 生成素材导入服装档案只建立引用关系，保留原素材类型、生成历史和独立生命周期。
- [x] 默认 DAG 已发布为不可变 `revision=1`；真实 PAGE 运行生成一个 Nano Banana 2 1K 候选后停在“采用候选”人工节点，运行、节点与任务记录完整。
- [x] 2026-08-27 合并后主分支 `cb324e3` 独立执行 `npm run check:full`，退出码 0：165 个 Python 测试、17 个 Vitest 测试、Ruff/ESLint、TypeScript 与 Next.js 生产构建通过。
- [x] Playwright/Axe E2E 独立复验 4/4 通过（23.3 秒）：统一导航、项目深链/后退/刷新/工作流入口、5 条路由请求预算、7 条路由 Axe serious/critical 检查均通过；首页保留 Dashboard + Vertex status 两项查询，预算 ≤3。
- [x] 新建隔离数据库通过 Alembic 全量升级至 `20260801_16 (head)`；7 项迁移测试及假模型完整漫画流程、默认 DAG 至输出测试通过；验收结束 SQLite 外键检查为零。
- [x] 任务完成落库使用原子取消门禁，项目删除会取消非终态任务；素材软删除、恢复上传和结构化引用解绑均有回归测试。中间状态与调用前的取消保护仍待 P1-7，不能据此宣称取消全链路已闭环。
- [x] Worker 租约基础、心跳、过期回收与完成/失败条件更新已落地（`d965007`～`390ebdc`）；API 启动移除 `create_all()` 并校验 Alembic head；任务幂等插入使用 savepoint 捕获唯一键竞争。P0-1 剩余闭环由 P1-7、P1-9～P1-11 跟踪。
- [x] 前端共享活动状态集合（`lib/task-status.ts`）接入工作台任务、候选、检查结果和资产生产面板轮询；检查/修复期间持续刷新、进入终态停止，并有单元测试与 TanStack Query 行为测试覆盖。
- [x] `npm run check:full` 已提供完整门禁（fast check + Playwright E2E）；Lighthouse 与 100 节点画布 FPS 仍按稳定环境单独设门槛。
- [x] 2026-07-17 的历史浏览器快照测得 Dashboard/资产/分镜/任务/生成首屏 API 请求数分别为 3/4/6/2/8；2026-08-26 复测首页已回升为 4 个请求，2026-08-27 通过 Dashboard 合并模型/供应商摘要降至 2 个。
- [x] 10,000 候选热请求基准固定首批 30 组、每请求不超过 5 条 SELECT、20 次热请求 P95 不超过 500ms；仓库提供 Lighthouse、Axe 与 100 节点画布 FPS 脚本，但 2026-08-26 因 Playwright/Axe 前置门禁失败，未把 Lighthouse/FPS 记为当前通过。

## PR #5 合并后的后续事项

- [x] PR #5 已合并为 `d5d32ec`，本地主分支已快进同步；保留 `agy` 分支。
- [x] 修正文档“P1 全部完成”的过度表述，并同步任务清单的完成项、当前基线与验收来源。
- [x] P1-5：PR #11 已完成章节、原文修订、批次和候选序号修复与事务回归，合并后 master 全量门禁通过；工作流发布的既有修复仍在 P2-8 跟踪。
- [x] P1-6 离线功能验收：合并后主分支 `check:full` 独立通过，含 Playwright/Axe、迁移及假模型流程。
- [ ] P1-6 独立性能验收：Lighthouse 与 100 节点画布 FPS 尚未执行；真实供应商调用另待用户明确授权。
- [x] P2-6：PR #10 已统一首页模型可用性口径，补齐边界回归并保持两项首屏请求定义；主分支独立 `npm run check` 通过，未改变实际选模/生成行为。
- [ ] P2-7：确认关闭态开关滑块变深是否有意保留；未发现功能性误伤，低优先级。

## 2026-08-27 安全审查 TodoList

以下 6 项代码修复已由 PR #6 合并；真实服务、容器网络和完整依赖审计仍未验收。详细定位与原验收条件以 [`roadmap.md`](roadmap.md) 同编号为准，P2-4 沿用既有任务。

- [x] **P0-3 / 安全审查 1：Next.js Windows 漏洞升级**。已升级到 `16.3.3`，锁文件与 `eslint-config-next` 一致；lint/Vitest/生产构建通过。未重跑 `check:full`。
- [x] **P1-13 / 安全审查 2：默认本地网络边界**。开发/生产启动显式绑定 `127.0.0.1`；文档写明单用户信任边界。
- [x] **P1-14 / 安全审查 3：Docker 数据服务隔离与凭据**。Compose 只发布到回环地址，Redis 启用 AUTH；无 Docker 环境，未做容器运行验证。
- [x] **P1-15 / 安全审查 4：上传请求入口限制**。流式计数 + 单文件字段限制，超限 413/422 且不落盘。
- [x] **P2-4 / 安全审查 5：图片像素限制与失败清理**。捕获解压炸弹并返回 4xx，失败文件清理。
- [x] **P2-9 / 安全审查 6：供应商元数据响应限制**。模型发现与余额查询有界读取并限制条目。

验证边界：7 项离线探针完成、54 项相关回归通过，`pip check` 与 `git diff --check` 通过；未重跑全量门禁、未做漏洞利用或容器网络验证，也未调用真实供应商。`npm audit` 因依赖清单外发未获授权而未运行，官方公告与本地版本核对不等同于完整依赖审计。既有回归通过不代表本节缺陷已修复。

清理已完成：114 个可再生成缓存文件（约 2.26 MB）、12 个临时测试文件（约 0.80 MB）及空 Playwright 输出目录已删除；凭据、业务数据库、素材、生成结果、依赖与生产构建保留。审查结束时 3000、3001、8000、8001、5432、6379 均未监听；未发现入侵证据的结论不在本轮取证范围，不能仅据端口状态作判断。后续需把临时复现固化为正式回归。

## 2026-08-27 深审修复 TodoList

以下 P1-7、P1-9～P1-12 的代码修复已由 PR #6 合并，P1-8、P2-8 已由 PR #7 合并；独立集成验收仍待完成。详细复现与原验收标准以 [`roadmap.md`](roadmap.md) 同编号为准。

- [x] **P1-7 / 审查 1：取消与租约失效保护**。中间状态条件更新；调用前检查执行权。
- [x] **P1-8 / 审查 2：草稿自动保存与发布一致性**。保存中的新编辑会补交；发布等待最新草稿保存成功，保存失败不继续发布。Vitest 覆盖保存队列与工作台组件，未跑浏览器 E2E。
- [x] **P1-9 / 审查 3：Redis 入队状态防覆盖**。先落 QUEUED 再发消息，发布后不再无条件回写。
- [x] **P1-10 / 审查 4：LOCAL/AUTO 可重试失败调度**。本地循环捕获可重试异常并退避续跑。
- [x] **P1-11 / 审查 5：RQ 槽位等待调度**。名额不足时延迟再入队；SQLite 单元已覆盖，未跑独立 Redis/PostgreSQL 集成。
- [x] **P1-12 / 审查 6：五类质检生产门禁**。空/缺项不标通过；最新失败阻断生产门禁。
- [x] **P2-8 / 审查 7：并发发布 revision 冲突**。PostgreSQL 行锁、实体刷新与锁内校验；唯一键及 SQLite BUSY/LOCKED 整体回滚后有限重试；耗尽返回 409，不破坏已发布指针。SQLite 双线程并发已覆盖，未跑 PostgreSQL。

上述可靠性任务内部顺序：P1-7、P1-9～P1-11 → P1-8、P1-12 → P2-8 与其他序号范围 → 新增回归及全量门禁复验。全局先执行安全审查 P0-3、P1-13～P1-15，再推进可靠性及 P2-4、P2-9 等改进，以路线图顺序为准。优先级与历史完成项分开记录，不把“已列入清单”标为“已修复”。

本轮深审使用 7 个后端隔离场景与 2 个前端实际回调时序场景，未执行真实 PostgreSQL、Redis 多进程、浏览器交互或付费模型调用，也未重跑全量门禁；临时脚本与数据库均已清理。本次仅更新任务文档，未修改业务代码，未提交或推送。

## 本轮验收环境与注意事项

- 验收提交 `cb324e3` 相对 PR #5 合并提交 `d5d32ec` 仅增加文档；本轮未修改业务代码或测试断言。
- 使用独立临时 SQLite、素材/上传目录、虚构项目 ID 与不含有效凭据的空 JSON；未使用真实供应商凭据，也未触发真实模型调用。E2E 库最终为 1 个测试项目、0 个生成任务、0 条调用记录、0 个供应商密钥。
- 首轮凭据文件不存在时为 155 通过、10 失败，均涉及供应商停用导致假模型用例不能进入执行链；补齐临时占位文件后全量通过。测试夹具显式化的改进已记录到路线图 P2-2，不将其掩盖为默认无配置环境已全绿。
- Python 测试有 15 条 FastAPI/Starlette、Alembic 弃用警告，不影响结果；浏览器检查只覆盖被测路由及状态，不等同于所有交互或真实供应商已验收。
- 测试服务已退出，3000/8000 无监听；本轮临时数据库、测试项目/素材、占位凭据、Pytest 临时目录及 Playwright 运行元数据已删除，结果保留于本文档。

## 运行边界

- 页面图片仍必须由用户逐页主动生成，没有无人值守整章生成入口。
- Redis/RQ 是正式 Worker 路径；开发环境未启动 Redis 时会切换到受控本地后台执行器，仍由用户的生成操作触发付费调用，普通查询不调用模型。
- 历史真实调用记录只作为溯源证据；当前正式流程不会为了文字相似度追加图片调用。
- 复杂图层、多用户协作和移动端不在 MVP 范围。
