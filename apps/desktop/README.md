# MangaFlow V02-54 桌面壳（`apps/desktop/`，自 V02-53B PoC 提升）

> 状态：**技术选型已批准（W-21，2026-09-06，Tauri 2 路线）。** 契约
> `docs/adr/v02-desktop-shell-evaluation.md` 已由 lead 终批（批准记录见该文头部）；
> 批准后 open 的实现事项：W-15 前端壳内形态（静态导出正式改造 vs 方案 B，实现轮提出
> 设计后定夺）、W-13/W-14 签名与自动更新（外部资源 BLOCKED）。
> 来源：V02-53B 可丢弃 PoC（Issue #110 / PR #111 / `203efad`，原 `apps/desktop-poc/`）；
> V02-54（Issue #114）按交付目录策略 A 将其整体提升为 `apps/desktop/` 并继续交付推进。
> `apps/api`/`apps/web` 业务代码零改动；本目录为纯新增；选型已定，回滚预案保留在 ADR §3.2。

## 1. 目录地图

```
apps/desktop/
├── sidecar/
│   ├── mangaflow_desktop_helper.py  # sidecar helper：冻结启动协议的 Python 侧实现
│   │                                #   （原子绑定 127.0.0.1:0 → journal → READY 行 →
│   │                                #    stdin GO 门控 → uvicorn/静态 stub）
│   │                                #   stub 模式（无三方依赖）供 Rust 测试；app 模式跑真实
│   │                                #   apps/api：alembic upgrade head + SQLite + 假模型通道
│   └── fake_channel.py              # 假模型通道：复用仓库验收缝 app.worker_tasks._adapter，
│                                    #   种子目录/连接/密钥（AES-GCM 生产凭据路径），零外呼
├── shell-core/                      # Rust crate（无 GUI 依赖，沙箱可完整测试）
│   ├── src/protocol.rs              # token/journal/READY 行校验、回环 origin 校验
│   ├── src/ownership.rs             # 进程树所有权：Windows 根 Job Object
│   │                                #   (KILL_ON_JOB_CLOSE) / Unix PDEATHSIG + 进程组
│   ├── src/handshake.rs             # spawn → READY 校验 → GO → 健康探活 编排；
│   │                                #   helper stderr 重定向到统一日志目录（V02-54B）
│   ├── src/logs.rs                  # 统一日志目录（logs/）+ RunLog 里程碑 +
│   │                                #   按大小轮转（V02-54C：12 MiB 阈值、
│   │                                #   .1–.5 世代、不跟随符号链接）+
│   │                                #   ZIP 导出（V02-54B；目标/成员双向路径安全）
│   ├── src/picker.rs                # 本地文件/目录选择校验 + 会话已选能力表（V02-54B）
│   ├── src/ziparch.rs               # 零依赖 store-only ZIP 写入器 + CRC32（V02-54B）
│   ├── src/bin/shell-sim.rs         # 壳崩溃模拟器（握手完成后 SIGABRT 自杀）
│   └── tests/startup_protocol.rs    # 集成测试：握手/并发端口/拒绝/崩溃清树/强杀升级
│       tests/delivery_contract.rs   # 安装/卸载用户数据契约
│       tests/log_export.rs          # 日志导出矩阵（含 python3 zipfile 外部校验）
│       tests/log_rotation.rs        # 日志轮转矩阵（V02-54C：会话清扫/会话内轮转/世代保留）
│       tests/picker_policy.rs       # 选择策略/穿越拒绝/能力表矩阵
├── shell/shell-tools.html           # 壳自带工具页（V02-54B）：日志导出/文件选择触发面
├── src-tauri/                       # Tauri 2 最小壳（Windows 侧 cargo check 编译验证）
│   ├── src/main.rs                  # setup：握手成功后才建 WebView；初始化脚本注入
│   │                                #   window.__MANGAFLOW_API_ORIGIN__ + invoke 命令
│   │                                #   desktop_get_api_origin / desktop_health_probe /
│   │                                #   desktop_export_logs / desktop_pick_file /
│   │                                #   desktop_pick_directory / desktop_read_picked_file
│   │                                #   （rfd 原生对话框）；退出时停树并记 RunLog
│   ├── tauri.conf.json              # frontendDist=../dist/frontend；withGlobalTauri；
│   │                                #   bundle msi+nsis（未构建）
│   └── capabilities/default.json    # core:default（自定义命令无需额外权限）
├── patches/web-static-export.patch  # 可丢弃前端补丁（静态导出 + 运行时 origin）
├── scripts/
│   ├── run-sidecar-e2e.sh           # 真实 API + 假通道 生成→候选 闭环（pytest）
│   ├── test_sidecar_e2e.py          #   同上，Python 侧协议实现 + 端到端断言
│   ├── build-frontend-static.sh     # 一次性 worktree 应用补丁 → next build 导出 → 拷入 dist/
│   ├── verify-static-origin.mjs     # D5 浏览器级验证（Chromium）
│   └── package-sidecar.sh           # PyInstaller 打包（Linux 形态）
└── dist/                            # 构建产物（gitignore 除占位 index.html 与
                                     #   shell-tools.html；根 .gitignore 的 dist/ 规则
                                     #   以 !apps/desktop/dist/ 显式放行本目录）
```

