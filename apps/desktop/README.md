# MangaFlow V02-54 桌面壳（`apps/desktop/`，自 V02-53B PoC 提升）

> 状态：**交付方向实现，技术选型仍未批准。** 契约 `docs/adr/v02-desktop-shell-evaluation.md`
> 仍是草案（V02-53A / Issue #56，V02-54 已补 PoC 输入）；最终选型由 lead 复核后决定。
> 来源：V02-53B 可丢弃 PoC（Issue #110 / PR #111 / `203efad`，原 `apps/desktop-poc/`）；
> V02-54（Issue #114）按交付目录策略 A 将其整体提升为 `apps/desktop/` 并继续交付推进。
> `apps/api`/`apps/web` 业务代码零改动；本目录为纯新增，回滚 = 删除本目录，
> 或按否决条件转 Electron 时整体丢弃后重做。

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
│   ├── src/handshake.rs             # spawn → READY 校验 → GO → 健康探活 编排
│   ├── src/bin/shell-sim.rs         # 壳崩溃模拟器（握手完成后 SIGABRT 自杀）
│   └── tests/startup_protocol.rs    # 集成测试：握手/并发端口/拒绝/崩溃清树/强杀升级
├── src-tauri/                       # Tauri 2 最小壳（Windows 侧 cargo check 编译验证）
│   ├── src/main.rs                  # setup：握手成功后才建 WebView；初始化脚本注入
│   │                                #   window.__MANGAFLOW_API_ORIGIN__ + invoke 命令
│   │                                #   desktop_get_api_origin / desktop_health_probe；退出时停树
│   ├── tauri.conf.json              # frontendDist=../dist/frontend；bundle msi+nsis（未构建）
│   └── capabilities/default.json    # core:default（自定义命令无需额外权限）
├── patches/web-static-export.patch  # 可丢弃前端补丁（静态导出 + 运行时 origin）
├── scripts/
│   ├── run-sidecar-e2e.sh           # 真实 API + 假通道 生成→候选 闭环（pytest）
│   ├── test_sidecar_e2e.py          #   同上，Python 侧协议实现 + 端到端断言
│   ├── build-frontend-static.sh     # 一次性 worktree 应用补丁 → next build 导出 → 拷入 dist/
│   ├── verify-static-origin.mjs     # D5 浏览器级验证（Chromium）
│   └── package-sidecar.sh           # PyInstaller 打包（Linux 形态）
└── dist/                            # 构建产物（gitignore 除占位 index.html）
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
   「resume 前落盘」更早，可证明运行目录归属。**Windows 运行时行为 NOT RUN**（本沙箱为
   Linux）：仅过 Windows 目标编译门禁，实机 D3 复验前不得称作生产已验证（见 `ownership.rs`
   模块文档）。Linux 等价（本沙箱实测）= spawn 前 `PR_SET_PDEATHSIG`
   + helper `setsid` + 壳按进程组 TERM→KILL。**每个壳退出路径（正常/崩溃/超时）都必须
   清树**，见 `tests/startup_protocol.rs::shell_crash_still_kills_helper_and_descendants`。

## 3. 运行方式（Linux 沙箱实测；Windows 见 NOT RUN）

需要 rustup stable（1.98.1，含 `x86_64-pc-windows-msvc` std）在 PATH；系统 cargo 1.85
无 Windows std，Windows 目标检查会报 E0463。

```bash
# a. 假模型闭环 + 协议 e2e（自动建 .venv-desktop）
apps/desktop/scripts/run-sidecar-e2e.sh

# b. Rust 协议/所有权测试（需 python3）
cd apps/desktop/shell-core && cargo test

# c. Windows 目标编译校验（在本 Linux 机即可；需要 llvm-rc 于 PATH）
cd apps/desktop/src-tauri && cargo check --target x86_64-pc-windows-msvc

# d. 静态导出（一次性 worktree，业务树零改动）
apps/desktop/scripts/build-frontend-static.sh

# e. D5 浏览器级验证（需 d 的产物 + .venv-desktop + chromium）
MANGAFLOW_DESKTOP_PYTHON=$PWD/.venv-desktop/bin/python \
  node apps/desktop/scripts/verify-static-origin.mjs

# f. sidecar PyInstaller 打包（Linux 形态；需 libpython3.13 在 LD_LIBRARY_PATH）
apps/desktop/scripts/package-sidecar.sh
```

真实壳冒烟（本机不可行，无 webkit2gtk/显示服务）：在装有 Tauri 前提的机器上

```bash
MANGAFLOW_DESKTOP_PYTHON=.venv-desktop/bin/python \
MANGAFLOW_DESKTOP_HELPER=apps/desktop/sidecar/mangaflow_desktop_helper.py \
MANGAFLOW_DESKTOP_API_ROOT=apps/api \
cargo run --manifest-path apps/desktop/src-tauri/Cargo.toml
```

