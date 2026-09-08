# ADR V02-53A：Windows 桌面壳技术评估（Tauri 2 vs Electron）

> **ADR 状态：已批准（APPROVED，2026-09-06）。** 用户（组长/产品决策人）复核本文与全部 PoC/实机证据后，**批准 Tauri 2 桌面壳选型**（否决条件均未触发，见下「批准记录」）。本文件前身为 DeepSeek 的评估研究草案；批准后 §3.1 的「建议」升级为决议，Electron 保留为记录在案的备选（§3.2），不因批准而删除。
>
> - 任务：Issue #56 / `[0.2.0][V02-53A] Evaluate the Windows desktop shell architecture`
> - 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（`origin/master`）
> - 工作分支：`codex/v02-53a-desktop-shell-evaluation`（worktree `D:\自媒体\漫画工作流-deepseek-v02-53a`）
> - 约束：不修改代码/迁移/配置/测试/`docs/roadmap.md`/`docs/development-progress.md`/`plan.md`；不读取凭据、不安装软件、不调用真实供应商
> - 一手资料访问日期：**2026-08-30**（来源见 §8，仅官方页面）
> - 修订记录：2026-09-04 V02-54（Issue #114）补充 V02-53B PoC 输入；**2026-09-06 lead 终批（W-21），批准记录见下**
>
> **PoC 输入（V02-53B，2026-09-04）：** `apps/desktop-poc/`（Issue #110 / PR #111 / 合并提交 `203efad`）已在 Linux 实测冻结启动协议、进程归属与假模型闭环，其 D1–D9 结论**建议继续 Tauri 2**；否决条件 3（前端静态导出）拿到关键风险输入——工作台子树无法仅靠 flag 静态导出（动态段预渲染组合 + 工作台预渲染崩溃），方案 B（捆绑 node 跑 `next start`）未在 PoC 验证。
>
> ## 批准记录（W-21，2026-09-06）
>
> **决议：采用 Tauri 2 路线交付 Windows 桌面壳（`apps/desktop/`），Electron 不采用。** 批准依据 = 本文 §3.1 框架 + V02-53B PoC 证据（PR #111）+ V02-54 全系实现（PR #115/#116/#118）+ 2026-09-06 Windows 实机验收轮（`LAPTOP-TV9KT8RC`，PR #177/#178）：否决条件逐项复核——
>
> 1. **sidecar 打包**：已证伪为否决项。Windows PyInstaller onedir 冻结产物冒烟全过（W-11 RUN，`_internal/` 硬约束双平台成立）。
> 2. **WebView2 渲染兼容**：仪表盘级渲染 + 运行时 origin 注入实机 RUN（W-02 部分）；画布级渲染受 D5 前端形态约束，属实现轮事项，不构成平台级否决。
> 3. **前端静态导出**：flag 级静态导出的确定性阻塞维持原判（构建期预渲染组合不可知 + 工作台预渲染崩溃）；**批准选型不等同于选定 D5 路线**——静态导出正式改造 vs 方案 B（捆绑 node 跑 `next start`）由 W-15 实现轮提出设计方案后由 lead 定夺（本文 §3.1 倾向「方案 B 或混合形态」的输入不变）。
> 4. **Rust 维护能力**：由 lead 在本批准中裁定可接受；壳核心逻辑集中在 shell-core（库约 3,300 行）+ src-tauri 粘合（约 340 行），2026-09-06 实机轮已两平台原生跑绿。
>
> 批准后仍 open 的实现事项（不阻塞选型）：W-15 前端壳内形态、W-13/W-14 签名与自动更新（外部资源 BLOCKED）。

---

## 1. 背景与决策驱动因素

### 1.1 MangaFlow 当前形态（本地证据）