## 2. 冻结启动协议（ADR §4.2/§4.4）

1. 壳生成 32 位 hex owner token，创建 `runtime/mangaflow-desktop-<token>/` 运行目录，
   **直接 spawn** helper（子进程），传入 `MANGAFLOW_DESKTOP_TOKEN` / `MANGAFLOW_DESKTOP_JOURNAL`。
2. helper 校验 token 与目录规范路径（对齐 `scripts/owned_processes.py` 的防link/防复用思路），
   **一次绑定** `127.0.0.1:0`（禁止先探测后绑定的 TOCTOU），跑 `alembic upgrade head`，
   原子写 readiness journal（仅身份字段：token/pid/port/origin/状态），stdout 打一行
   `MANGAFLOW_READY {json}`。
3. 壳校验 READY 行（token、PID 与子进程句柄一致、origin 必须回环）+ journal +（Linux）
   `/proc` starttime；**全部通过才**在 stdin 写 `MANGAFLOW_GO <token>`；任何不符 → 不建 WebView。
4. helper 收到正确 GO 才开始服务流量（此前 socket 只监听不 accept，探活必挂——
   e2e 测试同时断言了「GO 前零字节服务」）。错误 token → exit 75，不服务。
5. 所有权：Windows = 壳持根 Job Object（`KILL_ON_JOB_CLOSE`），按 `scripts/owned_processes.py`
   `start_python` 纪律实现 **`CREATE_SUSPENDED` 挂起创建 → assign → 恢复初始线程**：
   helper 的第一条指令执行前已进入 Job，V02-53B 骨架的 spawn→assign 竞争窗口已收口；
   任何一步失败即终止仍挂起的子进程（fail-closed）。壳侧 journal 在 spawn 之前由
   `RuntimeLayout::create` 写入（state=created + token + shell pid），比 owned_processes
   「resume 前落盘」更早，可证明运行目录归属。**Windows 实机运行行为截至本轮仅由
   shell-core 测试覆盖部分路径**（见 `ownership.rs` 模块文档），完整 D3 复验前不得称作
   生产已验证。**停止语义（两平台一致：先协作后强杀）**：壳 `stop(grace)` 先关闭 helper
   的 piped stdin——helper 内的 EOF 看护线程（`mangaflow_desktop_helper.py`
   `_start_stdin_eof_watch`）随之对自身 `raise_signal(SIGTERM)`：uvicorn 接管前走 `sys.exit(0)`
   handler，接管后走 uvicorn 自身的优雅退出（serve 循环经 FastAPI lifespan shutdown 收尾）；
   宽限期内未退出才升级强杀——Linux = 进程组 SIGKILL（协作阶段 Unix 仍同时向进程组发
   SIGTERM，覆盖不监看 stdin 的后代进程；升级阶段仅 SIGKILL），
   Windows = `TerminateJobObject`（job 退出码 125）。崩溃路径不变：壳死亡即 Job 句柄
   关闭，`KILL_ON_JOB_CLOSE` 无协作出杀全树。Linux 等价（沙箱实测）= spawn 前
   `PR_SET_PDEATHSIG` + helper `setsid`。**Windows 实机 2026-09-06 已验证**（debug 壳
   真实关窗协作停机 exit 0；`taskkill /F` 强杀壳后 helper 树含 launcher 链孙进程 3 秒内
   全灭）。**READY PID 身份（Windows launcher 链）**：CPython 3.12 venv 的 `python.exe`
   是 launcher——真实解释器作为子进程运行，READY 宣布的 pid 是孙进程；壳接受「直接
   子进程 pid 或（`IsProcessInJob`）位于根 Job 内的 pid」——安全不变量保持为「宣布者
   必须属于本壳拥有并可整树杀死的进程集合」，秘密 token 仍把关伪造（回归测试
   `ready_pid_from_a_launcher_interpreter_chain_is_accepted_via_job_membership`）。
   **每个壳退出路径（正常/崩溃/超时）都必须
   清树**，见 `tests/startup_protocol.rs::shell_crash_still_kills_helper_and_descendants`。

## 3. 运行方式（Linux 沙箱与 Windows 实机均实测过；安装器链仍 NOT RUN）

需要 rustup stable（1.98.1，含 `x86_64-pc-windows-msvc` std）在 PATH；系统 cargo 1.85
无 Windows std，Windows 目标检查会报 E0463。

