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
- **R5''（终审，2026-09-09）**：终审子代理对 #330（E3 polish）与 §7 台账逐项核验——分支 delta 与声明完全一致（守卫形状正确）、台账 11 Issue/5 PR 与 GitHub 真实状态一致、master 合并祖先核验（#301/#303/#318/#320/#321 全部在位）、helper 编译通过。**GO**。两个 LOW 已当场处置：#309 自动关闭无评论 → 补 FIXED 评论；#330 kill 后补 wait() reap。

**R6（跨目标续跑，master 4a0ebe6）**：交叉复核并行轮最新 6 个加固提交（时钟钳制/注入缝、token 契约钉、sweep 符号链接 journal 拒绝、非 UTF8 分类、future-mtime keep、review 强化）——**无引入缺陷**（生产侧仅 4969c93 panic→clamp 与 6a7d636 纯重构）；发现并落地：非 UTF8 分类测试被无必要 unix 门控 → 拆分出全平台测试（**PR #335**）+ 去重字节级断言；确认 #308 的 validate→open 窗口仍开放（删除场景已被 4969c93 系拒绝，swap 场景维持残差 R-c）；4969c93 的提交信息勘误已在 output/n1-shell-audit.md:173 记录。shell-core 套件 **106/106**。

**D — 旧账状态：** #264 FIXED（sweep）、#265 FIXED（ADR 修订 + 检测/横幅）、#272 FIXED（#271/#301）、#275 FIXED（0dfaac3 门控）、#276 FIXED（NativeBackendChecks）——均有带证据的关闭/评论；#299/#300 开放中由认领者跟进。非 Desktop 的 N1 期 Red Team Issue（#121-#152 等）不在 N3 审计面。

### Lead 决策补录（2026-09-11，master 4aa1797 起生效）

本文件自 night/n3-redteam-burn-20260909 移植到 master：#307/#311 等开放残差此前只在此分支可解析，正式报告应随代码可达。两项待决残差的组长决策如下：

**#307 node_port 共绑残差 —— 接受为同用户残差（选项三）。** 依据：攻击前提是同用户进程（netstat 级发现 + SO_REUSEADDR 共绑）；公告端口自 #301 起已 helper 自持且 Windows 独占绑定，共绑面只剩 node 内部临时端口；回环信任模型本就把同用户划在边界内。身份质询方案只能发现非代理型共绑（概率性），helper 前置 HTTP 终结是真实架构改动，均不值 P3 同用户残差的代价。若未来威胁模型把同用户纳入边界，重开此项。

**#311 runtime sweep —— 第一部分已实证、第二部分不自动回收。** 第一部分（junction 过 guard-1）：Windows 腿 2026-09-11 首次实跑 `sweep_never_removes_through_planted_links` 的 in-root junction 钉住（protocol.rs Windows cfg 块）——**通过**：当前工具链的 `DirEntry::file_type()` 把 junction（name-surrogate 重解析点）报为 `is_dir()==false`，guard-1 即拦截，无需生产改动。第二部分（SIGKILL created/ready 残渣永不回收）：**决策不实现自动回收**——WPF 腿共享 runtime 布局且无单实例互斥，错误回收会删掉活会话的 ownership journal，其风险远大于每次硬崩溃留下的小 JSON 残渣；现有"清扫跳过非终态"是蓄意且被测试钉住的行为。重开条件：sweep 引入跨平台 pid+starttime 活性预言机并经 lead 安全论证。

---

## 8. 2026-09-12 加重窗口台账（重开，窗口至 09:00）