| 面 | 现状 |
| --- | --- |
| 前端 | Next.js 16（`apps/web/`），`next dev`/`next start` 绑定 `127.0.0.1:3000`（`apps/web/package.json`） |
| API | FastAPI（`uvicorn` 绑定 `127.0.0.1:8000`，`apps/api/`） |
| Worker | RQ worker（`run_worker.py`），可选（`dev:full`） |
| 数据库/队列 | SQLite 或 PostgreSQL（docker-compose）；Redis 可选（无 Redis 时用本地并发受限 worker） |
| 启动 | `scripts/start-dev.ps1`：Alembic 迁移 → 注入 `REDIS_URL`/`MANGAFLOW_PROXY_URL` → `npm run dev`（concurrently 起 web+api） |
| **API 代理** | `apps/web/next.config.ts`：`rewrites()` 把 `/api/v1/:path*` 代理到 `MANGAFLOW_API_ORIGIN`（默认 `http://127.0.0.1:8000`） |
| 进程所有权 | `scripts/owned_processes.py`：**Windows Job Object**（`CreateProcess`+`CREATE_SUSPENDED`、`AssignProcessToJobObject`、`TerminateJobObject`、持久化 journal、`recover_stopped_tree`） |
| 凭据 | API Key AES-GCM 加密存 DB + 主密钥文件；Vertex 服务账号走环境变量；`.env` |
| 安全基线 | P1-13 回环绑定、单用户本地工作台、上传/凭据硬化已合并 |

### 1.2 桌面壳必须解决的架构问题

1. **前端服务形态**：Next.js 的 `rewrites` 代理在**静态导出下不受支持**（官方 `nextjs.org/docs/app/guides/static-exports`「Unsupported Features」列明 rewrites/proxy 不可用）——桌面壳若静态导出，前端 `/api/v1/*` 请求必须改为直连 API（需前端改动）；若保留 `next start`（Node server），rewrites 继续可用（前端零改动）但需打包 Node runtime。
2. **后端 sidecar**：FastAPI + RQ Worker 是 Python（依赖含 Pillow 等，site-packages 体积大），安装版必须捆绑 Python runtime + 依赖并稳定启动/迁移/读写。
3. **进程生命周期**：壳必须拥有并管理 API/Worker/前端三个子进程——MangaFlow 已有 `owned_processes.py`（Python 侧 Job Object 方案）可复用为独立 helper。
4. **端口与单实例**：开发态固定 3000/8000；安装版应为动态端口 + 单实例锁。
5. **自动更新、签名、凭据、日志、崩溃恢复、离线**：见 §4–§7。

---

## 2. 备选方案对比：Tauri 2 vs Electron（官方一手资料）

对比只引用官方一手来源；社区包（electron-builder/electron-updater 等）不作为决策依据，仅在需要时注明「社区、非官方」。

| 维度 | Tauri 2 | Electron | 对 MangaFlow 影响 |
| --- | --- | --- | --- |
| **核心架构** | Rust Core（WRY/TAO）+ 系统原生 WebView（Windows = WebView2），不内嵌浏览器引擎 | Chromium 多进程（main/renderer/utility），**每 app 打包自己的 Electron/Chromium，不共享 DLL** | Electron 体积大但版本可控；Tauri 体积小但依赖系统 WebView2 |
| **打包体积** | shell 二进制小（~几百 KB）；**不打包浏览器**，依赖系统 WebView2 Runtime | 打包完整 Chromium（官方确认"每 app 自带"），安装包显著更大 | 中——MangaFlow 必然带 Python sidecar（~200MB+），shell 差异被稀释 |
| **内存** | 共享系统 WebView2（与 Edge 硬链接，官方：磁盘/内存双优化）；Rust core 占用小 | 每 app 独立 Chromium 进程树；官方性能文档强调减少 renderer、避免 main 阻塞 | 中——两者都跑同一 Next.js 前端，WebView 渲染差异小 |
| **WebView 兼容** | **Evergreen WebView2**：版本由系统更新（官方要求 app 做 API feature-detect）；Fixed Version 可固定但 +250MB | **Chromium 版本固定**（随 app 分发），前端兼容完全可控 | 中高——Canvas 分镜编辑器/动画需要稳定渲染 |
| **前端服务** | 需额外 sidecar `node.exe` 跑 `next start`，或静态导出（rewrites 失效需改前端） | **自带 Node**，main 进程可直接 spawn `next start`（前端零改动） | **高** |
| **Python sidecar** | 官方 `externalBin` 面向**单个外部二进制**（target triple 后缀、需 capability 权限）；该机制不限定 MSI/NSIS，但 Python 目录形态仍需 embeddable/PyInstaller 或资源目录方案 | Node `child_process` 官方能力 + 打包任意目录文件（安装版可捆绑 Python runtime 目录） | **高** |
| **进程所有权** | Rust 侧 `std::process::Command` + 需自行接 Windows Job Object（或调用 Python helper） | Node `child_process` + 可调用既有 Python `owned_processes.py` helper（同侧语言） | **高** |
| **自动更新** | **官方 updater plugin**：签名强制（`tauri signer`），Windows MSI/NSIS + `.sig`，静态 JSON/动态服务器，安装时 app 自动退出 | **官方 Squirrel.Windows + `autoUpdater`**（+ `update.electronjs.org` 服务，需 public GitHub + signed）；官方文档不提社区 electron-updater | 中（Tauri 更一体，Electron 官方路径较老） |
| **代码签名** | Windows 发布应对 app 与 sidecar 签名；Tauri 更新产物另有强制更新签名 | Windows 发布应签名；官方提供 `@electron/windows-sign`。Windows 证书类型与 SmartScreen 信誉要求应在发布 PoC 中验证 | 同（真实证书与发布信誉均 NOT RUN） |
| **崩溃恢复** | Rust panic + WebView2 崩溃处理 | main/renderer crash 事件、`app.relaunch` | 同 |
| **凭据存储** | 无官方 keyring 插件；沿用应用自身加密 | 同（keytar 已弃用） | 同——两者都宜沿用现有 AES-GCM 文件主密钥模式 |
| **离线能力** | 需系统 WebView2 Runtime（Win11 内置、Win10 大多已装；离线环境需 Standalone Installer） | 全打包，无外部 WebView 依赖 | 低（Win11 为安装目标） |
| **团队技能** | **引入 Rust**（当前仓库无任何 Rust 代码） | **TS 全栈**（项目前端同栈，壳代码用 TS） | **高** |

