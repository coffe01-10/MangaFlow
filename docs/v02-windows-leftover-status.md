# MangaFlow Windows 剩余项状态目录（V02-54D，状态 only）

更新时间：2026-09-04　基线：`master` / `efedb08`（含 V02-54C / PR #118）
对应：Issue #119（本文档即其交付物）；父项 V02-54 / Issue #114 保持未勾，V02-55 未开。

## 0. 定位与读法

- 本目录**只做状态汇总，不实现任何条目**。V02-54C（PR #118 / `efedb08`）合入后，Linux 可落地的桌面续作（V02-54 第一轮、V02-54B、V02-54C）已尽；下表是继续推进 V02-54 → V02-55 之前仍欠的全部 `NOT RUN` / `BLOCKED` 项。
- 条目来源：`apps/desktop/README.md` 验收矩阵 D1–D9 与 §7 NOT RUN 汇总、`docs/roadmap.md` V02-54/52/53 子项、`docs/development-progress.md` 2026-09-04 各节。发现新余项时追加新 `W-xx` ID，不并入既有条目。
- 状态语义：
  - `RUN`：已有对应环境的实测证据（本文只引用、不新增）；
  - `NOT RUN`：依赖环境可及（Windows 笔记本 `LAPTOP-TV9KT8RC` 存在、或仅需用户授权），但该验收轮尚未执行；
  - `BLOCKED`：依赖用户明确拒绝安装的基础设施（Docker/PostgreSQL/Redis）、尚不存在的外部资源（签名证书、更新服务器）或治理决策（ADR 终批），当前无法推进。
- **非验收澄清**：以下既有绿灯**不构成** Windows 实机验收，不得写成已验收——shell-core `cargo test` 36 项（Linux 实测）、双 crate `cargo check --target x86_64-pc-windows-msvc`（仅编译门禁）、sidecar 假闭环 e2e（假通道零外呼、SQLite）、SQLite/fakeredis/mock 回归；2026-09-02 的 PostgreSQL live 18 项与 Redis/RQ SimpleWorker 8 项是 Linux 服务端证据，不替代 Windows Job Object 独立 Worker 矩阵（W-17）；2026-09-02 的 owned E2E 17/17 与 LH/FPS 4/4 是 Web 层 2 轮证据，不满足 V02-52A N=20（W-18）。

## 1. 状态统计

| 状态 | 数量 | 条目 |
| --- | --- | --- |
| NOT RUN | 16 | W-01～W-12、W-18、W-19、W-20、W-22 |
| BLOCKED | 6 | W-13、W-14、W-15、W-16、W-17、W-21 |

## 2. A. Windows 实机运行面（桌面壳：进程 / 渲染 / 日志 / 安全）

