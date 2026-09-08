# N1 shell-core 审计（2026-09-08 夜间烧 · 持续更新）

- 范围：`apps/desktop/shell-core/**`（基线 `origin/master` = `2e5772b`，含 PR #252–#258 与
  NUI/review-loop 波次）；验证驱动仅触及 `apps/desktop/scripts/**` 的 shell 相关测试。
- 方法：两个只读深读代理分别覆盖 logs.rs/ziparch.rs 与 protocol.rs/handshake.rs/ownership.rs
  （合计 107 条原始发现），组长对全部 BUG 级候选做了行级复核后才收录；红绿判别（C 项）
  与边界补测（D 项）结果随切片回填。
- 状态语义：BUG = 会误伤用户的真实缺陷（附复现）；RISK = 真实窗口/健壮性弱点；
  GAP = 测试缺口候选；DOC = 文档-代码不一致或缺文档。全部行号以基线 `2e5772b` 为准。

## 1. 已确认 BUG（按切片修复，各自独立 commit/PR）

| ID | 位置 | 缺陷 | 复现/判别 | 状态 |
| --- | --- | --- | --- | --- |
| BUG-1 | `ziparch.rs:91,106` | `name.len() as u16` 静默截断：>65,535 字节成员名产出结构性损坏的归档；模块文档（:60）承诺"panic 而非静默损坏" | `add_file(&"a".repeat(70_000), …)` → name_len 字段 = 70000 mod 65536，所有读者误析构 | 修复中 |
| BUG-2 | `logs.rs:1003-1008` | confirmed-overwrite 落盘先 `remove_file(destination)` 再 `rename`：rename 失败 → 用户原归档已删、`.pending` 孤儿（该分支无清理；hard_link 分支有） | 目标父目录 chmod 500 → rename EACCES；旧代码留 `.pending` 孤儿 | 修复中 |
| BUG-3 | `logs.rs:226-228` | 轮转 shift 前先删最旧世代 `.keep`：后续任一步失败不可回滚，真实历史永久丢失；既有测试反而把该损失固化为预期 | 在 `.keep` 位失败（如 planted 目录）→ `.5` 应幸存而现状被毁 | 修复中 |
| BUG-4 | `logs.rs:890` | 成员读取 `fs::read` 无上界：collect 时 ≤64 MiB、读取前涨到数 GiB 的日志会被整读进内存后才被拒，可 OOM 壳进程 | 构造性缺陷；修复 = open 后复查 metadata + `take(EXPORT_MAX_FILE_BYTES)` | 修复中 |
| BUG-5 | `logs.rs:735` | 子目录 `read_dir` 失败沿 `?` 中止整个导出，与 :886-889 对文件声明的 skip-and-report 策略矛盾 | 一个锁死/不可读子目录 → 整次导出失败 | 修复中 |
| BUG-6 | `ownership.rs:205-211,227-237` | Unix：helper 自行退出后 `stop()` 提前返回、`Drop` 的 `alive()` 为假 → 组信号永不发出，plan B 的 node 等 helper 后代孤儿化（无 KILL_ON_JOB_CLOSE 等价物）；SIGTERM-忽略型后代在子进程先死时同样漏杀 | helper 立即退出 + 忽略 SIGTERM 的孙进程 → drop 后孙进程仍存活 | 待修 |
| BUG-7 | `protocol.rs:81` | READY 的 `pid.as_u64() as u32` 回绕：`2^32+真实pid` 可通过 PID 归属校验 | 构造 pid=2^32+child_pid → TokenMismatch 之外的校验被绕过 | 待修 |
| BUG-8 | `protocol.rs:217-219,304` + `logs.rs:515` | `mark_stopped` 把不可解析 journal 静默重置为 `"{}"`，随后新 sweep 会在宽限后删除这份被毁的取证记录；且 sweep 契约（`protocol.rs:247-248`）承诺失败上 stderr，唯一调用点 `let _ =` 丢弃 | 植入损坏 owner.json → stop → sweep 删除 | 待修 |
| BUG-9 | `ownership.rs:217`（Windows 腿） | `TerminateJobObject` 结果 `let _ =` 丢弃后落入无超时 `child.wait()`：升级杀失败 → 潜在无限挂起 | Windows 原生；**NOT RUN**（Linux 不可证） | 代码修复，验证 NOT RUN |

## 2. RISK（记录，按优先级排队；部分随切片顺带收口）

- `protocol.rs:78,123` token 比较非常量时间（helper 本就持 token，风险受控，未文档化）。
- `protocol.rs:43,95` `u16::from_str` 接受前导 `+`，`http://127.0.0.1:+80` 通过回环门。
- `handshake.rs:148` / `protocol.rs:117` READY 行与 journal 读取无长度上界（恶意 helper 可撑内存）。
- `handshake.rs:307` 健康门只看 HTTP 200 不验身份，helper 死后端口被抢可过门。
- `handshake.rs:295` get_status 全量缓冲响应。
- `protocol.rs:150,144-153` starttime 锚点缺失即跳过（fail-open），且仅 cfg(unix)；Windows 腿无创建时间等价物。
- `ownership.rs:96-103` fork→prctl 窗口；`163-173` setsid 后代逃逸组（模块文档未声明该例外）；`261-280` Windows 线程快照 PID 复用窗口。
- `logs.rs:929` manifest 序列化 unwrap；`865,952` 全归档内存缓冲 + 位转 CRC；`1004-1007` 目标父链验证后内核重解析窗口；`662-674` Windows ADS 目标名未拒；`441` rotate_if_large 尺寸-重命名交换窗；`446,464` 熔断态每行重开句柄；`951,958-959` 并发导出同目标时 `.pending` 竞争误报 PendingIsSymlink。
- `handshake.rs:257`（及 `src-tauri/main.rs:242-244`）abort/stop 失败被 `.ok().flatten()` 吞掉。
- `protocol.rs:342-343,355,236,361-366` pub API 可达 panic（new_token/BCrypt/write_journal_atomic/unix_now）。