## 4. 验收矩阵 D1–D9（ADR §6）

| 编号 | 层 | 结论 | 证据 / 边界 |
| --- | --- | --- | --- |
| D1 | 打包 | **RUN（部分）/ NOT RUN（安装器）** | `tauri build` 未跑（沙箱无 webkit2gtk/显示服务，见下）；bundle 配置（msi/nsis/图标）就位并经 Windows 目标 `cargo check` 校验配置合法性；卸载不删用户数据的配置契约由 `shell-core/tests/delivery_contract.rs` 冻结。安装/卸载/重装数据保留的实机验证 NOT RUN。 |
| D2 | Python sidecar | **RUN（Linux 形态，含 PyInstaller 冻结产物）/ Windows 打包 NOT RUN**（V02-53B 证据） | `run-sidecar-e2e.sh`：真实 `app.main:app` 经 `alembic upgrade head`（28 个迁移到 head）+ SQLite 读写 + 本地 worker（无 Redis 时 API 内 LOCAL_EXECUTOR，即安装版默认形态）+ 假通道完成「生成→候选→采用→PNG 落盘→`/content` 可取」。PyInstaller onedir 冻结产物（116MB，含 alembic.ini+migrations 于 `_internal/`）实测通过完整握手→GO→健康→真实 dashboard API→优雅退出；`alembic.ini` 解析依赖冻结路径（`app.main.__file__`），故支持文件必须放 `_internal/`——这是打包形态的硬约束证据。**Windows PyInstaller/embeddable 形态 NOT RUN**；RQ/Redis Worker 进程形态 NOT RUN。 |
| D3 | 进程生命周期 | **RUN（Linux 等价）/ Windows 路径已按 CREATE_SUSPENDED+assign+resume 实现、实机 NOT RUN** | `cargo test`（10 项）：握手全链、错误 GO 拒绝 exit 75、并发双 helper 端口不冲突、`shell-sim` 崩溃（SIGABRT）后 helper+孙进程全灭（PDEATHSIG 链）、无协作者 SIGTERM→SIGKILL 升级、壳在 spawn 前写入归属 journal。Windows 路径已按 `scripts/owned_processes.py` `start_python` 纪律重写：`CREATE_SUSPENDED` 挂起创建 → 建 Job（`KILL_ON_JOB_CLOSE`）→ assign 仍挂起的子进程 → 快照枚举初始线程后 `ResumeThread`；任一步失败即终止仍挂起的子进程，spawn→assign 竞争窗口已收口。**Windows 实机运行行为 NOT RUN**（本沙箱 Linux），合并前该路径只有编译门禁（`cargo check --target x86_64-pc-windows-msvc`）证据，实机 D3 复验仍欠。 |
| D4 | 端口/单实例 | **RUN（端口+注入）/ 单实例 NOT RUN**（V02-53B 证据） | 原子绑定 `127.0.0.1:0`（socket 先绑后报，无 TOCTOU；并发测试两 helper 端口必异）；WebView 建立前完成握手；运行时注入 = 初始化脚本同步写 `window.__MANGAFLOW_API_ORIGIN__` + invoke `desktop_get_api_origin` 双通道，不依赖 `NEXT_PUBLIC_*`（浏览器断言 `api_origin_env_free`）/不依赖 Next rewrite（D5 实测直连）。单实例互斥体已接 `tauri-plugin-single-instance` 但**实机多开行为 NOT RUN**。 |
| D5 | 前端形态 | **RUN（机制验证，V02-53B 证据）/ 静态导出为「受限可行」** | `verify-static-origin.mjs`（Chromium）：静态导出页加载 → 注入 origin → 仪表盘**直连**动态端口 API（`/api/v1/projects/dashboard` 200，CORS 按桌面 origin 放行）→ 页面渲染，静态服务器 `/api/*` 零命中。**核心发现**：工作台子树无法只靠 flag 导出——`output:"export"` 要求每个动态段 ≥1 预渲染组合（真实项目 id 构建期不可知）且工作台组件树服务端预渲染崩溃；补丁以「poc 桩组合 + notFound stub + 删 3 个仅服务端页」换得壳级页面导出。**结论：静态导出路线需要正式的前端路由/组件改造（否决条件 3 的关键输入）；方案 B（捆绑 node 跑 next start，保留 rewrites）未被验证**。 |
| D6 | 凭据/日志/数据 | **RUN（目录+日志+凭据路径）/ ACL NOT RUN** | 用户数据目录布局：`data/`（DB）、`storage/`、`uploads/` 均落 user-data（测试断言不落仓库）；helper stderr 落 runtime 日志文件；假通道密钥走生产 `credential_crypto` AES-GCM + 文件主密钥（`storage/.provider-credential-master-key` 自动生成）。Windows ACL 收紧 NOT RUN；日志轮转/导出 NOT RUN（对齐 V02-54）。 |
| D7 | 性能门禁 | **NOT RUN（按约定）** | V02-52A N=20 全样本不存在、本轮明确不跑 V02-52B；仅记录参考值：假闭环 e2e 全程约 4.3s（含 28 个迁移），远优于 ADR 冷启动 ≤15s 建议线，但**非固定窗口测量、不作为门禁证据**。 |
| D8 | 自动更新（未签名） | **NOT RUN** | 未接 updater 插件、无签名密钥、无更新服务器（Issue 禁止真实签名/服务器）。 |
| D9 | 安全 | **RUN（PoC 面）/ 业务面沿用；CSP 有记录在案的债务** | journal 仅身份字段（测试断言字段集合，无命令/env/密钥）；token 由 OS CSPRNG 生成（Unix `/dev/urandom`、Windows `BCryptGenRandom`——后者编译验证、运行时 NOT RUN）；origin 回环强制（`verify_ready_line` 拒绝非 127.0.0.1）；GO 前零流量；注入串经 serde_json JSON 转义而非裸 format!；`tauri.conf.json` 设受限本地 CSP（`connect-src`/`img-src` 限 `self` + `http://127.0.0.1:*`，`object-src 'none'`、`frame-src 'none'`、`base-uri 'self'`）。**CSP 债务**：`script-src` 保留 `'unsafe-inline'`——Next 静态导出的内联引导脚本（`self.__next_f.push`）硬需求，去掉会白屏；WebView2 对该 CSP 的实际执行行为 NOT RUN（本机无 WebView）。上传像素/解压炸弹/总量等安全面未改动（沿用 P1-13 既有实现与测试）。 |

