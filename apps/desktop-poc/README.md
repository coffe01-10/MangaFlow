# MangaFlow V02-53B 桌面壳可丢弃 PoC（Tauri 2 先验）

> 状态：**PoC 证据，不构成选型批准。** 契约 `docs/adr/v02-desktop-shell-evaluation.md`
> 仍是草案（V02-53A / Issue #56）；最终选型由 lead 在复核本 PoC 后决定。
> 任务：Issue #110 / roadmap V02-53B。整个 `apps/desktop-poc/` 目录是可丢弃的：
> 回滚 = 删除本目录，`apps/api`/`apps/web` 业务代码零改动。

## 1. 目录地图

```
apps/desktop-poc/
├── sidecar/
│   ├── mangaflow_poc_helper.py   # sidecar helper：冻结启动协议的 Python 侧实现
│   │                             #   （原子绑定 127.0.0.1:0 → journal → READY 行 →
│   │                             #    stdin GO 门控 → uvicorn/静态 stub）
│   │                             #   stub 模式（无三方依赖）供 Rust 测试；app 模式跑真实
│   │                             #   apps/api：alembic upgrade head + SQLite + 假模型通道
│   └── poc_fake_channel.py       # 假模型通道：复用仓库验收缝 app.worker_tasks._adapter，
│                                 #   种子目录/连接/密钥（AES-GCM 生产凭据路径），零外呼
├── shell-core/                   # Rust crate（无 GUI 依赖，沙箱可完整测试）
│   ├── src/protocol.rs           # token/journal/READY 行校验、回环 origin 校验
│   ├── src/ownership.rs          # 进程树所有权：Windows 根 Job Object
│   │                             #   (KILL_ON_JOB_CLOSE) / Unix PDEATHSIG + 进程组
│   ├── src/handshake.rs          # spawn → READY 校验 → GO → 健康探活 编排
│   ├── src/bin/shell-sim.rs      # 壳崩溃模拟器（握手完成后 SIGABRT 自杀）
│   └── tests/poc_protocol.rs     # 集成测试：握手/并发端口/拒绝/崩溃清树/强杀升级
├── src-tauri/                    # Tauri 2 最小壳（Windows 侧 cargo check 编译验证）
│   ├── src/main.rs               # setup：握手成功后才建 WebView；初始化脚本注入
│   │                             #   window.__MANGAFLOW_API_ORIGIN__ + invoke 命令
│   │                             #   poc_get_api_origin / poc_health_probe；退出时停树
│   ├── tauri.conf.json           # frontendDist=../dist/frontend；bundle msi+nsis（未构建）
│   └── capabilities/default.json # core:default（自定义命令无需额外权限）
├── patches/web-static-export.patch  # 可丢弃前端补丁（静态导出 + 运行时 origin）
├── scripts/
│   ├── run-sidecar-e2e.sh        # 真实 API + 假通道 生成→候选 闭环（pytest）
│   ├── test_poc_sidecar_e2e.py   #   同上，Python 侧协议实现 + 端到端断言
│   ├── build-frontend-static.sh  # 一次性 worktree 应用补丁 → next build 导出 → 拷入 dist/
│   ├── verify-static-origin.mjs  # D5 浏览器级验证（Chromium）
│   └── package-sidecar.sh        # PyInstaller 打包（Linux 形态）
└── dist/                         # 构建产物（gitignore 除占位 index.html）
```

## 2. 冻结启动协议（ADR §4.2/§4.4 的 PoC 实现）

1. 壳生成 32 位 hex owner token，创建 `runtime/mangaflow-poc-<token>/` 运行目录，
   **直接 spawn** helper（子进程），传入 `MANGAFLOW_POC_TOKEN` / `MANGAFLOW_POC_JOURNAL`。
2. helper 校验 token 与目录规范路径（对齐 `scripts/owned_processes.py` 的防link/防复用思路），
   **一次绑定** `127.0.0.1:0`（禁止先探测后绑定的 TOCTOU），跑 `alembic upgrade head`，
   原子写 readiness journal（仅身份字段：token/pid/port/origin/状态），stdout 打一行
   `MANGAFLOW_READY {json}`。