```bash
# a. 假模型闭环 + 协议 e2e（自动建 .venv-desktop；Windows 原生通过 2026-09-06，
#    停止通道为生产 stdin-EOF 协作停机，退出码 0）
apps/desktop/scripts/run-sidecar-e2e.sh

# b. Rust 协议/所有权测试（需 python3；Windows 上默认解析为 `python`，
#    除非显式设置 MANGAFLOW_DESKTOP_PYTHON）
cd apps/desktop/shell-core && cargo test   # 49 项两平台原生全绿（2026-09-06 Windows 实机复跑）

# c. Windows 目标编译校验（在本 Linux 机即可；需要 llvm-rc 于 PATH）
cd apps/desktop/src-tauri && cargo check --target x86_64-pc-windows-msvc

# d. 静态导出（一次性 worktree，业务树零改动）
apps/desktop/scripts/build-frontend-static.sh

# e. D5 浏览器级验证（需 d 的产物 + .venv-desktop + chromium）
MANGAFLOW_DESKTOP_PYTHON=$PWD/.venv-desktop/bin/python \
  node apps/desktop/scripts/verify-static-origin.mjs

# f. sidecar PyInstaller 打包（Linux 形态见脚本；Windows 形态 2026-09-06 实测：
#    onedir + `_internal/`(alembic.ini+migrations) 冻结产物握手→健康→dashboard→协作停机全过）
apps/desktop/scripts/package-sidecar.sh
```

真实壳冒烟（**2026-09-06 Windows 实机 RUN**，`LAPTOP-TV9KT8RC` + WebView2 Evergreen）：

```bash
MANGAFLOW_DESKTOP_PYTHON=.venv-desktop/bin/python \
  MANGAFLOW_DESKTOP_HELPER=apps/desktop/sidecar/mangaflow_desktop_helper.py \
  MANGAFLOW_DESKTOP_API_ROOT=apps/api \
  cargo run --manifest-path apps/desktop/src-tauri/Cargo.toml
```

假模型通道开关（#275）：壳本身**不再**无条件给 helper 传 `--fake-channel`。
仅当启动器显式设置 `MANGAFLOW_DESKTOP_FAKE_CHANNEL=1` 时才追加该旗标（与 ADR
`native-windows-client` 的"测试通过显式环境开关启用 stub"契约一致；直接调用
helper 的 e2e 脚本不受影响，它们本就显式传参）。`start-desktop.cmd` 与安装形态
不设置该变量——真实 provider 启用后不会被开发 stub 静默截胡。

实机证据（debug 构建，基线 `lead/rc-closure`）：完整握手（挂起创建→Job→resume→READY→
token/journal/回环校验→GO→健康）通过后 WebView2 建窗；仪表盘渲染 + 运行时 origin 注入
实取 sidecar API 数据（模型目录/连接统计）；单实例第二实例立即退出（exit 0）且最小化窗口
被还原聚焦（`unminimize` 修复）；关窗协作停机 exit 0 + RunLog `stopped`；`taskkill /F`
强杀壳后 helper 树（含 launcher 链孙进程）3 秒内全灭（`KILL_ON_JOB_CLOSE`）；用户数据落
`%LOCALAPPDATA%\com.mangaflow.desktop\{data,storage,uploads,runtime,logs}`。

## 3.1 WPF 原生客户端的标准构建/启动入口（2026-09-09）

`apps/desktop/native/`（WPF）与 `apps/desktop/shell-core/`（Rust 宿主 `native-host.exe`）
是两个独立构建产物。WPF 的 `Services/NativeBackend.cs` 启动 sidecar 时**优先加载 WPF
输出目录内的 `native-host.exe`**，仅当输出目录缺失时才回退到
`shell-core/target/debug/native-host.exe`。因此单独 `dotnet build` 不会刷新输出目录内
的宿主，可能运行旧宿主——**标准入口是 `apps/desktop/scripts/start-native.ps1`**：

```powershell
# 构建 Rust 宿主 + WPF（Release），把宿主复制进 WPF 输出目录并做 SHA-256 校验
powershell -ExecutionPolicy Bypass -File apps/desktop/scripts/start-native.ps1 -BuildOnly
# 同上并启动 WPF 客户端（交互窗口）
powershell -ExecutionPolicy Bypass -File apps/desktop/scripts/start-native.ps1
```

- 脚本内 cargo/dotnet 任一失败即中止（`$ErrorActionPreference='Stop'`）；复制后比对源/
  目标 SHA-256，不一致即报错，**不会悄悄复用旧宿主文件**。
- 验收记录宿主来源：`NativeBackend.HostPath`/`HostSha256` 在每次启动时记录实际加载的
  宿主绝对路径与哈希（取自输出目录或回退目录）。
- 不要用“删除输出目录里的旧宿主来触发 debug 回退”的方式保证新鲜度——始终通过脚本或
  等价的 cargo build → dotnet build → copy + 校验链。
- 原生回归（含 UI 检查）：`dotnet run --project apps/desktop/native-tests -- --render`。

## 4. 验收矩阵 D1–D9（ADR §6）

