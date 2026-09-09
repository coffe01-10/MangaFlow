# N3 Desktop Red Team · 威胁模型（2026-09-08/09 夜）

基线：origin/master `929a216`（审计主体完成于 `961bcec`，收尾时 master 已并入本夜全部产出，见 §6）（含 2026-09-07 夜的 #271/#273/#274/#277 修复与 #259/#270，2026-09-08/09 夜并行轮的 #278/#279/#287/#289/#291、fake-channel 门控 0dfaac3、native 加固 6aee185 等）。
审计面：`apps/desktop/shell-core` + `sidecar` + `scripts` + `src-tauri`（IPC/CSP/withGlobalTauri）+ `native/**`（只读）。方法：多轮并行子代理对抗审计（file:line 级）+ 人工复核 + 交叉复核轮（§6）。

## 1. 资产

| 资产 | 位置 | 说明 |
| --- | --- | --- |
| 用户创作数据 | `<user_data>/data/mangaflow.db`、`storage/`、`uploads/` | 单用户工作台全部业务数据 |
| 供应商凭据 | DB 内 AES-GCM 密文 + `<user_data>/storage/.provider-credential-master-key`（0600，helper :440 设 STORAGE_ROOT 后落位） | 主密钥文件在用户数据目录 |
| 所有权令牌 | 128-bit CSPRNG（protocol.rs `new_token`） | 控握手与 journal 归属；仅存在于 shell 内存与 helper env |
| 日志树 | `<user_data>/logs/`（shell/helper/世代） | 身份字段 only（契约测试钉死）；导出面是唯一读取边界 |
| WebView 会话 | plan-B `http://127.0.0.1:<announced_port>`（helper 自持 socket）或静态导出占位 | 注入 `__MANGAFLOW_API_ORIGIN__`；`withGlobalTauri: true` 会把 `__TAURI__` 注入所有页面，**但仅静态导出（本地）页面持有 IPC**——plan-B 远程页面被 ACL 拒绝（M-1） |
| 固定中继 | 127.0.0.1:39443 → 动态 API 端口（Windows EXCLUSIVEADDRUSE） | 纯字节管道，回环 only |

## 2. 攻击者

- **A1 恶意网页（drive-by）**：可对回环端口发请求。TrustedHost 白名单 + 固定 CORS 白名单（apps/api/app/main.py:130-155）+ Chrome PNA 挡住读取。
- **A2 本机低权用户（跨会话）**：回环端口机器全局。信任模型接受“本机=可信”（ADR D9）；本模型的贡献是把**窗口竞态**（F-1 系列）关到“无法把外来内容放进 WebView”。
- **A3 同用户本地进程**：已有用户全部权利；本模型在“特权边界内退化”上设防：日志导出边界、picker 能力表、journal/sweep 防链、TOCTOU 窗口、端口抢占。
- **A4 被攻陷页面（bundled Next 内 XSS）**：plan-B 下无任何 shell IPC（capability 无 `remote` 上下文；tauri 2.11.5 源码级核实 + `delivery_contract.rs::no_capability_may_grant_a_remote_ipc_context` 钉死）→ 权力等于普通网页对回环 API 的访问。静态导出形态的本地页持全部 6 个 invoke 命令 + CSP `script-src 'unsafe-inline'`（G-3 记录）。
- **A5 误行为（非对抗）**：第三方库向 stdout 倾倒、node 启动崩溃/挂起、构建污染——F-1/F-2/F-4/F-6 系列产出均由此驱动。

## 3. 入口点（全量枚举）

1. Tauri invoke 命令 6 个（desktop_get_api_origin / health_probe / export_logs / pick_file / pick_directory / read_picked_file）。
2. 启动握手：READY 行（stdout，64KiB/行上限）→ token/PID/journal/回环校验 → GO → 健康门（响应 64KiB 上限）。
3. journal：`runtime/mangaflow-desktop-<token>/owner.json`（helper 写 ready/failed；shell 写 created/stopped）+ 会话起始 sweep。
4. web_origin：helper **自持 socket** 公告 → 壳 External 导航（本轮 F-1 架构修复，见 M-8）。
5. 中继：39443→API（Windows EXCLUSIVEADDRUSE）；公告 web 端口→node 临时端口（本轮新增，同策略）。
6. 日志导出 ZIP（对话框目的地址、成员双向遏制、`hard_link` 无覆写落位、成员名/条目数/总量的 panic-not-corrupt 不变量）。
7. picker 能力表（注册→读回全量再校验）。
8. native-host ↔ WPF（MANGAFLOW_NATIVE_READY 行、250ms 子进程巡视、stdin-EOF 生命周期）。