### 2.1 官方一手来源要点（访问日期 2026-08-30）

- **Tauri 2 sidecar**（`v2.tauri.app/develop/sidecar`）：`externalBin` 声明外部二进制，需 `-$TARGET_TRIPLE` 后缀（Windows 加 `.exe`）；执行需 capability 权限（`shell:allow-execute`）；**面向单二进制**。
- **Tauri 2 updater**（`v2.tauri.app/plugin/updater`）：签名强制不可关闭；Windows 产物 MSI/NSIS + `.sig`；安装时 Windows 强制 app 退出（`on_before_exit`）；`installMode` passive/basicUi/quiet。
- **Electron 官方架构/体积**（`electronjs.org/de/blog/webview2`）：Electron 从 Chromium 构建、不共享 DLL、**每 app 打包自己的 Electron**；WebView2 可选共享运行时、Windows 11 内置。
- **Electron 代码签名**（`electronjs.org/docs/latest/tutorial/code-signing`）：官方说明 Windows 与 macOS 分发需要代码签名，并提供 `@electron/windows-sign`；证书类型、硬件保存及 SmartScreen 信誉以发布 PoC 的当期 Microsoft 要求为准。
- **Electron 自动更新**（`electronjs.org/docs/latest/tutorial/updates`）：官方路径 Squirrel + `autoUpdater`；Windows 用 Squirrel.Windows `RELEASES`；`update.electronjs.org` 仅限 public GitHub，官方页面列出的签名前提针对 macOS，不外推为 Windows 服务端前提。
- **Microsoft WebView2 分发**（`learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution`）：Evergreen（自动更新、共享、Windows 11 内置）vs Fixed Version（**>250MB**）；Bootstrapper ~2MB（在线）vs Standalone（离线）；生产只能用 WebView2 Runtime（不能用 Edge Stable）；检测注册表 `pv` 键或 API。
- **Next.js 静态导出**（`nextjs.org/docs/app/guides/static-exports`）：`output: 'export'` → `out/`；**rewrites/proxy/ISR/Server Actions 等不支持**。

---

## 3. 决策：建议进入 PoC 的方向

### 3.1 建议

> **建议 Tauri 2 进入 PoC**，同时列出否决条件。

（注：以下权衡以 MangaFlow 的具体约束为限，不是对两种框架的一般性排名。）

**支持 Tauri 2 进入 PoC 的理由：**

