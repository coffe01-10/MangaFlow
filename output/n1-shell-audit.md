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

### 轮 4 终验（子代理）与收尾

- 终验判定：**SHIP**（0 BLOCKER/0 MAJOR）。两条新测试（new_token 契约、sweep
  symlink 拒绝）经构建追溯确认非恒真、红据充分；cfg 门一致（linux）；diff 无
  native 路径触碰。
- 终验 MINOR 已修：sweep-symlink 测试的 Windows symlink 创建失败改为优雅跳过
  （本环境 Linux 不受影响）。
- 如实记录：`get_status_caps_the_response_read`（master 既有，流上限波次）在
  全量并行负载下出现过一次时序 flake（单测隔离 3× 稳定绿）——非本轮 delta 引入，
  留给该测试的归属轮次处理。
- 终态计数：cargo test 9/9 套件、**103 项**全绿（单元 61 + 集成 42，Linux 实测
  2026-09-09）。Windows 腿 NOT RUN。

### 续跑增量（分支重置恢复 + 三个边界 pin）

- 分支恢复：lead 侧将 master/分支重置回 #330 谱系，6 个丢失提交
  （deleted-after-pick pin、clock clamp seam、token 契约测试、sweep symlink
  拒绝、round-4 账本、windows-skip）已 cherry-pick 回当前谱系并全绿。
- 新增 pin（commit 2cb6321）：
  - journal 符号链接 → JournalMissing；非 UTF8 journal → JournalMismatch("non-utf8")。
  - sweep 对未来 mtime 的 terminal journal 保持目录（时钟偏移 fail-closed 腿）。
- 测试增量：单元 63 项全绿（+2 相对 round-4 的 61；含 journal symlink/non-UTF8
  钉测与 sweep 未来 mtime 对照组）。轮 5 审查（本轮 pin + 恢复提交）判定 SHIP，
  5 条 NIT 已落实（outside.json 字节断言、aged 对照组、文档归属修正、TODO 勘误）。

- NIT 已修：journal symlink pin 补 outside.json 字节断言；sweep 未来 mtime 测试
  补 aged 对照组（hex 名修正后通过）；TODO 勘误（unix_now 已修）。
- 残留：write_journal_atomic 的 serde_json unwrap（Value 序列化实际不可失败）；
  get_status_caps 并行负载 flake（master 既有）。

### 20260911 夜间烧（基线 fe22969）

- 谓词边界表：is_runtime_dir_name（大小写/长度两侧/字符集/前缀漂移）与
  is_valid_token（大小写/长度/字符集/空串）直接钉测——sweep 的删除与日志名
  谓词以这两个门为前提，假阳性会扩大删除面。
- write_journal_atomic 去 unwrap：序列化错误映射为 io::Error（InvalidData），
  teardown 热线按契约保持无 panic。
- Linux cargo test 9/9 套件、110 项全绿（fe22969 + 本批）。
- 类别覆盖：启动协议（journal 校验）✓ picker（形状/注册表边界）✓ 导出上限 ✓
  错误路径 ✓ 红绿判别（谓词表）✓；OwnedTree/stop 与 CSP/capability 已由
  20260909 波次交付（#323/#326）。
- Windows 腿 NOT RUN。

### 20260911 续跑轮记录（review rounds 5-6 + 恢复）

- 轮 5（针对 20260909 夜遗留 pin 的复审）：SHIP + 5 NIT → 全部落实
  （outside.json 字节断言、aged 对照组、文档归属修正、TODO 勘误、pin 哈希落账）。
- 轮 6（针对续跑批）：HOLD → 修复：readback 交换测试改为 rename 型（tmpfs 的
  inode 复用曾挫败 (dev,ino) 身份检查——环境性 flake，现确定性检测）；
  get_status 夹具排干修复随分支重置丢失后重新落地（请求头排干 + 尾部排干 +
  128 KiB 收敛 + 精确截断断言）。
- 终态：cargo test 9/9 套件、**110 项**全绿（单元 66 + 集成 42，Linux 实测
  2026-09-11）。Windows 腿 NOT RUN。
- 轮 7 补充（续跑恢复后新增）：picker 对 FIFO 祖先的 pick 干净拒绝
  （元数据检查不阻塞，测试含 FIFO 夹具）——eecf33d。