说明：标注「V02-53B 证据」的行沿用 PoC 轮（PR #111 / `203efad`）实测记录，本目录提升
时未重跑这些环境受限项；能在本沙箱复跑的门禁见 §5。

### Windows 专属汇总（NOT RUN / BLOCKED）

- Job Object 实机行为（V02-54 已把 Windows 路径按 `owned_processes.py` 纪律实现为
  **CREATE_SUSPENDED → assign → resume**，spawn→assign 竞争窗口收口，但**实机运行 NOT
  RUN**）、WebView2 Evergreen 渲染兼容
  （Canvas 编辑器/动画）、WebView2 缺失安装行为、MSI/NSIS 安装器、SmartScreen/签名、
  自动更新链路、单实例多开：**NOT RUN**（本沙箱为 Linux、无 sudo，无法安装
  webkit2gtk-4.1 开发件或运行 Windows）。缓解：src-tauri 与 Job Object 代码已过 Windows
  目标 `cargo check`（llvm-rc 用户态解包提供），shell-core 全部可测逻辑与 Windows 侧共库；
  实机边界已在代码与上表显式标注，不以编译通过冒充实机验证。
- 否决条件核查（ADR §3.1，**逐项供 lead 复核，非结论**）：
  1. Python sidecar 打包：Linux 形态机制可行；**Windows PyInstaller/embeddable 实测缺失** → 不能据此否决，也不能据此放行。
  2. WebView2 渲染兼容：完全未测 → OPEN。
  3. 前端静态导出：**发现确定性阻塞**（动态段预渲染组合 + 工作台预渲染崩溃），静态导出非 flag 级改动；方案 B 未在本 PoC 验证 → 倾向「方案 B 或混合形态」输入，不构成否决。
  4. Rust 维护能力：壳核心逻辑集中在 shell-core（~600 行可测 Rust）+ src-tauri 粘合（~120 行）；成本判断留给 lead。

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

## 6. NOT RUN 汇总（诚实边界）

1. Windows 实机全链路（Job Object 行为、WebView2、安装器、签名、更新、单实例多开）。
2. RQ/Redis worker 进程形态与 Independent Worker（按 Issue 约束不装 Redis/Docker/Postgres；本地 LOCAL_EXECUTOR 已验）。
3. V02-52A N=20 性能门禁、Lighthouse/FPS（归 V02-52B）。
4. 真实供应商、真实凭据、PostgreSQL live（沿项目既有边界；假模型闭环零外呼）。
5. Electron 对比壳（ADR 建议 Tauri 2 进入 PoC；未构建 Electron 侧镜像实现，体积/内存对比无从测起）。

## 7. 复现环境

- Linux 6.12 x64（Debian trixie 容器，uid 1000 无 sudo）、Python 3.13.5（.venv-desktop）、
  Node 20.19.2、rustup stable（1.98.1，含 x86_64-pc-windows-msvc std；系统 cargo 1.85
  无 Windows std 不可用于 c 步）、llvm-rc 19（用户态解包）。
- 沙箱限制导致的取舍都记录在上文矩阵，未用 mock 顶替真实环境检查。