- 基线：origin/master `44e3945`，分支 `night/n3-redteam-burn-20260912`（与 master 同点起步）。`native/**`、`native-tests/**` 只读。
- 上一窗关闭时的"零发现"是对 4aa1797 前代码面的结论；本窗增量面 = `4aa1797..44e3945` 的桌面 churn（junction 重建/装配换窗/静态导出克隆/dist 锁/zip writer 守卫/ownership 错误展示/get-status 语义/审批别名目录钉/workflow view 暂停取消/ScriptView 双激活/native intake page 重建）——全部是**修复本身带来的新代码**，回归挖掘是本窗第一优先。
- 配额跟踪：B ≥20 实质 issue（杜绝水文，先挖后报）；C ≥6 非 native 防守测试/契约小 PR；D 既有 Desktop/RedTeam issue 清账（FIXED/PARTIAL/NOT_FIXED 有据）；E ≥6 轮互审；F 本节持续更新。

- **R10（隔夜修复复核 + 全量认证，06:28）**：交叉复核隔夜两修——75ce571（plan-B skip 门探针对齐 _find_node 首候选，回应本窗 R2 复核 F10 NIT）与 ecaf242（Stop-SampleTree 的 pid 重用身份守卫，回应 #348）——**均正确**（前者 test-only 且探针现在与 helper 的首候选精确一致；后者 ProcessName 核验先于 taskkill）。认证 HEAD f10b844：shell-core **146/146**、sidecar 全家 **45/45**（含隔夜新增的 identity-guard 与 skip-gate 修正）。**窗内账目终值：Issues 33+（我 17 + 并行 16+，全部 file:line 级）；防守 PR 8 个（#318/#320/#321/#330/#335/#352/#353/#354 合并 8 + #357/#465/#466 开放 3）；互审 10 轮（R1-R10）。**

- **R8（main.rs 全文对抗通读 + 时序观察，2026-09-12 夜）**：src-tauri main.rs（570 行）端到端复核——setup 全部失败路径经 stop_helper 收尾（菜单布线失败也走同一簿记）、shell-tools 窗口生命周期闭合（菜单重建 #351 / 随主窗销毁 / 不接受初始化脚本 / 本地上下文）、对话框守卫 RAII 全路径无泄漏、async 化读回命令、fake_channel 精确 env 门（含单测）——**无新发现**。时序观察（LOW，未立 Issue）：并发全套件下 \`a_silent_helper_fails_with_ready_timeout_and_is_torn_down\` 出现过一次失败（重跑三次均过），属负载时序抖动；pgrep 探针的 -f 匹配与僵尸窗口是候选根因，未复现不立案。
- **收敛判定（R6-R8 三轮连续无新价值 Finding）**：core 面（main.rs / protocol.rs / 中继 / sweep / journal）在本窗已由并行轮与本 agent 交替扫净；剩余开放项均在各自 owner（WPF native 面待 owner、#300 CSP 债待 nonce 重构、#307 已裁决接受、#311 政策已记录）。**本窗按规则进入收敛。**

- **R7（续跑认证 + 新增面复核，2026-09-12 夜）**：交叉复核最新合并增量——#472（phase2 runner default python 钉，test-only）、#463（中继 accept 韧性：errno 瞬态/终态分类、128 连接上限、饱和日志冷却——逐行复核无新缺陷）、#464（本窗 #314 收口，已并入）；当前 \`verify_journal\`/sweep（有界读、FIFO 拒绝、常量时间 token 比较、逐字段校验、linux starttime 锚点）对抗通读——无新发现。认证 HEAD：shell-core **145/145**、sidecar e2e 全家 **44/44**（含 dist 锁、溯源、env/api-root、非 UTF8 跨平台、mid-session 检测等全部新增钉）。**本窗 B 配额由并行 agent 达成（#409-#413/#426-#431/#457-#462 等已入账），C/D 由 #420-#423/#463/#464 及本轮认证覆盖。**