1. **体积/内存/安全维护符合单用户本地工作台**：共享系统 WebView2（Windows 11 内置、Evergreen 安全更新由 Microsoft 推送），Rust core 占用小；capability 权限模型（IPC Router 逐权限）适合「本地 API + 外部供应商」的信任边界。
2. **官方自动更新一体**：Tauri 官方 updater 签名强制、Windows MSI/NSIS + `.sig`，一条链路；Electron 官方 Squirrel 路径较老、社区 electron-updater 超出官方一手范围。
3. **进程所有权有成熟先例**：仓库 `owned_processes.py` 已实现 Windows Job Object、所有权令牌与 journal，可作为实现参考；桌面壳仍须成为 helper 的直接父级并建立可验证的所有权握手，不能把“调用现有脚本”等同于自动获得所有权。
4. **静态前端路线可被限定验证**：前端可改为静态导出 + 直连 API，但当前 `MANGAFLOW_API_ORIGIN` 只服务 Next.js rewrite，不是 WebView 运行时注入机制；PoC 必须实现并验证 §4.2 的启动握手和运行时 origin 注入。

**否决条件（PoC 证实任一条则转向 Electron）：**

1. **Python sidecar 无法以 Tauri 形态打包**：PoC 无法把 FastAPI+Alembic+RQ Worker（依赖 Pillow 等）以可接受的单二进制（PyInstaller `--onefile`）或 embeddable 形态稳定启动、迁移与运行；且 Electron 的目录捆绑可稳定承载它。
2. **WebView2 渲染兼容不可接受**：Next.js 16 前端（Canvas 分镜编辑器、动画）在目标用户机器上的 WebView2 Evergreen 版本渲染异常，feature-detect 无法规避；Electron 固定 Chromium 可规避。
3. **前端静态导出改造不可行**：`rewrites` 失效后，前端 `/api/v1/*` 直连改造（base URL 注入）在 PoC 中证伪（如鉴权/CORS/相对路径问题无法收口），而 Electron 自带 Node 可零改动跑 `next start`。（V02-53B 实测输入：静态导出存在确定性阻塞——`output:"export"` 要求每个动态段 ≥1 构建期预渲染组合而真实项目 id 不可知，且工作台组件树服务端预渲染崩溃；方案 B 未验证。见 `apps/desktop/README.md` §4（V02-54 起路径；PoC 时为 `apps/desktop-poc/README.md`）。）
4. **Rust 维护能力不可接受**：项目团队无 Rust 维护能力，Tauri 壳层（升级、修复、签名）持续成本不可接受。

### 3.2 明确「不选 Electron」不等于「否决 Electron」

Electron 是有效备选：自带 Node（`next start` 零改动）、目录捆绑 Python sidecar 直接、TS 技能连续、Chromium 版本可控。其劣势是安装包/内存显著更大、官方自动更新路径较老。**在 PoC 中把 Tauri 的 Python sidecar 与前端静态导出作为最高风险项优先验证**；若 §3.1 否决条件触发，Lead 可直接切换到 Electron 方向而无需重做架构评估。

---

## 4. 关键架构问题（无论选型都成立）

### 4.1 开发态与安装版差异

| 面 | 开发态（现状不变） | 安装版（壳负责） |
| --- | --- | --- |
| 前端 | `next dev`（热重载、固定 3000） | `next start`（Node server，`rewrites` 保留）**或** 静态导出 + 直连 API（若采纳 Tauri 否决条件 3 未触发） |
| API | `uvicorn --reload`（固定 8000） | `uvicorn`（无 reload，动态端口） |
| Worker | `dev:full` 可选 | 默认随壳启动（无 Redis 时本地受限 worker） |
| 端口 | 固定 3000/8000 | **动态端口**（127.0.0.1 绑定）+ 单实例互斥 |
| 配置 | `.env`（项目根） | 用户数据目录（`%APPDATA%/MangaFlow/`）配置，`.env` 由安装/首启生成 |
| 主密钥 | 项目根密钥文件 | 用户数据目录 + ACL 收紧 |
| 数据库 | 项目 `storage/` | 用户数据目录 `data/`（升级不删） |

### 4.2 前端服务形态（PoC 最高风险之一）

