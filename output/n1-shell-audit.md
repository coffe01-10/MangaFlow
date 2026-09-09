# N1 shell-core 审计（2026-09-09 夜间烧 · 持续更新）

> 2026-09-08 终版经 PR #280/#287 合入 master；本文档在 2026-09-09 夜继续维护，
> 基线推进至 `961bcec`。当日增量见文末「2026-09-09 增量」。

---

（以下为 2026-09-08 终版原文，历史结论以当时基线为准）



- 范围：`apps/desktop/shell-core/**`；验证驱动仅触及 `apps/desktop/scripts/**` 的
  shell 相关测试；`apps/desktop/native/**` 与 `native-tests/**` 全程未动（硬禁区）。
- 基线演进：起跑 `origin/master` = `2e5772b`；期间 PR #277–#281（并行夜间代理）与
  本轮 PR #280 被连续合入，终版审计锚定 `1a53b4a` + 本分支增量。
- 方法：两个只读深读子代理覆盖全部源文件（logs/ziparch 与 protocol/handshake/
  ownership，合计 107 条原始发现），组长行级复核后收录；三轮独立子代理审查
  （E 项）的阻断项全部修复；每个修复在旧实现上做过红绿判别（C 项）。
- 证据：`cargo test` 9/9 套件全绿，**78 项**（单元 46 + 集成 32），Linux 实测；
  红绿判别逐条记录于各 commit message 与下表。

## 1. BUG 账本（全部已修或明确边界）

| ID | 位置（基线行号） | 缺陷 | 状态 |
| --- | --- | --- | --- |
| BUG-1 | ziparch.rs:91,106 | 成员名 `as u16` 截断 → 结构性损坏归档 | 已修（assert + 边界测试） |
| BUG-2 | logs.rs place_archive | overwrite 先删后改名，失败即丢用户归档且孤儿 `.pending` | 已修（POSIX 原子替换优先 + 全失败路径清理；Windows 已存在→回退分支同样清理） |
| BUG-3 | logs.rs shift_generations_up | 轮转 shift 前先删最旧世代，失败不可回滚 | 已修（staged 化 + 提交后才删；round-3 把删除再推迟到 rotate_file 终步成功之后） |
| BUG-4 | logs.rs 成员读取 | `fs::read` 无上界，collect 后暴涨的日志被整读进内存 | 已修（`take(cap+1)`，超限按 changed 拒绝） |
| BUG-5 | logs.rs:735 | 子目录 read_dir 失败中止整个导出 | 已修（skip-and-report） |
| BUG-6 | ownership.rs stop/Drop | Unix 上 helper 自行退出后组信号永不发出，后代孤儿化（plan B node） | 已修（stop 观察到子进程退出即升级组 SIGKILL；Drop 对已 reap 的树做 group-only 补杀） |
| BUG-7 | protocol.rs:81 | READY pid `as u32` 回绕绕过归属校验 | 已修（try_from 拒绝 + 判别测试；round-1 修掉了测试字面量自身的优先级错误 `4242+1<<32`） |
| BUG-8 | logs.rs:515 + protocol.rs:247 | sweep 契约承诺 stderr 报告但 Result 被丢弃；mark_stopped 把损坏 journal 重置为 `{}` 后会被 sweep 删除 | stderr 已落实 + 接线测试；mark_stopped 语义属行为变更，记录为待 lead 决策项（见 §5） |
| BUG-9 | ownership.rs TerminateJobObject | Windows 升级杀结果被丢弃，可能无限等待 | **代码修复 NOT RUN**（Windows 腿；本环境不可证）——由后续 Windows 实机轮验证 |

## 2. RISK（本轮新记录；多数为受控窗口，详见各文件注释）

- token 比较非常量时间（helper 本持 token）；`u16::from_str` 接受 `+80` 端口。
- READY 行 / journal 读取无长度上界（PR #277 handshake-stream-caps 已并入后请以
  其为准复核；本轮未重复实现）。
- 健康门不验身份（回环 200 即过）；get_status 全量缓冲。
- starttime 锚点缺失即跳过且仅 cfg(unix)；Windows 无创建时间等价物。
- fork→prctl 窗口；setsid 后代逃逸组（无 KILL_ON_JOB_CLOSE 等价，已由 group-only
  补杀缩小）；Windows 线程快照 PID 复用窗口。
- 导出全归档内存缓冲 + 位转 CRC；目标父链验证后内核重解析窗口；Windows ADS 目标名；
  rotate_if_large 尺寸-重命名交换窗；熔断态每行重开句柄；并发导出同目标的
  `.pending` 误报。
- abort/stop 失败被 `.ok().flatten()` 吞掉（src-tauri 侧同型）。
- pub API 可达 panic（new_token/BCrypt/write_journal_atomic/unix_now）。
- 本轮新增已审：`.rotating`（base 内容）与 `.rotating-oldest`(历史) 的清理不对称——
  前者可重试、后者绝不无条件删（见 §1 BUG-3 修复与其文档）。