3. 壳校验 READY 行（token、PID 与子进程句柄一致、origin 必须回环）+ journal +（Linux）
   `/proc` starttime；**全部通过才**在 stdin 写 `MANGAFLOW_GO <token>`；任何不符 → 不建 WebView。
4. helper 收到正确 GO 才开始服务流量（此前 socket 只监听不 accept，探活必挂——
   本 PoC 测试同时断言了「GO 前零字节服务」）。错误 token → exit 75，不服务。
5. 所有权：Windows = 壳持根 Job Object（`KILL_ON_JOB_CLOSE`），spawn 后立即
   `AssignProcessToJobObject`——**这是 compile-only 骨架，不是生产形态**：helper 非挂起创建，
   存在 spawn→assign 竞争窗口，生产壳必须按 `scripts/owned_processes.py` 的
   `CREATE_SUSPENDED`+assign+`ResumeThread` 实现并在 Windows 实机重验（见 `ownership.rs`
   模块文档与 D3 行）。Linux 等价（本沙箱实测）= spawn 前 `PR_SET_PDEATHSIG`
   + helper `setsid` + 壳按进程组 TERM→KILL。**每个壳退出路径（正常/崩溃/超时）都必须
   清树**，见 `tests/poc_protocol.rs::shell_crash_still_kills_helper_and_descendants`。

## 3. 运行方式（Linux 沙箱实测；Windows 见 NOT RUN）

```bash
# a. 假模型闭环 + 协议 e2e（自动建 .venv-poc）
apps/desktop-poc/scripts/run-sidecar-e2e.sh

# b. Rust 协议/所有权测试（需 python3；用 rustup stable 或系统 cargo）
cd apps/desktop-poc/shell-core && cargo test

# c. Windows 目标编译校验（在本 Linux 机即可；需要 llvm-rc 于 PATH）
cd apps/desktop-poc/src-tauri && cargo check --target x86_64-pc-windows-msvc

# d. 静态导出（一次性 worktree，业务树零改动）
apps/desktop-poc/scripts/build-frontend-static.sh

# e. D5 浏览器级验证（需 d 的产物 + .venv-poc + chromium）
MANGAFLOW_POC_PYTHON=$PWD/.venv-poc/bin/python \
  node apps/desktop-poc/scripts/verify-static-origin.mjs

# f. sidecar PyInstaller 打包（Linux 形态；需 libpython3.13 在 LD_LIBRARY_PATH）
apps/desktop-poc/scripts/package-sidecar.sh
```

真实壳冒烟（本机不可行，无 webkit2gtk/显示服务）：在装有 Tauri 前提的机器上

```bash
MANGAFLOW_POC_PYTHON=.venv-poc/bin/python \
MANGAFLOW_POC_HELPER=apps/desktop-poc/sidecar/mangaflow_poc_helper.py \
MANGAFLOW_POC_API_ROOT=apps/api \
cargo run --manifest-path apps/desktop-poc/src-tauri/Cargo.toml
```

## 4. 验收矩阵 D1–D9（ADR §6）