## 4. 已缓解（本轮核实为 SOLID 的面）

- **M-1 plan-B 页面 IPC 全拒绝**：capability 仅 `windows:["main"]`+`core:default` 无 `remote` 上下文；tauri `ipc/authority.rs` 只让 Remote 匹配 `ExecutionContext::Remote`。注意 `withGlobalTauri` 会把 `__TAURI__` 注入远程页面，但每个 invoke 都被 ACL 拒绝。契约测试 `no_capability_may_grant_a_remote_ipc_context` 防回退。
- **M-2 初始化脚本注入**：origin 先被 `is_loopback_origin` 收敛（protocol.rs:39-45）再 `serde_json` 转义（main.rs）——双保险。
- **M-3 导出双向边界**：符号链接跳过 + 逐成员 canonical 包含 + 名称卫生（`\`、`..`、u16 名长/条目数/总量 panic 不变量，ziparch.rs 本轮补齐名长断言）+ 目的地出 user-data 根拒绝 + `.pending` 拒链 + `hard_link` 无覆写落位。
- **M-4 轮转灾难安全**：staging-first、部分失败 unwind、熔断器。
- **M-5 picker 能力模型**：全祖先无跟随链接检查 + 读回全量再校验 + canonical 键一致 + 20MiB 上限。
- **M-6 runtime sweep（#264，已修并验证）**：仅终态 + 24h 宽限 + 名称门 + 拒链 + canonical 包含 + fail-soft。
- **M-7 端口绑定策略**：API socket 与 39443 中继在 Windows 均 EXCLUSIVEADDRUSE（#256）；POSIX REUSEADDR 不被活动 LISTEN 驱逐（test_sidecar_relay_bind.py）；中继连接数上限 128 + 永续 accept 循环（本轮 7d8d385/522a6cf）。
- **M-8 公告 web 端口归 helper 自持（2026-09-09 架构修复，PR #301）**：helper 在 spawn node **之前**绑定公告端口（Windows EXCLUSIVEADDRUSE / POSIX REUSEADDR，settimeout 与 39443 中继同策略），node 绑独立临时端口，公告端口经 helper 字节中继转发。**公告端口不可被共绑**（Windows 互斥）；node 临时端口竞态仅导致 node 死亡 → 启动验证（`_await_web_server_boot`：poll + connect + 复 poll）→ fail-closed 降级（降级时释放公告与中继端口并有日志可断言）。node 中途死亡由 web-exit-watch 记录（0657f49）。**措辞校准（R2）**：这是"缓解并转移"而非"结构性消除"——node 自身临时端口仍是无选项 bind，同用户进程可 netstat 发现并共绑之造成中继流量混串（未发布、需已握有的同用户权利）；彻底关闭需 node 侧套接字协作（libuv 不提供），列为已接受残余。
- **M-9 fake-channel 已门控（0dfaac3）**：桌面假通道不再无条件注入；native READY/banner 行为已钉进 native-tests（#276 关闭）。

## 5. 缺口与残差

### F-1 [P2→修复中] web_origin 劫持窗口（历史）
2026-09-08 发现：helper 在 node 未验证监听前发布 web_origin（#272）。#271 以 poll+connect+复poll 修了 POSIX 形状；R2 交叉复核发现 **Windows 共绑残差**（libuv 无选项 → 劫持者 SO_REUSEADDR 共绑、node 存活 → 校验放行）。本轮以 M-8 架构修复（helper 自持公告端口）收口，PR 待审。

### F-2 [P3→已修] stop() 对已 reap pid 发信号（#273 合并）
`OwnedTree::stop` 现先 `try_wait` 早退。残差（记录）：reaped 树的 Unix 后代不再被组信号扫尾，链式死亡完全依赖 helper 直接子代的 PDEATHSIG（现状成立——helper 的子进程均带 PDEATHSIG；未来若有不带 PDEATHSIG 的子代派生路径需重新评估）。

### F-3 [P3→已修] fake-channel 无条件注入（#275 关闭，0dfaac3 门控）
### F-4 [P3→已修] package-sidecar.sh 可预测 /tmp 工作路径（#274 合并，mktemp 0700）

### 残差记录（接受项 / 非目标平台，全部要求同用户权利或失败方向为关闭）
- **R-a 10s 启动预算 vs 15s READY 预算**：node 挂起（活着但不 bind）时，Tauri 腿（15s READY）的“降级”可能升级为 ReadyTimeout 全树拆除。方向仍是 fail-closed；native-host 腿 20s 预算可完成降级。
- **R-b boot-scoped 校验**：READY 后 node 死亡不再复验（已由 web-exit-watch 记录 + 断连横幅恢复），公告端口仍归 helper（中继只报“上游已死”），无劫持面。
- **R-c picker validate→open TOCTOU、journal `.pending` TOCTOU、导出 stat 跟随换入链接、collect_members 递归深度、`pid_starttime` fail-open**：全部要求同用户写权限（A3 已有等价权利），保持为文档化残余。
- **R-d 静态资源逃逸**：D5 静态服务器已有 resolve 包含检查（#261）；导出 ZIP 成员名/尺寸/总字节/条目数在写侧全部有 panic 不变量或跳过规则（本轮 ziparch 名长断言补齐）。plan-B 页面无 CSP（tauri CSP 仅注入本地协议页）——无 IPC + 回环限制下记为已接受债（改进方向：next.config headers 的 CSP 已覆盖 plan-B 同源路径）。
- **R-e native 测试缺口**（#276）已由 0dfaac3 关闭（NativeBackendChecks.cs 112 行）。
- **R-f #273 修复的测试小疵**（R2 复核发现，R3 复核确认已修）：reap 等待循环的恒真式断言（`Instant::now() < Instant::now() + 5s`）已由 35859d7 修复为真实 deadline（startup_protocol.rs:876,882），master 上 grep 无残留。

## 6. 轮次记录

- **2026-09-08 夜 R1**：3 路并行子代理审计（shell-core / sidecar+scripts / tauri+native）→ 修复 #271/#273/#274/#277 合并，Issue #272/#275/#276；威胁模型初稿。
- **2026-09-08 夜 R2**：交叉复核子代理审出 Windows 共绑残差（MEDIUM）、中继降级残留、10s/15s 预算互play、e2e 断言不对称、两项误报判定复核为正确（Sidecar-F9 dist/、Sidecar-F7 journal 兜底）、威胁模型九项修正。
- **2026-09-09 夜 R1**：基线 961bcec；确认并行轮已关闭 #275/#276/#272（POSIX 形状）并落 #278/#279/中继上限/web-exit 检测/fake-channel 门控/native 钉死；本轮实施 M-8 架构修复（helper 自持公告端口）+ e2e 全量回归；威胁模型按 R2 修正重写（本文件）。
- **2026-09-09 夜 R2**：子代理交叉复审 #301 重构——结论"未引入缺陷"（资源生命周期/中继语义/fd 继承/journal 一致性均验证），产出 N1（`_bind_web_port` 缺 settimeout → 降级后公告端口滞留，已修+新增释放断言）、N3（"结构性消除"措辞过强 → 已校准为"缓解并转移"）、N2/N4/N5（低危/既有，记录在案）；两项昨日误报判定复核为正确。
- **2026-09-09 夜 R3**：终审子代理覆盖 fake-channel 门控（0dfaac3，判定 airtight：精确值 env 门 + 单一调用点 + native 腿结构上无法启用）、startup_protocol 平台门控增量（Unix 覆盖增强、恒真断言已消）、威胁模型 8 项事实抽检（2 处文档瑕疵当场修正）、scripts 全量扫尾（无新 red-team 级发现）。**GO**。
- **2026-09-09 夜 R2'/R3'（续跑）**：R2 复核的跟进全部落地——N1（`_bind_web_port` 补 settimeout(0.5)：修复降级后公告端口滞留）与 N4（web-exit 看护确认窗口：刻意停止不再记伪崩溃行，双语义直验）+ N5（node 端口 claim 失败降级而非判死会话）→ **PR #303**；N3 措辞校准（公告端口不可共绑 ✓，node 临时端口的同用户共绑为"缓解并转移"残差）入 M-8 与 docstring；main.rs fake-channel 门控注释漂移修正 → **PR #304**。并行代理新开 **#299**（plan-B 壳工具不可达——IPC 远程拒绝 + 无 UI 入口，机制分析与本模型 M-1 一致，设计决策留 lead）与 **#300**（CSP unsafe-inline 债，对应 R-d）——由认领者跟进，此处仅交叉引用。

## 7. 配额台账（2026-09-09 长线 Goal）

**B — Issue 账（本 Goal 内 11 个，均含 file:line 复现/严重度/修复面/测试现状）：**
- #307 [P3] node_port 同用户共绑残差（Windows libuv 无选项；流量混串；缓解与修复方向三选）
- #308 [P4] picker 读回 validate→open TOCTOU（无后置句柄身份校验）
- #309 [P4] pid_starttime 锚点 advisory（省略即跳过 Unix 身份检查）→ **已修 PR #318**
- #310 [P4] 导出尺寸统计跟随换入符号链接 + collect_members 递归无上限 → **已修 PR #320**
- #311 [P4] runtime sweep：Windows junction 过 guard-1 + SIGKILL 残渣永不回收（需 lead 政策）
- #312 [P4] node 子进程继承握手令牌/journal/全量父 env → **已修 PR #321**
- #313 [P4] MANGAFLOW_DISABLE_DOTENV setdefault 可被继承值复活
- #314 [P4] --api-root 无校验 + sys.path.insert(0) 遮蔽
- #315 [P4] start-native.ps1 把 DEBUG native-host 拷进 Release 输出
- #316 [P4] UI 线程同步命令面：20MiB base64 读回 + rfd 模态堆叠
- #317 [P4] D5 工具：固定端口 4173、负 pid 后座依赖 setsid、根内符号链接被读取

**C — 防守钉 PR 账（本 Goal 内 3 个 + 延续 2 个）：**
- #318 require pid_starttime anchor（protocol.rs 收紧 + tamper 矩阵扩展 + stand-in 更新，85→85+ 全绿）
- #320 export 深度上限 + no-follow 尺寸（collect_members + 新测试，86/86）
- #321 node 子进程 env 卫生（_node_child_env + 单测，e2e 17/17）
- （前窗）#303 降级健壮性双修（N4/N5 + 释放断言）、#304 注释校准

**E — 互审轮次（本 Goal ≥4 轮）：**
- R2'（#301 重构复核）：无引入缺陷；N1/N3 跟进已落地（#303 + 措辞校准）。
- **R3''（三个防守钉 PR 交叉复核，2026-09-09）**：三 PR 均判 MERGE——#318 资源/fixture/cfg 纪律全部核实（3 LOW 已修：过期 fail-open 注释、cfg(unix)→cfg(target_os=linux)、pid-4242 锚点缺失夹具的潜伏 flake）；#320 深度线程与 no-follow 语义核实（1 nit：注释措辞）；#321 env 移除与 import 无副作用核实（跟进：活体 /proc 断言可选）。无假阳性声明。
- **R4''（Issue 质量审计，2026-09-09）**：11 个 Issue 逐条对照代码——全部 CORRECT、无重复、严重度无超一级偏差；#309/#310/#312 已随 #318/#320/#321 合并关闭（Issue 已关+FIXED 评论）；#317 第一项被 #298 提前修复（已评论重划范围为 2-3 项）；#311 补 std 版本对 junction 语义的注意事项；行号漂移多处均不影响结论。307 的 P3 与其余 P4 的梯度张力已标注供 lead 校准。
- R5''：终审进行中。

**D — 旧账状态：** #264 FIXED（sweep）、#265 FIXED（ADR 修订 + 检测/横幅）、#272 FIXED（#271/#301）、#275 FIXED（0dfaac3 门控）、#276 FIXED（NativeBackendChecks）——均有带证据的关闭/评论；#299/#300 开放中由认领者跟进。非 Desktop 的 N1 期 Red Team Issue（#121-#152 等）不在 N3 审计面。