### 轮次与整合收尾（2026-09-11 续）

- 谓词边界表、token 门表、精确截断断言、FIFO/目录/缺失 journal pin 与
  文档归位已全部落在分支 tip；cargo test 9/9 套件、121 项全绿。
- 分支重置事故恢复：48ce9e3（目录/缺失 journal pin）与 650672a/3bdd9f5/fb70420
  （token 契约/sweep symlink/renamed swap doc）已全部回收到本分支谱系。
- 残留：write_journal_atomic 的 serde_json unwrap（Value 序列化实际不可失败，
  仅警告级）；健康门身份校验（设计级，需 lead 决策）。

新记录的待办（下轮候选）：
- 残留：write_journal_atomic 的 serde_json unwrap（Value 序列化实际不可失败）；
  健康门身份校验（设计级，需 lead 决策）。get_status_caps 并行时序 flake 的
  夹具根因已由本轮夹具加固消除。

- ~~`unix_now` panic 路径~~ 已修（commit 2cb6321 注入缝钳制，见轮 5 记录）；残留：
  `write_journal_atomic` 的 serde_json unwrap（Value 序列化实际不可失败，仅警告级）。
- 健康门不验响应身份（回环 200 即过）——设计级，需 lead 决策。
- keep=1 与 sweep 的交互（清扫对 keep=1 基名的世代匹配）未测。
- 并发导出同目标的 `.pending` 竞争（失败方误报 PendingIsSymlink）——需 seam。

### 20260912 夜间烧（基线 44e3945，分支 night/n1-core-burn-20260912）