## 3. D 项边界测试台账（新增/重写）

- ziparch：65,535 字节名往返 + 第 65,536 次 panic；u16 成员数上限；dos_date_time
  2107 上钳。
- 导出：恰好 64 MiB 成员纳入；超限 `.pending` 目录；覆盖式改名失败判别；
  locked-subdirectory（root 跳过）；成员 shrink/涨由 `take(cap+1)` + re-check 结构性
  约束（+1 的 call-site 变异不可见性已在审计记录，属残留）。
- 轮转：阈值-1 不轮转；shift 失败 unwind 且 `.5` 幸存（重写了固化损失行为的旧断言）；
  final-rename 失败时 `.4`/`.5` 幸存；`.rotating-oldest` 双态（占位 → fail+保留；
  空位 → 自愈重试）；restore 拒绝覆盖占位槽。
- 启动协议：孤儿化 Drop 判别（sleep 输出重定向防管道悬挂）；stop 幂等（协作退出码
  7 缓存判别）；SigIgn/SigCgt 位 14 轮询替代固定 300ms 竞态；真实 reap deadline（原
  `Instant::now() < Instant::now()+5s` 恒真）；pid 回绕单测；sweep 接线测试。

## 4. C 项红绿判别（每集成套件 ≥1）

| 套件 | 还原的生产修复 | 结果 |
| --- | --- | --- |
| startup_protocol | `process_group(0)`（波次）/ Drop 组补杀（本轮） | 红 ✓ / 红 ✓ |
| picker_policy | `reject_intermediate_links`（5aad7bc） | 红 ✓ |
| delivery_contract | capabilities 注入 `remote` 键 | 红 ✓ |
| log_export | overwrite placement / subdir-skip | 红 ✓ / 红 ✓ |
| log_rotation | 删除先行的旧 shift | 红 ✓（重写后的判别测试） |

## 5. E 项三轮审查记录

- 轮 1（2 代理）：107 条原始发现（BUG 候选 7）→ 全部行级复核 → 切片修复。
- 轮 2（1 代理）：BLOCKER（cfg-gated 测试 glob 被删 → Windows 腿编译断裂）、
  MAJOR（自愈失败返回 success 形态导致 POSIX 覆盖活世代；staged 删除时点仍偏早）
  及 6 MINOR → 全部修复；`4242+1<<32` 测试字面量优先级错误在本轮修正。
- 轮 3（1 代理）：HOLD → 4 阻断（未 cfg-gate 的 /proc 轮询、restore 覆盖占位槽、
  自愈重试在 round-2 塌缩中丢失、wait_until_gone 在 Windows 空转）→ 全部修复 +
  3 个新夹具（双态 leftover、restore 拒绝）；轮内同时确认了 unwind/restore 次序、
  位 14 掩码、`take(cap+1)` 语义与 `.1` 重占路径的正确性。
- 最终 Linux 状态：`cargo test` 78/78；Windows 腿 NOT RUN。

## 6. 交付物

- `night/n1-core-burn-20260908`（PR #280，已合并）：六切片修复 + 附属交付。
- `night/n1-core-review1-fixes`（PR #287，待审）：三轮审查修复，7 commits。
- 本审计文档：`output/n1-shell-audit.md`（随分支交付）。
- 另见保留分支 `night/preserved-desktop-scripts-20260907`（上一夜被重置的
  windows-d-checks 驱动等，未并入，供 lead 处置）。

## 7. NOT RUN / 待决策

- 一切 Windows 腿（BUG-9、Job Object、junction/ADS、MSI）：无 Windows 实机。
- mark_stopped 对损坏 journal 的重置语义：改成"保留损坏原文"属行为变更，留 lead
  决策（BUG-8 关联）。
- `take(cap+1)` call-site 的变异不可见性：无测试能在不引入 seam 的前提下覆盖
  "+1 的缺失"，已提取说明并记录（read_bounded 语义有 tiny-cap 单测约束）。
- 真实供应商、真实 WebView2、N=20 性能门禁（W-18/19）：沿项目边界，未动。


---

## 2026-09-09 增量（基线 `961bcec`）

 Overnight 并行轮次（review-round 系列、relay 矩阵、web-exit-detection、
 handshake-stream-caps）已消化上一版审计中的多条 RISK（READY 行/journal/健康响应
 读取上界、relay 连接上限等）——复核确认后不再重复实现。

本轮新增修复（每项独立 commit，红绿判别见 commit message）：

1. `token_matches`：READY/journal 的 owner token 比较改为长度无关折叠
  （防御纵深；两侧本已持 token）。测试：长度/字节差异仍判不匹配。
2. `is_loopback_origin`：端口只接受纯数字（`u16::from_str` 接受 `+80`，
   `http://127.0.0.1:+80` 曾能通过回环门）。测试判别：'+' origin 现被拒。
3. `mark_stopped`：不可解析 journal 不再被 `{}` 桩覆盖（桩会交给 sweep 删除，
   正好销毁取证记录）；保留原文并上 stderr 报告。红绿：损坏字节逐字幸存。