## 3. GAP（测试缺口 → D 项补测清单）

- 边界相等性：轮转阈值-1；`EXPORT_MAX_FILE_BYTES` **恰好** 64 MiB 应纳入；总字节上限**恰好等于**应纳入（现在只测 `>`）；65,534 成员 + manifest = u16::MAX 的端到端。
- 轮转：keep=1 退化；回滚账本中被重占槽位的 unwind 步失败；`.rotating` 残留清扫植入；跨会话清扫并发。
- 导出：missing logs dir 的 Io 变体；`.pending` 为目录；成员缩小（shrink）场景；覆盖式 rename 失败（BUG-2 的判别测试）；非 UTF-8 成员名 lossy 塌缩重复。
- ziparch：成员名 >65,535 字节 panic（BUG-1 判别）；`dos_date_time` 上界 2108 钳制。
- 协议/所有权：stop 幂等/并发；ReadyTimeout 路径；stop-before-GO；端到端畸形 READY/journal 篡改矩阵；READY pid 既非子进程又非 Job 成员的负例；Linux `pid_starttime` 校验（protocol.rs:144-153 零覆盖）；ready_timeout 时序；健康重试非 200→迟来 200；超大 stdout 洪泛；`RunLog::create` 真正触发 sweep 的接线测试；sweep read_dir 错误路径。
- 错误类型：`SpawnError`/`OwnershipError`/`VerifyError` 均无 `Display`/`std::error::Error` 实现（无法经 `?` 传播）。

## 4. DOC（文档-代码不一致；随切片修正）

- `ziparch.rs:69` "writer saturates today" 已失实（现 panic，有测试钉住）；`:60` panic 承诺被 BUG-1 打破；`:47` "Streaming" 实为全内存；`:22-23` 只提 1980 下钳、代码另有 2107 上钳（`ziparch.rs:37`）。
- `logs.rs:69` 同 "saturates" 失实；`:77-78` 总量上限"后续成员被跳过"过度承诺（:879-885 只跳过会越限的成员，后续更小者仍纳入）；`:406-407` RunLog"仅身份字段"在 record() 层无强制；`:745-746` "硬链接逃逸被跳过"不实（canonicalize 解析到根内名，硬链接文件会被归档）；`:990-992` 承诺的 pending 清理在 overwrite 分支不存在（BUG-2）。
- `protocol.rs:240-261` sweep 契约挂在常量 rustdoc 上且 stderr 承诺未实现（BUG-8）；`:283` 分隔符检查为死代码；`:148-150` journal "anchors PID identity" 对缺失字段静默跳过。
- `handshake.rs:29` ready_timeout 文档写 ADR 预算 ≤15s、`stub()` 实为 20s。
- 大量 pub 项缺文档（logs.rs:61,104,498,566-625；protocol.rs:8-13,16-24,27-37,159-162,203-211,331-366；handshake.rs:35-43,65-71,262-269；ownership.rs:59-77,152-154,175-177,307-313）。

## 5. C 项红绿判别（每集成套件 ≥1 抽查；结果回填）

| 套件 | 被还原的生产修复 | 预期 | 实测 |
| --- | --- | --- | --- |
| startup_protocol | `process_group(0)`（9989d9c 的 spawn 期组所有权） | `stop_kills_descendants_of_a_child_that_never_joins_its_own_group` 转红 | 待跑 |
| picker_policy | `reject_intermediate_links`（5aad7bc） | 链接祖先用例转红 | 待跑 |
| delivery_contract | capabilities/default.json 加 remote 键（负向检查） | 契约测试转红 | 待跑 |
| log_export | 目的地校验（最终成分符号链接 / 用户数据根包含） | 对应用例转红 | 待跑 |
| log_rotation | shift 回滚 / 阈值逻辑 | 对应用例转红 | 待跑 |

## 6. 切片与 PR 台账（B 项；每切片独立 commit/PR）

| 切片 | 内容 | commit/PR | cargo 证据 |
| --- | --- | --- | --- |
| ziparch | BUG-1 + 名称上界 panic 测试 + dos_date 上钳测试 | 待 | 待 |
| logs-export | BUG-2/4/5 + 相应判别与边界测试 | 待 | 待 |
| logs-rotation | BUG-3 垃圾桶-回滚设计 + 重写固化坏行为的测试 + 阈值-1 边界 | 待 | 待 |
| startup-ownership | BUG-6/7 + 并发 stop/幂等测试 + pid_starttime 单测 | 待 | 待 |
| protocol-sweep | BUG-8 stderr 契约 + 接线测试 | 待 | 待 |

## 7. NOT RUN（本环境不可证）

- BUG-9 与一切 Windows 腿（Job Object/TerminateJobObject/ADS/创建时间锚点/MSI）——Linux 沙箱无 Windows 实机。
- 真实 WebView2、真实供应商、付费调用：沿项目既有边界。
- `handshake.rs:307` 抢占端口的健康门竞态：需要受控端口竞争环境，Linux 上只能单元级模拟。

## 8. E 项子代理审查轮次

- 轮 1：待派（契约正确性 / 测试恒真 / 修复是否改行为；结论须落 file:line）。
- 轮 2：待派。
- 轮 3：待派。