开跑：fetch + reset --hard origin/master（44e3945，含 lead 的 native/review-loop 文档
与 #401）+ clean；分支即建于 tip。基线实测 cargo test 9/9 套件、126 项全绿。
native/** 与 native-tests/** 零触碰（今晚 master 含 native 改动，全部绕开）。

配额 B 账（16 枚独立小 PR，全部 Linux cargo 绿、单主题、基 44e3945）：

| PR | 分支 → sha | 主题 |
| --- | --- | --- |
| #402 | night/pr-dist-lock-fallback-hardening → 22bb150 | dist 锁 fallback release 所有权检查 + fd9 超时关闭 + noclobber 测试缝（3 新测试） |
| #403 | night/pr-pick-kind-unit-pins → (见分支) | PickKind::parse 精确表 + dialog_filter↔allowed_suffixes 对应 + 上限对齐 |
| #404 | night/pr-pick-error-display → 229f880 | PickError Display 全 13 臂（返工补 Grew/Swapped 两臂） |
| #405 | night/pr-spawn-error-display → (见分支) | SpawnError Display + source() 链（仅 Io/Verify/Ownership） |
| #406 | night/pr-export-non-utf8-skip → c86bacf | 导出器非 UTF8 名跳过 + skip 报告携带 lossy 名（行为小改进） |
| #416 | night/pr-export-staging-debris-skip → (见分支) | 轮转 staging 残骸跳过 + lookalike 极性对照 |
| #417 | night/pr-export-fifo-skip → (见分支) | 日志目录 FIFO 跳过（通道限界线程，永不打开管道） |
| #418 | night/pr-destination-dir-root-pins → (见分支) | DestinationIsDirectory/NoFileName 两臂 |
| #419 | night/pr-runtime-layout-symlink-guard → (见分支) | RuntimeLayout 规范名守卫（create_with_token 缝，去coy 不落 journal） |
| #421 | night/pr-go-write-epipe-abort → (见分支) | GO 写 EPIPE 中止（子进程关 fd0 确定性触发）+ 终态记录 |
| #422 | night/pr-journal-mismatch-abort → (见分支) | journal 与 READY 不一致 → Verify(JournalMismatch("state")) 中止 |
| #423 | night/pr-signal-fallback-pins → (见分支) | signal_tree 逐 pid 回退（手工非组长树）+ contains_pid 负臂 |
| #424 | night/pr-health-path-controls → (见分支) | get_status 拒绝 CR/LF 路径（拨号前，错误种类钉序） |
| #425 | night/pr-pick-registry-replace → (见分支) | pick 注册表重选替换语义 + 未知路径负臂 |
| #432 | night/pr-oversized-ready-line → (见分支) | 超长无换行 READY 行按上限截断 → BadJson 中止 |
| #433 | night/pr-empty-logs-export → (见分支) | 空日志目录导出 = manifest-only 档案（python zipfile 外部校验） |

配额 C 账：
- 轮 1（子代理，#402–#406）：5× SHIP，0 BLOCKER/0 MAJOR；3 MINOR 全返工——
  #402 waiter 时序裕量（4s 持锁）+ release 显式 return 0；#404 补齐 13 臂；
  #406 报告名诚实性 → 并折叠 lossy 名改进进本 PR。审查含突变法红绿验证与
  双 PR 合并冲突检查（picker 两模块名不冲突）。
- 轮 2（子代理，#416–#422 批）：进行中，结果下轮记录。
- 轮 3-6：窗口纪律继续挖相邻模块后补。

实测：各分支 cargo test 全绿（基线 126 + 各自 1–3 枚增量）。Windows 腿全部
NOT RUN（无实机）；#419 注明其 Linux pin 同时充当 Windows 大小写漂移的替身钉。
sidecar 零改动（留 N2）；roadmap/development-progress 未动；未 merge master。

#### 20260912 续：轮 2 结果、返工与 doc-sweep 收割（PR #434–#436）

配额 C 轮 2（子代理，#416–#422 六分支，含 merge-tree 预演）：4× SHIP，2× HOLD，
均一处级返工，已全部推上分支——
- #418 HOLD→修：`Path::new("/")` 在 Windows 非绝对（无盘符），该 pin 在主平台
  直接失败 → 按平台取 nameless absolute root（`C:\` / `/`），并断言 root 本身
  absolute，保证测的是 NoFileName 守卫而非绝对门；Display NIT 改直写。
- #421 HOLD→修：外层 doc 注释仍是旧稿（"journal 省略 anchor / 死 pid 双 None"）
  与脚本相反——死而未收的子进程是 zombie，/proc 仍应答，省略 anchor 反而触发
  StartTimeMismatch。已改为与脚本一致的真实理由。
- #421 附加加固（野外捕获）：单独满套件跑曾出现 1 次失败——即轮 2 披露的
  "GO 写先于 close(0) 落入缓冲"窗口（负载下子进程被抢占）。改为**先关 fd 0
  再发 READY**（stdout 是 fd 1 不受影响），任何交错下 GO 必 EPIPE；10/10 稳定。
- 合并冲突预告（轮 2 实测 merge-tree）：#416/#417 同点 EOF 追加 log_export.rs、
  #421/#422 同点追加 startup_protocol.rs——fn 名不冲突，后合方机械 rebase。

doc-code 不一致 sweep（子代理，双源核验）收割 6 项 → 3 PR：
- #434 README 状态行对账：安装器链 4 处"仍 NOT RUN"与 D1（2026-09-06 双产物
  构建 + NSIS 实机装/卸）矛盾 → 按 D1 改写；cargo test 计数 85→126（本轮实测）；
  plan-B e2e 17→21（pytest 收集复核：e2e 9 + relay 9 + bind 3，首验 17 项留档）；
  维护行数 3,323→6,231 / 1,900→2,742（wc 实测）；scripts 图补 dist 锁两文件
  （#343：不列=不测）。
- #435 rustdoc NOT-RUN 对齐：shell-sim "须在 Windows 复验"、OwnedTree::spawn
  "Windows 行为仍 NOT RUN"、delivery_contract 头"MSI/NSIS 安装升级卸载均 NOT RUN"
  ——三者均已被 D1 的 2026-09-06 Windows 实机轮超越（NSIS 装/卸 RUN），改为
  引用 D1 并保留真实残留（MSI 安装步 + 跨版本升级）。
- #436 verify-static-origin.mjs 注释勘误：sibling 探针夹具"先于服务器创建"不实
  （listen 先行）——按实际顺序改写并注明顺序无关。

诚实记录：#434/#435 引用的 D1 记录系 README 既有内容，本轮仅核对文本一致性，
未复跑任何 Windows 实机验证（Windows 腿整体 NOT RUN 不变）。

- 红绿判别力抽查（本轮配额项，突变法）：#403 的 parse 精确表在 parse 改为
  大小写不敏感时确实变红；#418 的 DestinationIsDirectory pin 在 is_dir 守卫
  被禁用时确实变红。抽查通过，两钉均非恒真。

#### 20260912 续二：轮 3 结果（8× SHIP）与返工

- 轮 3（子代理，#423–#436 八分支，含逐分支安全清扫与事实核查）：全部 SHIP，
  0 BLOCKER/0 MAJOR。一条 MINOR 返工（#434）：量测日期口径与同句两个陈旧计数
  （tests/ 1,100→2,857；main.rs 338→570）已刷新并统一为 2026-09-12 wc 实测。
  NIT 返工一处（#423）：foreign 子进程改为 kill-on-drop 守卫，任何断言路径
  （含限界挂起失败）都不再向宿主泄漏一小时 sleep 进程。
- 轮 3 事实核查确认：21 项 e2e 计数（pytest 收集）、126 项 cargo 计数
  （130 − 4 windows-gated）、6,231/2,742 行数、安装器行与 D1 一致、
  capability/get_status 全部 8 个调用点均传 HEALTH_PATH。
- 红绿抽查与 python 侧 dist 锁复核：python `_dist_build_lock` 超时路径在
  unlink 之前 SystemExit，无 bash 侧已修的同款"赢家锁被删"缺陷（核验记录）。
- 新增 PR：#437（user_data 指向文件 → SpawnError::Io 首步拒绝 + 零残留）、
  #445（并发 record：8 线程×50 记录 = 400 条完整 JSONL，并发类别钉测）。
  本 Goal 累计 21 枚独立小 PR。
- 轮 4 已派出（返工核验 + #437/#445 新审）。

#### 合并顺序提示（给 lead，20260912）

- 本夜 21 枚 PR 分支均基于 44e3945，互不 rebase；EOF 追加型冲突集中在四个文件：
  tests/log_export.rs（#406/#416/#417/#433）、tests/startup_protocol.rs
  （#421/#422/#423/#432/#437，且共含同一处 `use std::fs;` 导入块——三方合并可自动
  取同侧）、src/picker.rs（#403/#404/#425）、src/handshake.rs（#405/#424 的测试
  追加块）。所有测试 fn 名互异，冲突解法均为"两块都保留"；每合入一枚后，其余
  分支 `git rebase origin/master` 即可，需要我代 rebase 任何分支请直接指定。
  src/logs.rs 三分支（#406/#418/#445）改动区域不同（collect_members / 测试锚点），
  预期无冲突。
- 勘误（轮 5 更正前条）：.lead-tmp/* 并不在那 4 条旧分支上——它由 lead 以
  a08dae8 直接提交在 **master**（70+ 文件），本夜全部 21 枚分支的基树因此都
  继承它；各分支相对 master 的 diff 不触碰该目录，合入不引入新内容。原
  "旧分支不应复用为载体"警告对象有误，以本条为准。

#### 20260912 续三：轮 4 与轮 5 结果、关键返工

- 轮 4（子代理，三项返工核验 + 两个新分支）：3 SHIP；**2 HOLD，均一处级**：
  - **#445 critical（自查自纠）**：红绿抽查用的 `if false &&` 突变残留在保存的
    fixture diff 里，被 #445 分支携带——生产代码 `validate_destination` 的
    is_dir 拒绝被禁用。已 revert；同分支测试插入曾搁浅
    `run_log_record_survives_mutex_poisoning` 的 #[test]（毒化覆盖静默消失、
    新钉双重注册）——归位。两钉各自单收，套件 127 绿。
  - #423 medium：kill-on-drop 守卫原位于两枚 pin 之后，守卫前的 panic 仍会
    泄漏 sleeper——先取 pid、守卫上提至 spawn 之后，断言用保存的 pid。
  - 其余：#418/#421/#434 返工全部核验通过（#421 的 close-before-print 被证明
    "任何交错都不可能投递 GO"）；#437 SHIP。
- 轮 5（子代理，横切面：账本 + 21 份 PR 描述 vs 分支实况）：
  - 账本三项 sha、126 基线（130 − 4 windows-gated）、轮 1-3 返工记录、合并
    顺序提示全部核验属实；1 条勘误纠错（.lead-tmp 在 master，见上）。
  - 12 份 PR body 系统性算术错误（"128 green" 应为 127）→ 已逐 PR 评论更正；
    #404 此前已更正；#406 body "Test-only" 不完整（含 src 小改）→ 已评论披露；
    #421 body 陈旧于返工后实现 → 已评论更正。其余 body 全部与 diff 相符。
- 突变残留教训入账：红绿突变必须与被测分支物理隔离，保存的 diff 须在
  应用前 grep 排除突变指纹（本轮起执行）。
- 合并交互预警（同仓并行代理）：#415（glm/lock-header-accuracy）与 #420
  （glm/dist-lock-latch-and-header）同样改 scripts/dist-build-lock.sh 的
  头注/分支门，与 #402 三方相碰——lead 侧请先合其一，余者 rebase；
  三者的语义意图（锁分支一致性/头注准确性/测试缝）不互斥。

#### 20260912 轮 6（终验扫）与账本收口

- 轮 6（子代理，21 枚分支终态扫）：**21/21 PASS**——安全（零 native/**、
  native-tests/**、output/roadmap/.env 路径；合并基全部 44e3945）、突变指纹
  全零（确认 #445 的 `if false &&` 泄漏在保存 tip 上彻底消失）、远端 tip 与
  本地一致 21/21（无未推送返工）。合并顺序提示对最终 tip 复核仍然有效。
- 勘误（补 20260911 段）：该段"121 项全绿"系中断会话的未验证计数，从未构成
  证据（当时已注明"不作为证据"）；20260911 的权威实测为 110 项（fe22969 谱系），
  其后各夜计数以各段实测记录为准。
- 账本 tip 审阅点：本轮 6 次审查全部落账；21 枚 PR 全部开放待 lead 审合。

- 新增 #456（night/pr-grandchild-pid-refusal → 见分支）：Unix 替身钉——
  launcher 链契约的拒绝侧（Unix 成员=仅直接子，fork 真孙进程以己 pid 发
  READY → Verify(PidMismatch) 拒绝；abort 组停收割双进程 + journal stopped，
  有界 /proc 扫描防泄漏）。Windows 侧验收钉原为 cfg(windows)，此为其
  fail-closed 对偶。累计 22 枚。
- 轮 7（子代理，两条尾部分支）：#424 SHIP 零发现（held-listener 判别力经
  独立复现验证：post-dial 守卫确实落到 TimedOut，1s×3 有界）；#456 HOLD →
  已修：/proc 扫描改用 proc_dead（容器 init 不收尸时 zombie 不得计为存活），
  doc 去夸口（扫描只验证死亡，收割是 abort 的组停；测试二进制被硬杀时
  sleeper 以两分钟自清），stand-in sleep 3600→120。
#### 20260912 同步新远端与 rebase 波次（lead 指令）

- `git fetch` 后 master 已由 44e3945 前移至 c3d694a→d0aaf9f（lead 实时合入
  本夜 PR 与并行代理 PR：#402/#403/#404/#405/#406/#407/#408/#414/#415/#416/
  #417/#418/#419/#420/#424/#425/#434/#435/#436/#445/#450-456 等）。按指令对
  仍开放的 12 枚分支逐一 rebase/reset 到最新 master 并全量复测，force-with-
  lease 推回（均含本 Goal 独有提交，未丢弃）：
  #403/#404/#405（picker/handshake EOF 冲突，双模块保留；#405 首轮解析缺闭括号
  已修复）、#406（rebase 中重构：以 master 版为底追加最终形态测试，2 commit）、
  #417（首 rebase 误丢测试 → 从分支 blob 重建单 commit）、#421/#422/#423
  （startup_protocol EOF 冲突，取分支侧）、#432/#433/#437（reset 到 master 后
  重放单 commit）。
- **异常记录**：#423 首轮全量复测曾出现 1 次未具名失败（137/1），此后 3 轮
  全量 + 6 轮定向全部 138 全绿——按既有时序 flake 观察项挂账，若复现以
  失败名归因。
- **#456 勘误与补钉**：#456 在 round-7 返工推送前被合入（merged at 4dbb6ac），
  proc_dead 僵尸扫描修复滞留分支 → 新开 #459（单 commit，基 d0aaf9f）补送。
- 全部 rebase 分支 Linux cargo 全绿（134-139 不等，随 master 基线增长）。
  Windows 腿 NOT RUN；native/** 零触碰。
- #420（glm/dist-lock-latch-and-header，并行代理）仍开放，与 #402（已合）
  的锁文件后续改动无冲突（#402 已在 master，#420 rebase 责任在其作者侧）。

- 红绿抽查补样（窗口纪律继续）：#422 的 `JournalMismatch("state")` 钉在
  verify_journal 的 state 比较被禁用时变红（panic 消息如实报告实际 variant）；
  #445 的 400 行完整线钉在 record 静默丢行时变红（0 ≠ 400）。累计抽查 4 钉
  （#403/#418/#422/#445）全部非恒真。

#### 20260912 轮 8（rebase 波次终检）与五分支重建

- 轮 8（子代理，11 枚 rebase 后分支逐支核验）抓获**本轮最严重缺陷**：
  手工 rebase 的"取分支侧/从底重建"解决在 5 枚分支上**静默删除了 master 侧
  较新测试**（#456 的 grandchild 钉、#417 的 fifo 钉、#403 的 mod tests）——
  各 tip"全绿"恰因被删测试不再运行。逐一以故障安全法重建：
  `checkout -B <branch> origin/master` + 从旧 tip 机械提取本支 payload 追加
  （fn 名锚定 + 硬化存在性断言），每支全量 cargo 绿：
  - #404：仅追加 mod display_tests，与 master 的 mod tests/registry_tests
    三模块共存（141 绿）。
  - #406：仅追加 non-utf8 钉 + logs.rs lossy-report 变更；测试 doc 的陈旧
    表述已改正（141 绿）。
  - #421：master 实测无 go-write 测试（早前"已在"信号系冲突标记文件的假
    阳性——本代理工具错误，已在 PR 评论自纠），完整 payload 重放
    （close-before-print 硬化 + 更正后锚定理由）（141 绿，3/3 定向稳定）。
  - #422：仅追加 journal-mismatch 钉（141 绿）。
  - #423：仅追加 signal 回退钉 + hoisted 守卫（141 绿，12 次定向复跑干净）。
  - #432 关闭（superseded）：oversized READY 钉已在 master
    （oversized_ready_line_fails_verification_quickly，a5e739c，wave 中独立
    合入）；重建分支成空壳（两空行），按审查建议关闭而非重复。
- **未具名一次性失败挂账**：rebase 后首轮全量曾各出现 1 次无失败名失败
  （#423 谱系两次）；此后 4 轮全量 + 8 轮定向 + 多轮复跑全部全绿。
  归因未定，若复现以失败名追责。
- 轮 8 方法教训：手工冲突解决后，"套件绿"不充分——必须**显式断言 master
  既有测试清单在合并后仍在**（fn 名清单比对）。本轮起作为 rebase 收尾步骤。
- 账本分支自身亦按此教训 rebase 到最新 master（f304a1f）：取 master 版
  logs.rs（意外夹带的 #418 测试在重放中自愈反转）与 master 版审计（并行
  代理的轮次记录在 wave 中入账），今晚各节作为纯尾差重新追加；套件 140 绿。

### 20260912-wknd 夜间续（基线 f43ac71）

- 基线复核：cargo 149 全绿、node 13 全绿、pytest（assemble/phase2 contract）9 pass。
- 波次吸收确认：#487-#499（13-issue mega round）全部携带钉测——relay transient/
  terminal accept 双侧、pump partial-start、fake_channel cleanup、static-export
  strip（env 测试已扩展 #447 断言）、staging 自愈（absorbs-a-planted-directory +
  EFBIG RLIMIT 注入）、assemble 锁化、guard-frontend-dist（4 钉）——本机复跑
  全绿，无新增缺口。
- 新增 #505（night/pr-json-header-precedence）：json() 头合并**优先级**钉
  （轮 9 咨询项——caller content-type 覆盖默认；spread 反向即红）。
  弃置的 spawnOwned 钉说明：assertSupervised 走真实控制器子进程，无测试缝，
  记为设计级（不给缝造假）。
- 新增 #506（night/pr-guard-query-and-root-refs）：#444 guard 引用过滤的两
  个承重半边——skip 表（remote/data/hash/root 不落盘解析 + 相对引用极性对照）
  与查询串剥离（Next 哈希 URL）（6 pass）。