- **方案 A（推荐先验）**：静态导出 `out/` + 前端直连 `127.0.0.1:<api_port>`。启动协议冻结为：壳先启动 API helper；helper 绑定动态端口后，通过仅本次启动有效的所有权令牌和本地握手通道返回 `{pid, api_origin, owner_token, journal_path}`；壳验证 PID、令牌与 journal 后，才创建 WebView。WebView 首次业务请求前，通过 Tauri command/invoke（Electron 则用 preload/contextBridge）读取 `api_origin`，调用 `lib/api.ts` 的运行时 setter。不得依赖构建期 `NEXT_PUBLIC_*`，也不得把 `MANGAFLOW_API_ORIGIN` 当作运行时配置，因为后者当前只控制 Next.js rewrite。API CORS 只允许该桌面 origin 与回环动态端口。
- **方案 B**：安装版捆绑 Node 运行 `next start`（保留 `rewrites`，前端零改动）。Tauri 需额外 `node.exe` sidecar；Electron 自带 Node。
- PoC 先验 A；A 失败则 B。

### 4.3 Python sidecar（PoC 最高风险之二）

- **形态**：PyInstaller `--onefile` 单二进制（与 Tauri `externalBin` target triple 命名兼容）或 Windows Python embeddable 目录 + 预装 site-packages（CI 冻结版本构建）。
- **必须稳定**：`alembic upgrade head`、SQLite 读写、FastAPI 启动、RQ worker 本地模式、上传 Pillow 安全面（像素/解压炸弹）、`storage`/`uploads` 目录定位（相对用户数据目录而非安装目录）。
- **进程归属**：壳直接 spawn helper，并把 helper 加入由壳持有、设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的根 Job Object；helper 通过带 owner token 的 readiness 握手返回真实 PID/journal，壳验证后才允许 helper 启动 API/Worker/前端。`owned_processes.py` 是可复用的实现模式，不代表被调用后天然具备壳级所有权。

### 4.4 进程所有权与端口

- 以 `owned_processes.py` 的 Job Object、owner token、journal 和恢复逻辑为实现参考；壳必须直接拥有 helper，并以根 Job Object 的 `KILL_ON_JOB_CLOSE` 覆盖壳异常退出。helper 只能在 readiness 握手被壳验证后启动其子进程，避免出现壳尚未取得所有权便留下孤儿进程的窗口。
- 端口：helper 让服务直接绑定 `127.0.0.1:0` 并从已绑定 socket/服务回报取得实际端口，禁止“先探测空闲端口、释放、再绑定”的 TOCTOU 流程。API origin 按 §4.2 注入前端；单实例互斥体防多开。

### 4.5 凭据、日志、崩溃恢复

- 凭据：沿用现有 AES-GCM 文件主密钥（`credential_crypto.py`），壳只负责把主密钥/`.env` 放到用户数据目录并设置 ACL；**不引入系统凭据管理器**（两者均无官方键值方案，非官方 keyring 不纳入）。
- 日志：壳日志 + API/Worker 日志统一写 `%LOCALAPPDATA%/MangaFlow/logs/`，按进程分文件、轮转、可导出（对齐 V02-54「日志导出」）。
- 崩溃恢复：壳监控 API/Worker health（探活），崩溃自动重启；Worker 复用既有租约恢复（P0-1）；壳自身崩溃重启后 `recover_stopped_tree` 恢复/清理子进程。

> **范围修订（2026-09-08，lead 决策，Issue #264/#265）：** 0.2.x 桌面交付（WPF 原生客户端为唯一正式客户端，见 2026-09-07 分支决策表）实际交付的崩溃恢复契约是「检测 + 干净收尾 + 一键重连」，**不含自动重启**：
>
> - native-host（WPF leg）以 250ms 轮询 `child.try_wait()` 监控 helper 进程退出——OOM、外部 kill、helper 崩溃均在 250ms 内检出；退出时执行完整收尾（进程树停止、日志 `stopped` 里程碑、journal `mark_stopped`），以非零码退出并报「local API process exited; restart the connection」。
> - WPF 客户端轮询 `backend.IsRunning`，后端中途退出即显式切到断开横幅「本地服务已断开 · 点击重新连接恢复工作，已有数据保留」，`Reconnect` 一键重跑完整握手取得新 origin。
> - **出范围（0.2.x）**：自动原地重启（重握手 + 新 origin 再注入）与稳态 loopback 健康探活。理由：自动重启要求新的进程生命周期设计（跨 L3 边界，需独立设计轮）；「挂着但僵死」的进程由每请求超时 + 手动重连路径覆盖；§5.2 的「崩溃后自动重启成功」验收以「崩溃后检测成功且一键重连恢复」为准解释。
> - 陈旧 runtime 会话目录（`runtime/mangaflow-desktop-<token>/`）自 2026-09-08 起由会话启动清扫回收（终态 + 24h 宽限，见 `shell-core/src/protocol.rs` `sweep_runtime_dirs`）。