| 编号 | 层 | 结论 | 证据 / 边界 |
| --- | --- | --- | --- |
| D1 | 打包 | **RUN（部分）/ NOT RUN（安装器）** | `tauri build` 未跑（沙箱无 webkit2gtk/显示服务，见下）；bundle 配置（msi/nsis/图标）就位并经 Windows 目标 `cargo check` 校验配置合法性。安装/卸载/重装数据保留 NOT RUN。 |
| D2 | Python sidecar | **RUN（Linux 形态，含 PyInstaller 冻结产物）/ Windows 打包 NOT RUN** | `run-sidecar-e2e.sh`：真实 `app.main:app` 经 `alembic upgrade head`（28 个迁移到 head）+ SQLite 读写 + 本地 worker（无 Redis 时 API 内 LOCAL_EXECUTOR，即安装版默认形态）+ 假通道完成「生成→候选→采用→PNG 落盘→`/content` 可取」，4.3s。PyInstaller onedir 冻结产物（116MB，含 alembic.ini+migrations 于 `_internal/`）实测通过完整握手→GO→健康→真实 dashboard API→优雅退出；alembic.ini 解析依赖冻结路径（`app.main.__file__`），故支持文件必须放 `_internal/`——这是打包形态的硬约束证据。**Windows PyInstaller/embeddable 形态 NOT RUN**；RQ/Redis Worker 进程形态 NOT RUN。 |
| D3 | 进程生命周期 | **RUN（Linux 等价）/ Windows 路径为 compile-only 骨架（实机 NOT RUN）** | `cargo test`（9 项）：握手全链、错误 GO 拒绝 exit 75、并发双 helper 端口不冲突、`shell-sim` 崩溃（SIGABRT）后 helper+孙进程全灭（PDEATHSIG 链）、无协作者 SIGTERM→SIGKILL 升级。Windows 路径**明确标注为 compile-only 骨架**（`ownership.rs` 模块文档）：`std::process::Command` 无法挂起创建，helper 先于 assign 执行首批指令，存在 spawn→assign 竞争窗口——生产壳必须实现 `CreateProcessW(CREATE_SUSPENDED)`+assign+`ResumeThread`（对齐 `owned_processes.py`）并在 Windows 实机重验，**不得把该路径当作生产启动器**。 |
| D4 | 端口/单实例 | **RUN（端口+注入）/ 单实例 NOT RUN** | 原子绑定 `127.0.0.1:0`（socket 先绑后报，无 TOCTOU；并发测试两 helper 端口必异）；WebView 建立前完成握手；运行时注入 = 初始化脚本同步写 `window.__MANGAFLOW_API_ORIGIN__` + invoke `poc_get_api_origin` 双通道，不依赖 `NEXT_PUBLIC_*`（浏览器断言 `api_origin_env_free`）/不依赖 Next rewrite（D5 实测直连）。单实例互斥体已接 `tauri-plugin-single-instance` 但**实机多开行为 NOT RUN**。 |
| D5 | 前端形态 | **RUN（机制验证）/ 静态导出为「受限可行」** | `verify-static-origin.mjs`（Chromium）：静态导出页加载 → 注入 origin → 仪表盘**直连**动态端口 API（`/api/v1/projects/dashboard` 200，CORS 按桌面 origin 放行）→ 页面渲染，静态服务器 `/api/*` 零命中。**核心发现**：工作台子树无法只靠 flag 导出——`output:"export"` 要求每个动态段 ≥1 预渲染组合（真实项目 id 构建期不可知）且工作台组件树服务端预渲染崩溃；补丁以「poc 桩组合 + notFound stub + 删 3 个仅服务端页」换得壳级页面导出。**结论：静态导出路线需要正式的前端路由/组件改造（否决条件 3 的关键输入）；方案 B（捆绑 node 跑 next start，保留 rewrites）未被本 PoC 验证**。 |
| D6 | 凭据/日志/数据 | **RUN（目录+日志+凭据路径）/ ACL NOT RUN** | 用户数据目录布局：`data/`（DB）、`storage/`、`uploads/` 均落 user-data（测试断言不落仓库）；helper stderr 落 runtime 日志文件；假通道密钥走生产 `credential_crypto` AES-GCM + 文件主密钥（`storage/.provider-credential-master-key` 自动生成）。Windows ACL 收紧 NOT RUN；日志轮转/导出 NOT RUN（对齐 V02-54）。 |
| D7 | 性能门禁 | **NOT RUN（按约定）** | V02-52A N=20 全样本不存在、本轮明确不跑 V02-52B；仅记录参考值：假闭环 e2e 全程 4.3s（含 28 个迁移），远优于 ADR 冷启动 ≤15s 建议线，但**非固定窗口测量、不作为门禁证据**。 |
| D8 | 自动更新（未签名） | **NOT RUN** | 未接 updater 插件、无签名密钥、无更新服务器（Issue 禁止真实签名/服务器）。 |
| D9 | 安全 | **RUN（PoC 面）/ 业务面沿用；CSP 有记录在案的债务** | journal 仅身份字段（测试断言字段集合，无命令/env/密钥）；token 由 OS CSPRNG 生成（Unix `/dev/urandom`、Windows `BCryptGenRandom`——后者编译验证、运行时 NOT RUN）；origin 回环强制（`verify_ready_line` 拒绝非 127.0.0.1）；GO 前零流量；注入串经 serde_json JSON 转义而非裸 format!；`tauri.conf.json` 设受限本地 CSP（`connect-src`/`img-src` 限 `self` + `http://127.0.0.1:*`，`object-src 'none'`、`frame-src 'none'`、`base-uri 'self'`）。**CSP 债务**：`script-src` 保留 `'unsafe-inline'`——Next 静态导出的内联引导脚本（`self.__next_f.push`）硬需求，去掉会白屏；WebView2 对该 CSP 的实际执行行为 NOT RUN（本机无 WebView）。上传像素/解压炸弹/总量等安全面未改动（沿用 P1-13 既有实现与测试）。 |