- **R8（main.rs 全文通读）+ 时序观察**：无新发现；并发负载下 \`a_silent_helper_fails_with_ready_timeout\` 一次性抖动（3 次重跑绿，pgrep 探针候选根因，未立案）。
- **R9（终覆盖轮，2026-09-12 深夜）**：最后 9 个未深读文件（WorkspaceState/Models/Labels/Ui/SceneEditor/JobDetailsWindow/StyleWorkspace/StyleProductionCard/CharacterPackagePane，2,838 行全读）——6 发现已立 Issue：**#484 [P3]** \`Flag("deleted_at")\` 永不匹配时间戳字符串形态（已亲验 Models.cs:24-26 vs web scene-picker.tsx:73）→ 已归档场景资产仍可绑定；**#485 [P3]** CharacterPackagePane 四个变更按钮无 busy 守卫（双提交/publish-save 竞态）；**#486 [P4] 束** dead-batch 闩锁/仪表盘卡片过期等值/灯箱吞错/diff 重入。SOLID 面与覆盖声明见 issue 正文。native 面至此全量覆盖（全部 Views + Services + 共享设施）。

- **R9（同步轮，master tip 3736112）**：窗口分支合并 master（吸收并行轮 #466/#467 traverse-only fallback 全六影子名钉与 % 转义、31e3386 node env 编排名剥离扩展）；交叉复核 31e3386——六个 MANGAFLOW_DESKTOP_* 编排名从 node 子进程 env 剥离正确（helper 自身启动读取不受影响，strip 只作用于 node env dict），无引入缺陷。HEAD 认证：shell-core **145/145**、sidecar 全家 **44/44** 持续绿。

- **R1（本窗首轮，2026-09-12）**：三路并行审计（native 新增面深审：storyboard 编辑套件/检查面板/workflow inspector/审批队列；shell-sidecar-src-tauri 增量：picker 身份校验 1992152、对话框重入守卫、plan-B shell-tools 窗口 #299 解决、helper env/api-root 校验 e0b108c；scripts+docs 一致性）——产出 Issue #339-#351（13 个）与 #343/#346/#349 的修复 PR #352/#353/#354（均合并）。
- **R2（交叉复审）**：#357 三声明全 HOLD（MERGE）；11 个旧 Issue 质量审计——全 CORRECT 无重复，#317 第 1 项被 #298 提前修复（已重划），行号漂移不影响结论。
- **R3（补挖）**：#344 伴随形状、#343 wiring 空转、#311 工具链注意事项与 GO 拒绝残渣第三来源——已评论/入台账。
- **R4（终审）**：#354 扩展三处 + 台账与 GitHub 状态一致性核验——GO。
- **R5（续跑轮，master 44e3945）**：交叉复核窗口内生产增量——helper \`_validate_api_root\` iterdir fail-closed（修复真实的 fail-open：traverse-only 根的裸 set() 回退，探针已证）、get_status 夹具 RST 规避排水纪律（test-only，并行负载下的 ConnectionReset 消除）、rotate-logs stderr 报告（契约一致）、ziparch 名长守卫（此前已审）——**无引入缺陷**。认证 HEAD：shell-core **126/126**、sidecar e2e+relay+env+dist-lock **34/34**（apps/web 树哈希溯源断言按设计强制了 bundle 重建）。回归挖掘由并行窗内审计持续覆盖（native intake/workflow 暂停取消/ScriptView 双激活已在 R1 native 深审范围）。

### 本窗 R1（2026-09-12）结果：3 路并行 Hunter + 1 轮独立 Verifier → 11 项候选全核实（10 P3 确认，FC1 建议折叠：fake_channel 扫描是防误用标记而非安全边界——helper 已先经 alembic env.py 执行树内任意代码，目录形态零增量对抗风险）

**已归档 issue（B 账 +5）**：
- #409 assemble-web-resources retired-copy 生命周期：ignore_errors 泄漏 web.old-<pid>（pid 复用后 :114 裸 rmtree 中止构建）+ 拒绝范围 pid-local + 恢复指引过期（Move-Item 对现存 web 是嵌套非恢复）
- #410 单实例/跨客户端键失效：WPF 互斥体按词法路径哈希（subst/junction/8.3 别名绕过）+ tauri/WPF 无共享 user-data interlock → 一库两 API 服务（busy_timeout 无 WAL → 锁竞争非损坏）
- #411 WPF NativeBackend 无击杀升级：>40s 楔死宿主 → StopAsync 超时未重置状态 → 重连永久死锁；窗口关闭后宿主+sidecar 存活继续服务 DB（Job Object 只绑 helper 树）
- #412 neutrality 门漏检：模式集缺 `vertexai` 标记——vertex_credentials.py:249 的 `vertexai=True` 今天就在门外且文件不在 allowlist，check:neutrality 照样通过
- #413 start-dev.ps1 .env 按 5.1 ANSI 解码：非 ASCII 凭据变 mojibake 后提升进 env（python-dotenv 不覆盖已设值）

**台账备注（不立卷，有据）**：FA3 dist-lock bash/python 混装互斥失效——87a5ce9 已文档性裁决 "unsupported"，不重复立卷；FA4 build-frontend-static.sh link→link 链重建 glob 序依赖（fail-closed 构建中止，flaky 非损坏）——并入 #409 修复批次顺带；FC1 fake_channel 目录形态——按 Verifier 建议折叠进敌意根加固说明（建议在 _validate_api_root 契约注释标明"目录形态不拒"或在测试补一条钉死现状）；FB4 native 无全局异常面（44 个 async void 均内部有守卫，纯加固注记）。

**核实为守住（本窗新增代码面）**：7386eca 悬空绝对目标拒绝、cf4bcff swap 回滚 fail-closed、42f5e5e junction-aware 克隆、bdf8d9a 别名目录钉（与 web 侧谓词等价 + 服务端复验）、7a32cc0 PAUSED 取消生命周期（WAITING_APPROVAL→CANCELLED 重写 + UI 刷新）、7699a87 轮询节流（陈旧 ≤10s 自愈）、fc06f3c 单次加载不变量 + conflict bar 收拢、373/375/376 测试钉行为一致。

### 本窗 R2（2026-09-12）结果：native Views 深挖（首个全量 Views 状态机审计）+ shell-core 边缘 + e2e/tauri 面。3 路 Hunter + 1 轮独立 Verifier（3 个 P2 全确认，Z2 被 Verifier 追加第二个跨写窗口）

**已归档 issue（B 账 +6，累计 11/20）**：
- #426 [P2] ScriptView 衣柜多存复活已删分配（陈旧 scene 快照逐角色 PATCH、无 version、后端整体替换；范围修正：≥2 脏位且含删除时才触发）
- #427 [P2] WorkflowView 切换竞态跨工作流写图（无加载序守卫 + Activate 双加载 + 排队 flush 用陈旧画布 + 等版本绕过 CAS——Verifier 追加第二个窗口）
- #428 [P2] Reconnect 绕过 ConfirmLeaveAsync（#341 修复只覆盖 RefreshAsync；Activate 全量重载摧毁脚本表单/分镜草稿/导演草稿；应用文案「项目数据已保留」与实际行为相反）
- #429 [P3] Views 草稿守卫家族四例（GenerateView 导演草稿、Settings/ProjectSettings 表单含半输 API key、StoryboardView 章节切换陈旧渲染、LocalEditWindow 关窗丢 mask）
- #430 [P3] shell-core 生命周期三边缘（导出 ENOSPC 孤儿 .pending 且无磁盘空间检查/2GiB 内存构档、RunLog::create 失败泄漏永久不可清扫 runtime 目录、planted .rotating 目录永久楔死该日志轮转）
- #431 [P3] phase2_runner 契约套件 POSIX 静默跳过 + Job 顺序守卫仅源码字符串匹配（附带 P4 注记：e2e Windows stop 或孤儿 node 孙进程、health_probe UI 线程无连接超时）

**核实为守住（R2 clean inventory）**：shell-core——READY 64KiB cap 边界、token 常量时比较、is_loopback_origin 全拒绝形态（[::1]/0.0.0.0/127.0.0.2/unicode）、owner.json 容错、post-spawn 全失败路径 abort_spawn、sweep 名门/拒链/未来 mtime、ziparch 不变量不可达、无输入可达 panic；native——无 WebView2/URI scheme 面、token 不进 C#、Preferences/KeyValueStore 损坏安全、ApiClient traversal 校验、ExportLogs 100MB 上限 + 拒 reparse point、44 个 async void 内部有守卫；e2e——owned-tree Ctrl+C/CtrlBreak 击杀、跨 run 端口污染守卫、#350 dist lock + tree-hash 溯源、#308 picker post-open 身份校验、fake-channel 门控、断言行为化（无 sleep-and-hope）。

### 本窗 R4（2026-09-12）结果：导航/连接契约深挖 + 已立 8 issue 对抗互审（E 轮次）

**已归档 issue（B 账 +4，累计 22/20 ✓ 配额 B 达成）**：
- #446 [P3] Preferences.Save 关窗/dock 切换无 catch —— 数据目录不可写时 async void 崩溃、跳过优雅后端停止（KILL_ON_JOB_CLOSE 兜底故 P3；KeyValueStore 有对照 catch）
- #447 [P3] node 子进程继承 MANGAFLOW_STATIC_EXPORT —— plan-B 服务的 web 静默降级为静态导出形态（strip 表缺该名；#313 同族）
- #448 [P3] ScriptView 编辑表单保存无重入守卫 —— 双击同版本双 PATCH，第二个弹假 「保存未完成」 冲突框
- #449 [P3] 启动 boot-hang 复用 「提交操作可能已被服务接收」 冲突文案 + 全部图片面绕过 ApiClient 错误详情契约（EnsureSuccessStatusCode/静默 catch/裸英文）

**互审结论（E 轮次 4-5）**：R4-B 交叉审 8 个已立 issue —— 全部 STRENGTHENED/CORRECTED 无 REFUTED：#426 加重（单字符保存也会抹掉其他客户端并发新增：无版本整体替换 + 非 quiet LoadScriptAsync 孤儿化控件）；#427 加重（Verifier 独立发现第二窗口：排队 flush 在 handler1 设置 workflowId 后执行，PATCH workflows/B 携带 A 的图）；#428 加重（应用文案主动邀请重连但路径无任何 ConfirmLeave）；#438/#439/#442 全部加强；#430 修正（三机制真实、P3、stderr 有日志）；#431 确认（结构守卫是该文件 docstring 明示意图）。R4-A 新增：N1（#446）、N2/N3（#449）、media 面契约绕过；**核实守住**：幽灵持久化项目回退首页、状态文件 tmp+move 原子、后端无 401/403（本地 sidecar 无需认证流）、ConfirmLeave 重定向守卫 (:416-423)、HomeView 双提交守卫。
### 本窗 R5-R6（2026-09-12）结果：C 配额完成 + D 配额完成 + E 第 6 轮

**C — 防守测试/契约小 PR（6/6 ✓）**：
- #450 start-dev.ps1 .env UTF-8（#413）
- #451 neutrality 门补 `vertexai` 标记 + allowlist vertex_credentials.py（#412）
- #452 fake_channel 目录形态拒扫 + 契约测试（本窗新发现，FC1）
- #453 RunLog::create 失败 finalize ownership journal（#430-2；cargo 76+9+7 全绿）
- #454 assemble 孤儿 web.old-*/tmp-* 清扫 + 5 契约测试（#409-1/-3）
- #455 phase2_runner 契约测试 POSIX 化拆分（#431-1；POSIX 实跑 2/2）

**D — 旧账清账 ✓**：#149 **FIXED**（.pending 拒链 + hard_link 无覆写落位 + 目的地/pending 共享检查，HEAD 直证）；#150 四项全 **FIXED**（rotation staging-first + 3 次熔断、JobHandle Drop、PYTHONUTF8/PYTHONIOENCODING、.pending 拒链）——均有 HEAD file:line 证据评论。#307/#311 维持 lead 决策记录；#264/#265/#272/#275/#276/#309/#310/#312 前窗已有证据评论。

**E — 互审轮次（6/6 ✓）**：E1 R1 Verifier（11 项）；E2 R2 Verifier（3 P2）；E3 R3 Verifier + 结构 P3 抽检；E4 R4 JOB1 交叉审 8 个已立 issue（全部 STRENGTHENED/CORRECTED，零 REFUTED）；E5 R4 Verifier（N1 降级 P3 + N5/N6/N2/N3 确认）；E6 终轮（#429 四项确认 + JobsView 陈旧对话框新姊妹发现，证据已挂 #429）。

**F — 最终配额状态（截至本节提交）**：B **22/20 ✓**（#409-#413、#426-#431、#438-#444、#446-#449）；C **6/6 ✓**（#450-#455）；D **✓**；E **6/6 ✓**。窗口结束条件：时间到 09:00 后可 COMPLETE；若窗口延长，下一优先 = 已立 issue 的 owner 认领跟进 + 对 #426/#427/#428 修复 PR 的回归挖掘（native Views 是新开的深水区）。

### 本窗 R7（2026-09-12 续跑，master 对齐 c3d694a）结果：新修复批回归挖掘 + E 第 7 轮

- **已立 issue 关闭核验（D 延续）**：#409（cb0fc60 + #454 双清扫）、#412（b7833c8/#451）、#413（9dc0257/#450）、#430-2（633a868/#453）、#431（0f36205/#455）——全部 FIXED，证据评论已挂。6 个防守 PR（#450-#455）全部被认领合入且在新 tip 存活；#454 被 owner 扩展（cb0fc60 post-swap sweep），与 #454 的 pre-sweep 清扫在 HEAD 共存。
- **新 issue（B 账 +2，累计 24）**：
  - #457 [P3] assemble post-swap 清扫与模块 docstring/恢复契约矛盾（「different pid 不触碰」已失效）+ 并发 assemble 无锁（清扫可删除另一 run 的回滚源；fail-closed）
  - #458 [P3] runtime layout 规范化守卫只比叶名——planted symlink at user_data/runtime 父级重定向绕过（会话树落位逃逸；同用户前置；ff0a0e2 只钉了叶名失配形态）
- **E7**：新批 Hunter 回归挖掘 + lead 直读复核（docstring 矛盾与叶名守卫均经双确认）。
- **新批核实为守住**：6aba8c2 目的地目录根钉（纯测试钉）、2c598ee get_status 预拨 CR/LF 拒绝（defense-in-depth，全部调用方传常量）、#407 junction 测试平台跳过（与本窗 PR 无重叠）、#408 traverse-only fallback 测试钉（与 #452 目录形态测试互补不冲突）。

### 本窗 R8（2026-09-12 续跑）结果：E8 交叉审 + 对 6 个已合 PR 的对称挖掘

- **E8 裁决**：#457 确认（P3 公允——无 shippable 损失，dev 构建脚本）；#458 确认（生产 `create` 同形态；后果精化为 placement + token journal 披露，非 split-brain）。
- **对称挖掘新发现（B 账 +3，累计 27）**：
  - #460 [P3] _validate_api_root 阴影扫描只防 fake_channel——api_root 树同样可遮蔽 helper 的 `alembic`/`uvicorn` 裸名导入（sys.path[0]；lead 直读 import 行确认）
  - #461 [P3] build-web-standalone 严格 rmtree——锁定文件截断 DESKTOP_DIST 后，同 commit 的旧 build-info.json 使 e2e 新鲜度门放行截断 bundle（#454 同类在姊妹脚本漏修）
  - #462 [P3] neutrality 门测试模块 win32 门内含可移植断言 + allowlist 读取缺 -Encoding（#450 同族姊妹读）
- **#430 补充证据**：RuntimeLayout::create 内部窗口（create_dir_all 后、journal 写前失败 → 无 journal 目录永不回收）——#453 只修了 RunLog::create 半边。
- **E 配额累计 8 轮**。