---

## 5. PoC 边界、固定验收指标、回滚方案与发布风险

### 5.1 PoC 边界（只验证，不迁移正式业务代码）

- 独立 worktree/分支，不触碰 `apps/api`/`apps/web` 业务代码（前端 base URL 改动仅以可丢弃补丁验证，不合并）。
- 打包一个**最小可运行壳**：壳 + Python sidecar（打包后 API 启动 + SQLite 迁移）+ 前端（静态导出 或 next start）+ 本地 worker。
- 用假模型通道验证端到端「生成 → 候选」闭环（不调用真实供应商）。
- **不做**：真实 EV 签名、真实自动更新服务器、安装包分发。

### 5.2 固定验收指标（PoC 通过门槛）

| 指标 | 门槛 |
| --- | --- |
| 冷启动（双击到可生成页面） | ≤ 15s |
| 空闲内存（无任务） | ≤ 600MB（Windows 11 x64） |
| 安装包体积 | ≤ 400MB（含 Python sidecar） |
| 100 节点画布 FPS | 按项目既有 `architecture.md:131` 固定采样窗口通过 |
| 子进程归属 | 壳退出后无残留进程（Job Object 验证）；崩溃后自动重启成功（2026-09-08 修订：按 §4.5 范围注记，以「崩溃后 250ms 内检出 + 干净收尾 + 一键重连恢复」为准，自动重启出 0.2.x 范围） |
| 数据不丢 | 升级/卸载/重装不删除用户数据与素材（`%APPDATA%/MangaFlow/`） |
| 离线可用 | 无网络能启动 + SQLite 生成（假模型） |

### 5.3 回滚方案

- PoC 失败 → 按 §3.1 否决条件转向 Electron 重做 PoC，或**维持 Web 工作台现状**（桌面壳不阻塞主产品线；V02-55 发布门禁独立）。
- 壳代码全部为新增，不迁移正式业务代码；回滚 = 丢弃壳 worktree，主仓库零影响。
- 用户数据（数据库/素材/凭据）在壳目录与既有 `storage/` 之间**不迁移**直到 PoC 通过并获 lead 批准。

### 5.4 发布风险

- **签名**：未签名安装包可能触发 SmartScreen/未知发布者警告；适用证书类型、信誉建立与 CI 签名集成需在发布 PoC 中按当期 Microsoft 要求验证。→ NOT RUN（真实签名未验证）。
- **自动更新**：Tauri 官方 updater 依赖自有更新服务器 + 签名密钥管理；Electron 官方 Squirrel 依赖 RELEASES 托管。→ 更新服务器行为 NOT RUN。
- **WebView2 缺失（Tauri）**：Windows 10 少数机器需 Bootstrapper/Standalone 安装（离线需打包 ~100MB+ standalone）。→ 安装行为 NOT RUN。
- **Python sidecar 兼容**：PyInstaller/embeddable 打包后第三方依赖（SQLAlchemy/Pillow）平台行为差异。→ 需 PoC 验证，标记高风险。

---

## 6. 后续 PoC Issue 可直接采用的验收矩阵