### Windows 专属汇总（NOT RUN / BLOCKED）

- Job Object 实机行为（Windows 路径为 **compile-only 骨架**：spawn→assign 竞争窗口未收口，
  生产须 CREATE_SUSPENDED+assign+resume）、WebView2 Evergreen 渲染兼容（Canvas 编辑器/动画）、
  WebView2 缺失安装行为、MSI/NSIS 安装器、SmartScreen/签名、自动更新链路、单实例多开：**NOT RUN**
  （本沙箱为 Linux、无 sudo，无法安装 webkit2gtk-4.1 开发件或运行 Windows）。缓解：src-tauri 与
  Job Object 代码已过 Windows 目标 `cargo check`（llvm-rc 用户态解包提供），shell-core 全部可测
  逻辑与 Windows 侧共库；骨架边界已在代码与上表显式标注，不冒充生产形态。
- 否决条件核查（ADR §3.1，**逐项供 lead 复核，非结论**）：
  1. Python sidecar 打包：Linux 形态机制可行；**Windows PyInstaller/embeddable 实测缺失** → 不能据此否决，也不能据此放行。
  2. WebView2 渲染兼容：完全未测 → OPEN。
  3. 前端静态导出：**发现确定性阻塞**（动态段预渲染组合 + 工作台预渲染崩溃），静态导出非 flag 级改动；方案 B 未在本 PoC 验证 → 倾向「方案 B 或混合形态」输入，不构成否决。
  4. Rust 维护能力：壳核心逻辑集中在 shell-core（~600 行可测 Rust）+ src-tauri 粘合（~120 行）；成本判断留给 lead。

## 5. NOT RUN 汇总（诚实边界）

1. Windows 实机全链路（Job Object 行为、WebView2、安装器、签名、更新、单实例多开）。
2. RQ/Redis worker 进程形态与 Independent Worker（按 Issue 约束不装 Redis/Docker/Postgres；本地 LOCAL_EXECUTOR 已验）。
3. V02-52A N=20 性能门禁、Lighthouse/FPS（归 V02-52B）。
4. 真实供应商、真实凭据、PostgreSQL live（沿项目既有边界；假模型闭环零外呼）。
5. Electron 对比壳（ADR 建议 Tauri 2 进入 PoC；未构建 Electron 侧镜像实现，体积/内存对比无从测起）。

## 6. 复现环境

- Linux 6.12 x64（Debian trixie 容器，uid 1000 无 sudo）、Python 3.13.5（.venv-poc）、
  Node 20.19.2、rustup stable（1.98.1，含 x86_64-pc-windows-msvc std）、llvm-rc 19（用户态解包）。
- 沙箱限制导致的取舍都记录在上文矩阵，未用 mock 顶替真实环境检查。