| 编号 | 层 | 结论 | 证据 / 边界 |
| --- | --- | --- | --- |
| D1 | 打包 | **RUN（双安装包构建 + NSIS 实机装/卸，2026-09-06）/ MSI 安装步 NOT RUN** | `tauri build` 于 Windows 实机产出 MSI（`MangaFlow_0.1.0_x64_en-US.msi`）与 NSIS（`MangaFlow_0.1.0_x64-setup.exe`，内嵌真实静态导出）；NSIS 实机脚本验证：静默安装→用户数据 222 文件逐字节不变→HKCU 卸载项注册→静默卸载→安装目录与注册表项清除、用户数据仍逐字节不变（§5 契约实测成立，脚本保留于验收记录）。MSI 静默安装（per-machine）需管理员授权未提权，NOT RUN。安装/升级/重装数据保留的升级路径实机（覆盖安装同一包）以 NSIS 装卸两态覆盖；带 schema 升级的跨版本升级路径仍欠。 |
| D2 | Python sidecar | **RUN（Linux + Windows 双形态，含 PyInstaller 冻结产物）** | `run-sidecar-e2e.sh`：真实 `app.main:app` 经 `alembic upgrade head`（30 个迁移到 head）+ SQLite 读写 + 本地 worker（无 Redis 时 API 内 LOCAL_EXECUTOR，即安装版默认形态）+ 假通道完成「生成→候选→采用→PNG 落盘→`/content` 可取」（**2026-09-06 Windows 实机原生复跑通过 11.1s**，停止通道为生产 stdin-EOF 协作停机）。PyInstaller onedir 冻结产物双平台实测（Linux 116MB / Windows 2026-09-06）：完整握手→GO→健康→真实 dashboard API→协作停机 exit 0；`alembic.ini`+`migrations` 必须放 `_internal/` 的硬约束在 Windows 形态同样成立。RQ/Redis Worker 进程形态仍 NOT RUN。 |
| D3 | 进程生命周期 | **RUN（两平台原生：Linux 沙箱 + Windows 实机 shell-core 集成 + Windows 实机完整 debug 壳）/ 安装器链仍欠** | `cargo test`（两平台原生，Windows 实机 2026-09-06 全 49 项）：握手全链、错误 GO 拒绝 exit 75、并发双 helper 端口不冲突、`shell-sim` 崩溃后 helper+孙进程全灭、无协作者强杀升级、壳在 spawn 前写入归属 journal、launcher 链 READY pid 经 Job 成员验收。Windows 路径按 `scripts/owned_processes.py` `start_python` 纪律实现：`CREATE_SUSPENDED` 挂起创建 → 建 Job（`KILL_ON_JOB_CLOSE`）→ assign 仍挂起的子进程 → 快照枚举初始线程后 `ResumeThread`；任一步失败即终止仍挂起的子进程（fail-closed）。**完整 debug 壳 Windows 实机 2026-09-06**：真实关窗协作停机（RunLog `stopped` exit 0）与 `taskkill /F` 崩溃清树（全树 3 秒内灭）均实测。真实安装器链 + 多开下的完整 D3 复验仍欠。 |
| D4 | 端口/单实例 | **RUN（端口+注入+单实例多开实机）/ WebView2 缺失安装行为 NOT RUN** | 原子绑定 `127.0.0.1:0`（socket 先绑后报，无 TOCTOU；并发测试两 helper 端口必异）；WebView 建立前完成握手；运行时注入 = 初始化脚本同步写 `window.__MANGAFLOW_API_ORIGIN__` + invoke `desktop_get_api_origin` 双通道，不依赖 `NEXT_PUBLIC_*`（浏览器断言 `api_origin_env_free`）/不依赖 Next rewrite（D5 实测直连）。**单实例多开 Windows 实机 2026-09-06 RUN**：第二实例立即退出（exit 0）；最小化窗口经 `unminimize()+set_focus()` 修复后正确还原聚焦（修复前 `set_focus` 单独对最小化窗口无效——实机发现的真实缺陷）。 |
| D5 | 前端形态 | **RUN（机制验证，V02-53B 证据）/ 静态导出为「受限可行」** | `verify-static-origin.mjs`（Chromium）：静态导出页加载 → 注入 origin → 仪表盘**直连**动态端口 API（`/api/v1/projects/dashboard` 200，CORS 按桌面 origin 放行）→ 页面渲染，静态服务器 `/api/*` 零命中。**核心发现**：工作台子树无法只靠 flag 导出——`output:"export"` 要求每个动态段 ≥1 预渲染组合（真实项目 id 构建期不可知）且工作台组件树服务端预渲染崩溃；补丁以「poc 桩组合 + notFound stub + 删 3 个仅服务端页」换得壳级页面导出。**结论：静态导出路线需要正式的前端路由/组件改造（否决条件 3 的关键输入）；方案 B（捆绑 node 跑 next start，保留 rewrites）未被验证**。 |
| D6 | 凭据/日志/数据 | **RUN（目录+日志+凭据路径+日志导出+日志轮转）/ ACL NOT RUN** | 用户数据目录布局：`data/`（DB）、`storage/`、`uploads/` 均落 user-data（测试断言不落仓库）；V02-54B 起统一日志目录 `logs/`（壳 RunLog 里程碑 + helper/API/Worker stderr 按运行分文件），壳侧 `desktop_export_logs` 可归档（store-only ZIP + manifest.json）到用户可选路径；V02-54C 起**按大小轮转**：`shell-*.log` / `helper-*.stderr.log` 单文件达 12 MiB（< 导出 64 MiB 上限）rename 为 `.1`–`.5` 世代、超出删最旧——壳 RunLog 会话内轮转（写前检查、轮转后原打开路径继续写），helper stderr 由 helper 进程持有 fd，采用**跨会话轮转**（新会话 `RunLog::create` 清扫，取舍见 §6.4）；轮转不跟随符号链接、rename/删除不越 canonical logs 根。假通道密钥走生产 `credential_crypto` AES-GCM + 文件主密钥（`storage/.provider-credential-master-key` 自动生成）。Windows ACL 收紧 NOT RUN；轮转 Windows 实机行为 NOT RUN（Linux 实测，见 §6.4）；导出对单文件 64 MiB 上限仍跳过并在 manifest/report 记录。 |
| D7 | 性能门禁 | **NOT RUN（按约定）** | V02-52A N=20 全样本不存在、本轮明确不跑 V02-52B；仅记录参考值：假闭环 e2e 全程约 4.3s（含 28 个迁移），远优于 ADR 冷启动 ≤15s 建议线，但**非固定窗口测量、不作为门禁证据**。 |
| D8 | 自动更新（未签名） | **NOT RUN** | 未接 updater 插件、无签名密钥、无更新服务器（Issue 禁止真实签名/服务器）。 |
| D9 | 安全 | **RUN（PoC 面）/ 业务面沿用；CSP 有记录在案的债务** | journal 仅身份字段（测试断言字段集合，无命令/env/密钥）；token 由 OS CSPRNG 生成（Unix `/dev/urandom`、Windows `BCryptGenRandom`——后者编译验证、运行时 NOT RUN）；origin 回环强制（`verify_ready_line` 拒绝非 127.0.0.1）；GO 前零流量；注入串经 serde_json JSON 转义而非裸 format!；`tauri.conf.json` 设受限本地 CSP（`connect-src`/`img-src` 限 `self` + `http://127.0.0.1:*`，`object-src 'none'`、`frame-src 'none'`、`base-uri 'self'`）。V02-54B：RunLog 与日志导出 manifest 只写身份字段（测试断言不含命令/env/脚本路径）；本地文件选择拒绝 `.`/`..` 成分与符号链接，读取仅限会话内已选能力表成员（测试矩阵见 `tests/picker_policy.rs`）。**CSP 债务**：`script-src` 保留 `'unsafe-inline'`——Next 静态导出的内联引导脚本（`self.__next_f.push`）硬需求，去掉会白屏；`withGlobalTauri` 使壳命令暴露给壳内页面（页面全部为本仓库产出的静态导出/工具页，无第三方内容；CSP `default-src 'self'` 限制外链）；WebView2 对该 CSP 的实际执行行为 NOT RUN（本机无 WebView）。上传像素/解压炸弹/总量等安全面未改动（沿用 P1-13 既有实现与测试）。 |