| 编号 | 层 | 场景 | 类型 |
| --- | --- | --- | --- |
| D1 | 打包 | 壳可构建 Windows 安装包；安装/卸载/重装不删用户数据 | PoC 手动 + 脚本 |
| D2 | Python sidecar | 打包后 API 启动、`alembic upgrade head`、SQLite 读写、RQ 本地 worker 运行；假模型闭环「生成→候选→检查」 | PoC 自动 |
| D3 | 进程生命周期 | 壳直接拥有 helper；根 Job Object 启用 `KILL_ON_JOB_CLOSE`；带 owner token 的 PID/journal readiness 握手通过后才启动子服务；正常退出、壳崩溃、helper 崩溃均无残留 | PoC 自动（复用 `owned_processes` 的实现模式与测试夹具） |
| D4 | 端口/单实例 | 服务原子绑定动态端口、并发启动无 TOCTOU；单实例互斥；WebView 首次请求前通过 invoke/preload 注入运行时 API origin；不依赖 `NEXT_PUBLIC_*` 或 Next rewrite | PoC 自动 |
| D5 | 前端形态 | 静态导出加载 + 直连 API（CORS 验证）；或 next start + rewrites 保留 | PoC 自动 |
| D6 | 凭据/日志/数据 | 主密钥文件 ACL；日志轮转导出；用户数据目录定位 | PoC 自动 |
| D7 | 性能门禁 | 冷启动、空闲内存、安装包体积、100 节点画布 FPS（固定采样窗口） | PoC 自动（`architecture.md:131` 窗口语义） |
| D8 | 自动更新（未签名） | 更新流连通性（下载/校验/安装提示）；真实签名/服务器 NOT RUN | PoC 手动 |
| D9 | 安全 | 上传安全面（像素/解压炸弹/总量）、凭据不落日志、路径穿越在壳目录下不退化 | PoC 自动 |
| D10 | 决策门 | 对比 §3.1 否决条件逐项核查；输出最终 ADR 决议 | lead 复审 |

---

## 7. NOT RUN / UNKNOWN 边界（如实标记）

1. **真实 Windows 签名未验证**：证书采购、`signtool`/Tauri signer/`@electron/windows-sign` 的真实签名与 SmartScreen 信誉行为 NOT RUN。
2. **真实自动更新未验证**：Tauri updater 服务器、Squirrel RELEASES 托管、`update.electronjs.org`（需 public GitHub）均 NOT RUN。
3. **真实安装/升级/卸载未验证**：NSIS/WiX MSI/Squirrel 安装器行为、WebView2 Runtime 缺失时 Bootstrapper/Standalone 安装、离线环境均 NOT RUN（本环境未安装任何框架、未构建安装包）。
4. **真实性能数字未验证**：本 ADR 的验收指标是**建议门槛**，非实测；Tauri/Electron 的实测内存/体积/FPS 需 PoC 固定窗口测量。
5. **真实供应商与真实 PostgreSQL 未运行**：PoC 用假模型与 SQLite；真实 PG/Redis 桌面化验收沿项目既有边界。
6. **社区包不作为决策依据**：electron-builder/electron-updater/keytar 等社区方案已注明非官方，未纳入对比结论；其能力仅作已知现状提及。

---

## 8. 引用来源（官方一手，访问日期 2026-08-30）

| 来源 | 页面 | 用途 |
| --- | --- | --- |
| Tauri 2 官方 | `https://v2.tauri.app/develop/sidecar/` | sidecar 机制、target triple、capability 权限 |
| Tauri 2 官方 | `https://v2.tauri.app/plugin/updater/` | 自动更新、签名强制、Windows 产物/安装行为 |
| Electron 官方 | `https://www.electronjs.org/de/blog/webview2` | 架构/体积/进程模型（每 app 自带 Chromium） |
| Electron 官方 | `https://www.electronjs.org/docs/latest/tutorial/code-signing` | Windows 代码签名与 `@electron/windows-sign` |
| Electron 官方 | `https://www.electronjs.org/docs/latest/tutorial/updates` | Squirrel + autoUpdater、update.electronjs.org、Windows RELEASES |
| Electron 官方 | `https://www.electronjs.org/docs/latest/tutorial/performance` | 内存/主进程不阻塞/打包指导 |
| Microsoft 官方 | `https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution` | WebView2 Evergreen/Fixed Version、Bootstrapper/Standalone、检测、生产限制 |
| Next.js 官方 | `https://nextjs.org/docs/app/guides/static-exports` | 静态导出配置与不支持特性（rewrites/proxy 等） |

> 以上页面均于 **2026-08-30** 访问并据内容引用。未使用任何非官方/社区页面作为决策依据。