| ID | 描述 | 依赖环境 | 状态 | 阻塞原因 | 已有证据（不视作实机验收） |
| --- | --- | --- | --- | --- | --- |
| W-01 | Windows 实机 Job Object 全链路：`CREATE_SUSPENDED` 挂起创建 → `KILL_ON_JOB_CLOSE` 根 Job → assign → `ResumeThread`；任一步失败终止挂起子进程（fail-closed）；崩溃/退出清树实测 | Windows 10/11 实机 | NOT RUN | 本沙箱为 Linux、无法运行 Windows；桌面壳 Windows 实机轮尚未执行 | 双 crate Windows 目标 `cargo check`；代码按 `scripts/owned_processes.py` 纪律实现；Linux PDEATHSIG 等价清树实测（`startup_protocol.rs`） |
| W-02 | WebView2 Evergreen 渲染兼容：工作台 DOM/SVG 画布、拖拽/缩放、动画与 reduced-motion、静态导出页在 WebView2 内的实际表现 | Windows 实机 + WebView2 Runtime | NOT RUN | 无 WebView2 运行环境 | D5 Chromium 机制级验证（静态导出页直连动态端口 API）；非 WebView2 内核 |
| W-03 | WebView2 缺失/损坏安装行为：Evergreen 未安装或损坏时壳的引导、提示与退出路径 | Windows 实机（可控卸载 Runtime） | NOT RUN | 同 W-02 | 壳侧握手与 WebView 创建顺序已有 Linux 等价测试 |
| W-04 | WebView2 内工具页 invoke：`shell-tools.html` 经 `withGlobalTauri` 调用 `desktop_export_logs` / `desktop_pick_file` / `desktop_pick_directory` / `desktop_read_picked_file` 的实机链路 | Windows 实机 | NOT RUN | 同 W-02 | 触发页已随静态导出拷入 `dist/frontend/`；invoke 命令面过 Windows 目标编译门禁 |
| W-05 | rfd 0.17 原生对话框实机行为：COM 线程模型、模态关系、与 WebView2 的焦点交互、保存/打开对话框 | Windows 实机 | NOT RUN | src-tauri 仅 Windows 目标编译门禁 | picker 策略/穿越拒绝/能力表矩阵在 shell-core `tests/picker_policy.rs` Linux 全绿 |
| W-06 | 日志轮转 Windows 实机行为：对 helper 进程仍打开文件的 rename 语义、世代 shift、ACL 收紧前的符号链接种植场景 | Windows 实机 | NOT RUN | rename-on-open 的 Windows 语义无法在 Linux 复现 | shell-core 36 项测试 Linux 全绿（含符号链接注入、清扫收紧、自愈重开）；见 README §6.4 |
| W-07 | 单实例多开行为：`tauri-plugin-single-instance` 第二实例聚焦/参数传递；会话启动清扫的「无并发壳」假设在多开下的实际竞态 | Windows 实机 | NOT RUN | 互斥体已接、实机未验；清扫无活跃性排除（README §6.4 并发假设） | 单实例插件已集成；清扫范围收紧有单元断言 |
| W-08 | owner token CSPRNG 运行时：Windows `BCryptGenRandom` 路径实际产出 32 位 hex token | Windows 实机 | NOT RUN | 仅编译验证 | Unix `/dev/urandom` 路径 Linux 实测；token 校验逻辑共库 |
| W-09 | CSP 在 WebView2 的实际执行：`script-src 'unsafe-inline'` 在案债务、`withGlobalTauri` 命令暴露面、`default-src 'self'` 外链限制的实际行为 | Windows 实机 + WebView2 DevTools | NOT RUN | 无 WebView2 | CSP 配置静态就位并在 README D9 记录为债务 |
| W-10 | 用户数据目录 ACL 收紧：`%LOCALAPPDATA%\com.mangaflow.desktop\` 每用户权限边界、日志/运行目录的权限实测 | Windows 实机 | NOT RUN | user-data ACL 收紧未做（README D6/§6.4 残余风险记录） | 目录布局（data/storage/uploads/logs/runtime）有测试断言不落仓库 |

## 3. B. 打包、安装与分发

| ID | 描述 | 依赖环境 | 状态 | 阻塞原因 | 已有证据（不视作实机验收） |
| --- | --- | --- | --- | --- | --- |
| W-11 | Windows sidecar 打包形态：PyInstaller onedir / embeddable 在 Windows 构建，`alembic.ini`+`migrations` 入 `_internal/` 硬约束复验 | Windows 实机构建环境 | NOT RUN | 沙箱无 Windows 打包链 | Linux 形态 116MB onedir 冻结产物完整握手→GO→健康→API 冒烟通过（V02-53B 证据） |
| W-12 | `tauri build` 产物与 MSI/NSIS 安装/升级/卸载实机：安装只换程序文件、Alembic 原地迁移、卸载不删 `data/`/`storage/`/`uploads/`/凭据、禁止 NSIS installer hooks 删用户数据 | Windows 实机 | NOT RUN | 沙箱无 webkit2gtk/显示服务，无法构建；Windows 构建轮未执行 | bundle（msi+nsis/图标）配置就位并过 Windows 目标编译校验；`shell-core/tests/delivery_contract.rs` 3 项冻结配置契约（README §5） |
| W-13 | 代码签名与 SmartScreen：证书类型（OV/EV）、时间戳、签名后 SmartScreen 信誉实测 | 代码签名证书 + Windows 实机 | BLOCKED | 无证书、无签发授权；购买/身份属用户决策；Issue 明确禁止真实签名 | 未签名构建与安装契约已冻结；无任何签名实现 |
| W-14 | 自动更新链路：updater 插件、签名密钥、更新服务器/分发渠道、升级不删用户数据实测 | 签名基础设施 + 更新服务器 | BLOCKED | 依赖 W-13；当前无插件、无密钥、无服务器（README D8） | 未接 updater；D8 整项 NOT RUN |
| W-15 | 前端壳内形态收口：静态导出正式改造（动态段预渲染组合 + 工作台树预渲染）或方案 B（捆绑 node 跑 `next start` 保留 rewrites） | ADR 终批确定路线后的实现轮 + 实机验证 | BLOCKED | 先决 W-21：D5 路线未定 | 否决条件 3 已拿到确定性阻塞输入：flag 级静态导出不可行；poc 补丁仅覆盖壳级页面（D5） |

## 4. C. 运行基础设施与 Worker 形态

| ID | 描述 | 依赖环境 | 状态 | 阻塞原因 | 已有证据（不视作实机验收） |
| --- | --- | --- | --- | --- | --- |
| W-16 | Redis/RQ 桌面 worker 形态：安装版默认 LOCAL_EXECUTOR 已验，Redis 独立 worker 在桌面形态下的进程/生命周期未验 | Windows 实机 + Redis | BLOCKED | 用户明确不在 Windows 笔记本安装 Docker/Redis/PostgreSQL | 安装版默认形态（无 Redis → API 内 LOCAL_EXECUTOR）在假闭环 e2e 实测 |
| W-17 | Windows Job Object 独立 Worker 进程矩阵：`test_live_independent_worker_*` 7 项（重试新起进程、双 Worker 槽位、强退租约、五类检查入队） | Windows + PostgreSQL + Redis | BLOCKED | 同 W-16（2026-09-02 起 7 项在非 win32 明确 skip，不是假 skip） | Linux live：PostgreSQL 18 项、Redis/RQ SimpleWorker 8 项通过（2026-09-02，非等价替代） |

## 5. D. 验收、性能与真实供应商

| ID | 描述 | 依赖环境 | 状态 | 阻塞原因 | 已有证据（不视作实机验收） |
| --- | --- | --- | --- | --- | --- |
| W-18 | V02-52B 性能门禁执行：按 `docs/v02-desktop-performance-acceptance-plan.md` 固定环境清单、N=20 全样本、nearest-rank P95、10 秒持续窗口、保留失败轮次 | 固定环境 + 独占性能窗口 | NOT RUN | 缺 N=20 全样本；本轮明确不跑 V02-52B | 2026-09-02 owned LH/FPS 4/4 为脚本 2 轮（`98b93a0`），非 N=20，不作门禁证据 |
| W-19 | 真实供应商与真实 CLI 生图/编辑：`VERIFIED` 能力来源实测、账号权限、费用入账、真实取消/超时进程树（含 CLI Job Object runner 实机） | 用户授权 + 真实凭据 | NOT RUN | 付费调用需用户逐次明确授权；本轮禁止 | 假通道闭环零外呼；三 CLI 通道仅只读探测实机过（版本/登录态） |
| W-20 | Electron 对比壳：ADR §3.1 参考；体积/内存对比 | Electron 侧实现构建 | NOT RUN | 未构建 Electron 实现，对比无从测起；是否补测由 ADR 终批决定 | ADR 否决条件核查表已在 README §4 逐项记录待 lead 复核 |

## 6. E. 治理与发布门禁

| ID | 描述 | 依赖环境 | 状态 | 阻塞原因 | 已有证据（不视作实机验收） |
| --- | --- | --- | --- | --- | --- |
| W-21 | ADR 选型 lead 终批：`docs/adr/v02-desktop-shell-evaluation.md` 仍为 DRAFT；§3.1 否决条件核查表 + D5 输入 + W-01～W-20 状态供复核 | lead 复核决策 | BLOCKED | 治理门禁：选型不由实现/文档轮自行放行 | ADR 状态说明已按 V02-53B/V02-54 输入更新（仍草案） |
| W-22 | V02-55 发布门禁：全新安装/升级/卸载/恢复测试、浏览器 E2E、固定性能门禁、授权范围内真实供应商验收、全部 worktree 清点、三 manifest 统一 `0.2.0` + 变更记录 | 上述各项收口 | NOT RUN | 等前置项收口；版本号与变更记录不提前改动 | roadmap V02-55 保持未勾；`0.2.0` 版本结论只声明目标 |

## 7. 维护规则

- 状态变化只能来自验证证据：每轮 Windows/发布验收后由 lead 核对证据 SHA 更新对应行，并在 `docs/development-progress.md` 记录该轮证据；不因分支测试全绿或 PR 打开而改状态。
- 条目完成即整行移入该轮验收记录并标注证据链接；新发现的余项追加新 `W-xx` ID，不改写历史条目。
- `BLOCKED` 项解除条件：W-13/W-14 = 用户提供证书与服务器并授权；W-15 = W-21 定路线后开实现轮；W-16/W-17 = 用户同意安装 Docker/PostgreSQL/Redis（或提供等价远程环境）；W-21 = lead 复核 ADR。