说明：标注「V02-53B 证据」的行沿用 PoC 轮（PR #111 / `203efad`）实测记录，本目录提升
时未重跑这些环境受限项；能在本沙箱复跑的门禁见 §5。

### Windows 专属汇总（2026-09-06 实机后余项）

- **已转 RUN（`LAPTOP-TV9KT8RC`，Windows 11 10.0.26200 + WebView2 Evergreen，debug 构建）**：
  Job Object 实机全链（shell-core 49 项原生测试 + 完整壳启动握手/关窗协作停机/强杀清树）、
  WebView2 仪表盘级渲染 + 运行时 origin 注入实取 sidecar API 数据（工作台子树受 D5 静态
  导出约束，画布级渲染仍属 D5 改造范围）、单实例多开（第二实例门控 + 最小化还原修复）、
  rfd/文件选择的**策略层**（picker 矩阵 Windows 原生跑绿）、日志目录/RunLog/里程碑实机写入、
  PyInstaller Windows onedir 冻结 sidecar 冒烟。
- 仍 **NOT RUN**：WebView2 缺失/损坏安装行为（需可控卸载 Runtime）、WebView2 内
  `shell-tools.html` 工具页 invoke 与 rfd 原生对话框实机交互（壳内 UI 无工具页入口，协议/
  命令面过 Windows 原生测试）、MSI/NSIS 安装器构建与安装/升级/卸载实机、SmartScreen/签名、
  自动更新链路。缓解不变：编译门禁 + 配置契约测试，不以编译通过冒充实机验证。
- 否决条件核查（ADR §3.1；**已于 2026-09-06 随 W-21 终批逐项复核，决议见 ADR 头部「批准记录」**）：
  1. Python sidecar 打包：**已证伪为否决项**——Windows PyInstaller onedir 冻结产物冒烟全过（W-11 RUN，`_internal/` 硬约束双平台成立）。
  2. WebView2 渲染兼容：仪表盘级渲染 + 运行时 origin 注入实机 RUN（W-02 部分）；画布级渲染受 D5 前端形态约束，属 W-15 实现轮事项，不构成平台级否决。
  3. 前端静态导出：**发现确定性阻塞**（动态段预渲染组合 + 工作台预渲染崩溃），静态导出非 flag 级改动；方案 B 未在本 PoC 验证 → W-15 实现轮提出设计方案后由 lead 定夺（ADR 倾向「方案 B 或混合形态」输入不变）。
  4. Rust 维护能力：壳核心逻辑集中在 shell-core（library 约 3,323 行 Rust，其中
     `logs.rs` 约占 1,900 行——日志布局/轮转/导出是 V02-54B/C 后最大的单一模块；
     另有 tests/ 集成测试约 1,100 行）+ src-tauri 粘合（`main.rs` 约 338 行）；**已由 lead 在终批中裁定可接受**。
     （行数为 2026-09-06 powershell 实测口径：`src/**/*.rs` 去 `src/bin/`、`src-tauri/src`。）