4. 导出：非 UTF8 文件名跳过并报告（lossy 转换会把两个不同名折叠成同一
   ZIP 成员）。
5. picker：表驱动形状边界（空/相对/不存在/超长成员/超长总路径/锁定祖先），
   确定性行断言精确变体（EmptyPath/NotAbsolute/DoesNotExist），root 跳过。
6. 轮转：keep=1 退化路径钉测（base 腾空、单一世代、staged 清理）。

测试增量：`cargo test` 9/9 套件、**80 项**全绿（46→52 单元 + 32 集成 + picker 7
等，Linux 实测）。

### 2026-09-09 深夜配额账本（B/D 项）

8 个独立小 PR（类别 → 分支/PR）：

| # | 类别 | 分支 | PR | 分支内容 |
| --- | --- | --- | --- | --- |
| 1 | 启动协议 | night/n1-09b-ready-journal | #322 | journal 读取上界、token 折叠、数字端口门、mark_stopped 非对象保留、starttime/篡改矩阵 |
| 2 | OwnedTree/stop + 红绿判别 | night/n1-09b-stop-respawn-tests | #323 | ReadyTimeout 端到端、respawn 纪律、lead 测试真实 deadline |
| 3 | picker 路径 | night/n1-09b-picker-shapes | #324 | 表驱动形状边界（含精确变体与真实深树） |
| 4 | zip/log 导出上限 | night/n1-09b-log-caps-nonutf8 | #325 | 非 UTF8 跳过、keep=1、总量等值边界 |
| 5 | CSP/capability 契约 | night/n1-09b-capability-surface | #326 | 能力面 + withGlobalTauri 钉测 |
| 6 | 错误路径 | night/n1-09b-error-display | #327 | OwnershipError Display、pid_starttime 钉测 |
| 7 | 启动协议（补充） | night/n1-09b-loopback-matrix | #328 | 回环形近 origin 矩阵、journal 目录错误路径 |
| 8 | 文档账本 | night/n1-pr-readme-ledger | #329 | README cargo test 计数同步（49 → 85） |

伞形集成 PR：#302（night/n1-core-burn-20260909，含上述全部内容与审计文档）。
合并顺序建议：先 #302，其余按类别各自独立或随后 rebase。

### 2026-09-09 深夜 · 审查轮次与 rebase 后续账（C/D 项）

- 轮 1（2 代理并行）：protocol/journal 加固 + logs/picker/tests 侧——发现 sweep 无上界
  读取、mark_stopped 非对象 panic、stand-in 平台脆弱性、文档错挂等 → 全部修复
  （commits 947dead..2a9916b 段）。
- 轮 2（1 代理）：确认 12 项（2 MAJOR：stand-in Windows 崩溃、非对象根 panic 未真
  修）→ 全部修复（2a9916b）。
- 轮 3（1 代理）：HOLD——指出 9e07e61 提交信息与 rebase 后 diff 不符（per-file PR
  先行合入所致的中间态）+ 4 MINOR → MINOR 已修（1bd05c4），errata 如下。
- 轮 4（1 代理）：HOLD——web-origin 测试的 linux 门缺失（Windows E0425）+ 3 MINOR
  → 全部修复（06169cd）。终态：cargo test 9/9 套件、98 项全绿（Linux）。

**Errata（提交信息勘误）**：rebase 线性化使 9e07e61/6a5330e 等提交的 diff 与其信息
表述出现错位（内容已由并行合入的 per-file PR 先行落在 master，rebase 重放的 diff
呈删除形态）。HEAD（06169cd）为完整终态，review 以 HEAD 为准；历史中间提交的
信息不再改写。

**后续 PR**：分支 `night/n1-core-burn-20260909` 对 master 的剩余 delta（journal
读写分类、mark_stopped 保留、staging 结构修复等 7 commits）以新 PR 提交待审。

### 轮 2 审查（子代理）与 erratum

- 判定 SHIP（本范围）。NIT 已落实：readback 测试改名覆盖 deleted 用例。
- **Erratum**：commit c66bc14（"Refuse a deleted-after-pick read…"）只新增了
  unix_now 钳制与注入缝测试——被删后读取的拒绝行为在 master 既有代码中已存在
  （picker.rs canonicalize-first），该提交只是补钉测；标题不改变行为这一事实
  以本记录为准。
- pid_starttime 现为 cfg(target_os = "linux")；verify_journal 锚点块同门。
  macOS 腿无锚点覆盖（设计内，受支持腿为 Linux + Windows）。

新记录的待办（下轮候选）：

- `write_journal_atomic` 的 serde_json unwrap 与 `unix_now` panic 路径（pub API
  可达 panic）——需要 no-panic 化重构。
- 健康门不验响应身份（回环 200 即过）——设计级，需 lead 决策。
- keep=1 与 sweep 的交互（清扫对 keep=1 基名的世代匹配）未测。
- 并发导出同目标的 `.pending` 竞争（失败方误报 PendingIsSymlink）——需 seam。