## 5. 用户数据安全（安装/升级/卸载契约）

- **目录布局（安装版运行时）**：用户数据全部位于 Tauri `app_local_data_dir`
  （Windows = `%LOCALAPPDATA%\com.mangaflow.desktop\`）：`data/`（SQLite 数据库）、
  `storage/`（素材与凭据文件主密钥 `.provider-credential-master-key`）、`uploads/`、
  `runtime/`（单次启动的 owned 运行目录）。安装目录只含程序文件，不存用户数据。
- **升级**：安装器只替换安装目录内程序文件；数据库结构升级由 sidecar 启动时的
  `alembic upgrade head` 原地完成，不删除、不重置用户数据库；回滚沿用仓库 Alembic
  下行迁移边界。
- **卸载**：NSIS/WiX 卸载器默认只移除安装目录、注册表项与快捷方式，不触碰
  `%LOCALAPPDATA%` 用户数据。**契约：禁止任何 installer hook（NSIS
  PREUNINSTALL/POSTUNINSTALL 等）删除 `data/`、`storage/`、`uploads/` 或凭据**；
  删除用户数据的唯一途径是用户手动删除该目录。该契约由
  `shell-core/tests/delivery_contract.rs` 冻结（无 installer hooks、bundle 配置无
  删除指令、identifier 不被悄悄更换——它决定用户数据目录）。
- **凭据**：模型 API Key 沿用生产 AES-GCM 加密（`credential_crypto.py`）+ 文件主密钥，
  与数据库同在用户数据目录；journal 与日志只写身份字段，不写密钥/命令/env。
- **边界**：真实 MSI/NSIS 安装/升级/卸载行为 NOT RUN（本环境无法构建 Windows 安装包）；
  以上以配置契约与卸载器官方默认行为为据，实机验收（D1）欠。

## 6. V02-54B 日志导出与本地文件选择 + V02-54C 日志轮转（Linux 实测；Windows 实机 NOT RUN）

### 6.1 统一日志目录与导出（ADR §4.5）

- **目录**：`<user-data>/logs/`。每次运行的文件按 owner token 分名：
  `shell-<token>.log`（壳里程碑，JSON lines：`spawn` / `ready_verified` / `go_sent` /
  `healthy` / `stopped`，仅身份字段）与 `helper-<token>.stderr.log`
  （helper stderr——桌面形态下即 API/uvicorn/LOCAL_EXECUTOR Worker 输出）。
  壳在 spawn 时把 helper stderr 重定向到该文件（替换 V02-54 之前的
  `Stdio::inherit()`——GUI 壳没有可继承的控制台）。
- **导出**：invoke `desktop_export_logs`（**无参**——rfd 原生保存对话框是目标的唯一来源；
  #149 修复后不再接受 renderer 传入的 `destination` 参数，shell-core 默认拒绝已存在的
  目标文件，仅对话框确认覆盖后经由显式的 confirmed-overwrite 入口替换）。产物为
  store-only ZIP（零依赖写入器 `ziparch.rs`，CRC32 有已知向量测试）+ `manifest.json`
  （文件清单/跳过原因，仅身份字段）。写入先落 `*.pending` 再 rename（`.pending` 处
  植入符号链接同样拒绝，#150），失败不在目标路径留下半截归档。
- **双向路径安全**（测试 `tests/log_export.rs`，归档另经 python3 `zipfile`
  外部校验 CRC 与结构）：归档成员只收 `logs/` 内的常规文件——符号链接跳过
  不跟随、每个成员 canonical 路径必须仍在 canonical logs 根之下、单文件
  64 MiB 上限（超出跳过并记录；V02-54C 起轮转让常规日志稳定低于该上限，
  见 §6.4）；导出目标必须为绝对路径、不含 `.`/`..` 成分、父目录必须存在，
  且**最终成分**不得为符号链接/junction；父目录链本身**允许**符号链接/junction——
  canonical 化会解析全部 reparse point 后再做包含检查，经链接最终解析进用户数据根
  的目标同样被拒（`logs.rs` `validate_destination` 行为）。目标**默认不得已存在**
  （未经用户确认覆盖）、canonical 化后**不得位于用户数据根之内**
  （既防止把用户数据当导出目标
  覆盖，也防止归档自我包含）。轮转产生的世代文件（`*.log.1`…）是普通
  成员，随导出一起归档（测试 `tests/log_rotation.rs`）。

### 6.2 本地文件/目录选择

- **命令**：`desktop_pick_file(kind)`（`source_text` | `reference_image`）、
  `desktop_pick_directory`、`desktop_read_picked_file(path)`；对话框用 rfd
  原生实现，路径不经 WebView 表单输入——穿越在结构上无入口，校验再兜底。
- **策略对齐既有上传边界**（`shell-core/src/picker.rs`）：原作/正文 =
  TXT/Markdown（同 `sources.py` 后缀集）；参考图/素材 = PNG/JPEG/WebP
  （同 `uploads.py REFERENCE_IMAGE_TYPES`）；两者 ≤ 20 MiB（同
  `max_upload_bytes`）。像素/解压炸弹等图片校验仍由 API 服务端执行，壳侧
  只是前置对齐，不是替代。
- **能力表**：校验通过的路径（canonical 化、拒绝 `.`/`..`、拒绝符号链接、
  最终成分换名检测）进入会话级 `PickedRegistry`；`desktop_read_picked_file`
  每次调用都重查能力表并重跑完整校验，未注册路径一律拒绝——选择是一次
  能力授予，不是对路径字符串的永久授权。页面拿到字节后仍走既有
  `/uploads`、`/sources` 上传接口，服务端边界零改动。
- **触发面**：`shell/shell-tools.html`（壳自带工具页，构建脚本随静态导出
  一起拷入 `dist/frontend/`，壳内访问 `/shell-tools.html`；依赖
  `withGlobalTauri`，已启用）。Web 前端内嵌入口属静态导出/方案 B 改造
  （否决条件 3）范围，本轮不做。

### 6.3 门禁（Linux 可跑部分全绿，2026-09-04）

- shell-core `cargo test` 26 项（协议 5 + ziparch 3 + logs 2 + 契约 3 +
  日志导出集成 3 + 选择策略集成 5 + 启动协议集成 5）；src-tauri 与
  shell-core 过 Windows 目标 `cargo check`（新增 rfd 0.17 依赖）；
  `run-sidecar-e2e.sh` 假闭环 1 passed；`ruff check apps/desktop` 通过；
  `tests/test_pytest_collection_gate.py` 通过。
- 修复：V02-54 记录的 `dist/frontend/index.html` 占位实际未入库（根
  `.gitignore` 的 `dist/` 规则整树吞掉，`apps/desktop/.gitignore` 的
  反选无效），Windows 编译门禁从干净 clone 会失败；本轮补 `!apps/desktop/dist/`
  放行并入库占位页。

### 6.4 日志轮转（V02-54C，Linux 实测；Windows 实机 NOT RUN）

- **策略**：`shell-*.log` / `helper-*.stderr.log` 单文件达
  `ROTATION_THRESHOLD_BYTES`（12 MiB，**本轮自选值**，严格低于导出 64 MiB
  上限；ADR 只要求「轮转」，未给出数值区间）即 rename 为带序号世代
  `.1`–`.5`（`ROTATION_KEEP_GENERATIONS`），每次轮转先删最旧世代再自新
  到旧逐级 shift——每个目标位都是刚腾空的，普通 rename 在 Windows（无
  覆盖式 rename）上同样成立。
- **两种轮转时机，取舍写明**：①壳 RunLog（`shell-<token>.log`）**会话内
  轮转**——`RunLog::record` 写前检查活动文件大小，达阈值即关句柄、
  shift 世代、重开同一 base 路径，轮转后打开路径继续可写（shift 失败
  降级为继续追加、不丢里程碑行；重开失败则该行以错误返回且不写穿路径上
  的可疑条目，下次 record 先自动重试恢复句柄，路径修复后写入自愈）；
  ②helper stderr（`helper-<token>.stderr.log`）
  的句柄由 helper 进程持有整个会话（Windows 无法 rename 他进程打开的
  文件、Unix rename 会把 helper 后续写脱接到新 base），故采用**跨会话
  轮转**：每次会话启动（`RunLog::create`）在上一批文件必然关闭的时机清扫
  logs/ 并轮转超阈值文件。单会话内 helper stderr 仍可能超过阈值，此时由
  导出 64 MiB 上限兜底（跳过并记录）。
- **并发假设**：会话启动清扫假设同一 user-data 上**无并发壳实例**（D4
  单实例互斥体已接但实机多开 NOT RUN）；清扫无活跃性检查，若未来支持
  多开，需先按 `runtime/` 归属 journal 排除仍活跃会话的 base 再轮转。
- **清扫范围**：仅匹配 `shell-<32 hex>.log` 与 `helper-<32 hex>.stderr.log`
  （复用 token 校验）；`shell-notes.log` 之类外来名、世代文件与非日志
  文件一律不动（单元测试断言）。会话启动清扫 fail-soft：清扫失败不阻塞
  会话启动。
- **路径安全**：轮转只匹配 logs 根内的固定文件名模式，世代名由匹配到的
  文件名派生，rename/删除目标不可能越出 logs 根；rename 前有
  canonical 包含复查（与导出成员同款）。符号链接一律不跟随：base 路径
  上的链接跳过不轮转（也不移动链接本身），世代位上的链接改为 unlink
  （外链目标文件存活）。日志打开统一走 `open_append_regular`：打开前拒绝
  非常规条目并要求父目录 canonical 在 logs 根内（不向外创建任何文件），
  打开后复查句柄为常规文件且路径 canonical 仍在 logs 根内，不符即拒绝
  写入（检查后打开的置换窗口与导出侧同款残余风险，user-data 为每用户
  目录，ACL 收紧 NOT RUN）。RunLog 写路径全程无 panic：序列化 fallible、
  互斥锁中毒经 `into_inner` 恢复、重开失败后 record 报错而非 panic。
- **门禁（Linux 实测，2026-09-04）**：shell-core `cargo test` 33 项全绿
  （新增 logs 单元 4：世代 shift/保留、符号链接不跟随与越根跳过、清扫
  只动超阈值 base 文件、RunLog 拒绝符号链接路径；新增
  `tests/log_rotation.rs` 集成 3：会话启动清扫上会话 shell/helper 超阈值
  日志、会话内轮转后打开路径继续写 + 世代保留 ≤5、世代文件随导出归档
  且不触发 64 MiB 跳过；超阈值用稀疏文件 `set_len` 构造，真实 12 MiB
  默认阈值零成本参与）；shell-core 与 src-tauri 双双通过 Windows 目标
  `cargo check`（rustup stable 1.98.1 + llvm-rc 19）。**轮转 Windows
  实机行为 NOT RUN**（含 rename-on-open 的 Windows 语义实测），归入 §7。
  **修复轮一（同日，审阅后）**：写路径去 panic 化与 open 后复查落地，
  新增锁毒恢复（人为投毒后 record 仍落盘）与 `open_append_regular`
  越根/符号链接父目录拒绝（打开前不向外创建文件）两项单元测试。
  **修复轮二（superseding，同日）**：①重开失败不留死句柄——record 以
  错误返回，下次 record 自动重试恢复句柄（测试：rotation 关句柄窗口期
  在 base 植入符号链接 → record 返回 `Err` 不 panic 且不写穿链接，路径
  修复后写入自愈）；②会话启动清扫 fail-soft（清扫失败不阻塞建会话）；
  ③清扫模式收紧为 `shell-<32hex>.log` / `helper-<32hex>.stderr.log`
  （`shell-notes.log` 等外来名不动，测试断言）；④12 MiB 阈值表述改为
  「本轮自选值」，删除「ADR 建议 8–16 MiB」的不实表述（ADR 无数值
  区间）；⑤记录清扫的「无并发壳」假设。shell-core `cargo test` **36 项**
  全绿，双 crate Windows 目标 `cargo check` 复跑通过。

## 7. NOT RUN 汇总（诚实边界）

> 完整剩余项状态目录（每项 ID / 描述 / 依赖环境 / RUN·NOT RUN·BLOCKED / 阻塞原因，
> 含打包、签名、更新、性能与治理项）见 `docs/v02-windows-leftover-status.md`（V02-54D）。

1. Windows 实机全链路：**2026-09-06 实机轮已覆盖**完整 debug 壳的握手/渲染（仪表盘级）/
   单实例多开/崩溃清树/协作停机/用户数据布局/日志链路与 PyInstaller 冻结 sidecar；**仍欠**：
   WebView2 缺失/损坏安装行为、`shell-tools.html` 工具页 invoke 与 rfd 对话框的壳内实机交互
   （壳内 UI 无入口；策略/命令面已由 Windows 原生测试覆盖）、MSI/NSIS 安装/升级/卸载实机、
   签名、自动更新、多开下的会话清扫竞态。
2. RQ/Redis worker 进程形态与 Independent Worker（按 Issue 约束不装 Redis/Docker/Postgres；本地 LOCAL_EXECUTOR 已验，双平台）。
3. V02-52A N=20 性能门禁、Lighthouse/FPS（归 V02-52B；#28 处方两轮门禁见 docs/acceptance/）。
4. 真实供应商、真实凭据、PostgreSQL live（沿项目既有边界；假模型闭环零外呼）。
5. Electron 对比壳（ADR 建议 Tauri 2 进入 PoC；未构建 Electron 侧镜像实现，体积/内存对比无从测起）。
6. V02-54B 遗留：rfd 原生对话框 Windows 实机交互（COM 线程/WebView2 交互）——策略矩阵已 Windows 原生跑绿。
7. V02-54C 遗留：日志轮转 Windows 原生测试全绿（含 junction 注入拒绝）；会话清扫「无并发壳」
   假设的实机多开竞态仍欠（单实例插件已缓解）。

## 8. 复现环境

- Linux 6.12 x64（Debian trixie 容器，uid 1000 无 sudo）、Python 3.13.5（.venv-desktop）、
  Node 20.19.2、rustup stable（1.98.1，含 x86_64-pc-windows-msvc std；系统 cargo 1.85
  无 Windows std 不可用于 c 步）、llvm-rc 19（用户态解包）。
- 沙箱限制导致的取舍都记录在上文矩阵，未用 mock 顶替真实环境检查。
