# N2 Sidecar / 壳内 Web 审计报告（2026-09-08 夜）

- 基线：`origin/master` = `0534def`（含 #270/#271/#273/#274）；工作树
  `night/n2-platform-burn-20260908`。
- 范围：`apps/desktop/sidecar/**`、`apps/desktop/scripts/**`、plan-B 壳内 Web 非 native 面；
  shell-core 仅在交互点核对（留给 N1）。
- 硬边界：未触碰 `apps/desktop/native/**`、`apps/desktop/native-tests/**`。
- 状态标记：✅=已有代码+测试覆盖；⚠️=缺陷（本轮开 PR）；ℹ️=记录在案的残余/设计决定。

## 1. relay 字节管道（`sidecar/mangaflow_desktop_helper.py`）

> 行号基线：master `37055ab`（#288 已含、#290 待合并——合并后 `_serve_relay` 区块行号会再偏移）。

| 项 | 位置 | 结论 |
| --- | --- | --- |
| 连接超时与管道隔离 | `_serve_relay` L180-266；`create_connection(timeout=5)` 后 `upstream.settimeout(None)`（L229；PR #252，已合并） | ✅ 连接超时只约束 CONNECT；双向 pump 无读死限。客户端侧由 timeout-mode listener accept 出 blocking socket，无需显式设置。 |
| 慢响应 / keep-alive | `test_sidecar_relay.py::test_relay_delivers_a_response_slower_than_the_connect_timeout`（上游 TTFB 5.6s）、`::test_relay_serves_a_second_request_after_a_long_keep_alive_gap`（空闲 5.5s 后复用） | ✅ 红/绿已验证（旧代码必失败）。 |
| 半关闭语义 | `_pipe` finally `dst.shutdown(SHUT_WR)`（L239-244） | ✅ 半关闭=向对端转发 EOF；两个方向对称。表驱动红绿：`test_relay_pipe_semantics_table[client-half-close / upstream-fin]`（EOF 吞噬变异必失败，#278/#292）。 |
| 单连接故障隔离 | `_pipe` `except OSError: pass`（L239-240） + accept 循环 `except OSError: return`（L205-206） | ✅ 单连接 RST/ECONNRESET 只死 pump 线程，accept 循环存活。红绿：`test_relay_error_path_table[client-rst-isolation]`（SO_LINGER RST 后新连接可服务，#278/#292）。 |
| 上游拒连（错误路径） | `create_connection` 失败 → `client.close()` + 释放槽位 + `continue`（L216-219） | ✅ uvicorn 未起时 node 代理请求快速失败（FIN/RST）。红绿：`test_relay_error_path_table[dead-upstream-recovery]`（API 端口复话后同一 relay 恢复服务，#278/#292）。 |
| 线程模型 | E3 双中继架构（announced web 端口→node 临时端口；39443→API），每 relay 独立 `_RelayLimiter`（各 128，总 256）+ 每连接 3 线程；溢出快速关闭。announced 端口由 helper 于 node spawn 前持有（Windows EXCLUSIVEADDRUSE）。红绿：`test_relay_caps_concurrent_connections` + `test_sidecar_mid_session_...`（死后 announced 端口快速关闭、无 HTTP 字节——兼作 Windows co-bind 劫持绊线）。PR #288/#333。 |

- **续跑复核（2026-09-09 深夜，master 仍为 `053b9e6`，无新提交）**：按轮首指令同步后
  （`reset --hard origin/master && clean -fd`，审计随 95ce11a 保留），串行证据复跑全绿：
  runner **17 passed**（28.3s）、shell-core `cargo test` **9/9 套件 ok**。#333 待 lead 合并。
  本轮无新增缺陷：E3/E4 双中继代码经第 5 轮对抗审查零发现，表驱动矩阵与降级形态断言均已钉死。

## 2. bind / REUSE / EXCLUSIVE（`_bind_loopback` L87-104、`_bind_relay` L116-152）

| 平台/项 | 语义 | 状态 |
| --- | --- | --- |
| API 动态端口（`_bind_loopback` L87-104） | Unix `SO_REUSEADDR`；Windows `SO_EXCLUSIVEADDRUSE`（#256） | ✅ 端口 0 无冲突面；Windows 防 SO_REUSEADDR 抢绑。 |
| relay 固定端口 39443（`_bind_relay` L141-177） | Unix `SO_REUSEADDR`（跨自身 TIME_WAIT 重绑，#253）；Windows `SO_EXCLUSIVEADDRUSE`（#256，保留 TIME_WAIT 残余并记录于 docstring） | ✅ 单元红绿：`test_sidecar_relay_bind.py::test_relay_bind_policy_table[own-time-wait]`（旧代码失败，#253/#292）、`[…][foreign-live-listener / foreign-reuseaddr-listener]`（前后皆过，钉死对外 fail-closed 含 SO_REUSEADDR 持有者新行，#292）。 |
| web 端口 claim-bind-close | `_spawn_web_server`（L534 起，claim 在 L560 附近） | ℹ️ bind(0)→close→node bind 的极小 TOCTOU，plan-B 设计稿 §6 记录在案；#271 的 `_await_web_server` 已把"宣布未持有端口"的最坏后果关闭（见 §4）。 |
| 已知缺陷 | **web boot 失败路径不释放 relay**：`_spawn_web_server` 仅在 node spawn OSError 时 `relay.close()`（L540）；`_await_web_server` 失败（L383 调用，L554-603 实现）reap node 后返回 `(None, None)`，relay 仍绑 39443 且 pump 线程存活整会话；`_run_app` finally（L418-430）无 relay 句柄。 | ⚠️ **PR-B**：`_spawn_web_server` 返回 relay 句柄；`_await_web_server` 失败即关闭；e2e 断言降级会话 39443 不再监听（红/绿）。 |

## 3. loopback 不变量

- READY/api_origin：`_run_app` L385-396 只发布 `127.0.0.1:<dynamic>`；shell 侧 `is_loopback_origin`（shell-core protocol.rs）双向校验 api_origin/web_origin。✅
- web 服务器 bind：helper 以 `HOSTNAME=127.0.0.1` 启 node（L508），e2e `test_sidecar_plan_b_web_server_loop` 在存活期探测非回环本地 IPv4 拒连（#266）。✅
- D5 静态服务器：`verify-static-origin.mjs` L116 仅绑 `127.0.0.1:4173`（围栏在 L97-105；根路径推导已改 `fileURLToPath`，L18-24，PR #289）。✅
- helper/壳不读非回环地址；shell-core 交付契约测试钉 CSP `connect-src 'self' http://127.0.0.1:*`。✅

## 4. READY / journal（`_read_context` L63、`_write_journal` L78、`_run_app` L385-403）

- 身份字段白名单（token/pid/port/origin/state），journal 原子写（pending+rename，L83-84）；伪造/链接/跨目录拒绝（L63-76）。✅（e2e 断言 journal 键集合，`test_sidecar_e2e.py` L426、L527-530）
- **#271（新）**：`_await_web_server`（L554-603）双证据（poll 存活 + connect 应答 + 应答后复验 poll）才允许发布 `web_origin`；node 死于 boot 或 10s 不应答 → reap node、无 web_origin、会话降级为静态导出。✅（e2e `test_sidecar_dead_web_dist_fails_closed_without_web_origin` L547）
- 会话启动清扫 stale runtime 目录（shell-core protocol.rs `sweep_runtime_dirs`，终态+24h 宽限+链接/包含拒绝）。✅（N1 面核对，无异议）
- ⚠️ 关联缺陷见 §2：降级会话应同时释放 relay 端口（PR-B 一并修 + 断言）。

## 5. e2e 超时（`scripts/test_sidecar_e2e.py`）

| 阶段 | 超时 | 状态 |
| --- | --- | --- |
| READY 行 | `_read_ready_line` 守护线程硬截止 15s（L124-148，#266；修掉"readline 永久阻塞使断言永不触发"） | ✅ |
| API 健康 | `wait_health` 20s 轮询（L196-207）；Rust 侧 health_timeout 10s | ✅ |
| web boot | `_await_web_server` 10s（helper L551）+ e2e 侧 node 就绪轮询 | ✅ |
| 停机 | POSIX killpg SIGTERM 15s→SIGKILL；Windows stdin-EOF 20s→kill（L209-240）；OwnedTree drop 兜底 3s | ✅ |
| job 等待 | `_wait_job` 120s（生成闭环） | ℹ️ 依赖 fake channel 确定性；无外呼。 |

## 6. npm shim（`scripts/build-web-standalone.py` L32-37）

- `NPM = "npm.cmd" if os.name == "nt" else "npm"`（#262/#256 双修后合流为单常量）。✅
- 构建时校验 routes-manifest 烤入 `127.0.0.1:39443`（L48-56），错目标 build 响亮失败。✅
- `run-sidecar-e2e.sh` 缺 bundle 时自动构建 → Linux 路径恢复可用（本会话实测通过，见 §9）。

## 7. static export 路径围栏（`scripts/verify-static-origin.mjs` L88-107）

- `resolve(root, "." + decodeURIComponent(path))` + `startsWith(root + sep)` 包含检查，`curl --path-asis` 式 `..`/`%2e%2e` 逃逸 → 404（commit `6a97625`；fence 断言 L103-105）。✅
- ℹ️ 测试缺口：围栏本身无可执行验证（D5 只走正常路径）→ **PR-C**：D5 自测追加编码遍历请求断言 404（红/绿：临时撤掉围栏必须失败）。
- Tauri 侧等价围栏（`shell-core/tests/delivery_contract.rs` CSP 契约 + 无 remote IPC 能力契约，#254）✅。

## 8. CORS / CSP 与 helper 交互

- CORS：`apps/api/app/main.py` L130-134 由 `WEB_ORIGIN` 派生 {origin, localhost↔127.0.0.1 变体}；helper 默认 `http://tauri.localhost`（`--web-origin`，L611-615，默认值 L613）。plan-B 同源 rewrites 使浏览器无跨域请求（设计稿 §3）——CORS 形同备用。✅
- TrustedHost：`localhost,127.0.0.1`（L156-163）；Next 代理 Host=127.0.0.1:39443 去端口后命中。✅（plan-B e2e 实证）
- CSP：tauri.conf L13 约束本地文档（静态导出形态）；`script-src 'unsafe-inline'` 为 Next 内联引导的必要债务，plan-B 由 `apps/web/next.config.ts headers()` 提供等价策略（同样的 unsafe-inline 债务）。ℹ️ 移除需 Next nonce/hash 改造，记录不动。
- withGlobalTauri：注入进所有文档（含 plan-B 外部源），无 `remote` context 能力时 invoke 一律拒绝（tauri 2.11.5 ACL），交付契约测试钉死（#254）。✅

## 9. 本轮产出与验证记录

| 产出 | 内容 | 验证 |
| --- | --- | --- |
| PR-A #278（已合并）（relay 子系统） | `test_sidecar_relay.py` 表驱动补：客户端半关闭后响应仍达、上游 FIN 透传 EOF、上游拒连后 relay 存活、RST 后 accept 循环存活 | Linux pytest RUN；红/绿抽查见 §10 |
| PR-B #279（已合并）（standalone/planB 子系统） | helper：`_spawn_web_server` 返回 relay 句柄，boot 失败即释放；`_run_app` finally 兜底关闭。e2e：降级会话断言 39443 已关闭、成功会话断言 39443 在听 | Linux pytest/e2e RUN（真实 standalone bundle）；Windows NOT RUN |
| PR-C #281（已合并）（static-export 子系统） | D5 自测：编码路径遍历请求必须 404（钉死 6a97625 围栏） | Linux node RUN（完整 D5）；Windows NOT RUN |
| PR-D #282（已合并）复审修订（relay 测试润色） | 注释纠偏（FIN/RST、linger 语义）、>100 列换行、read_response 校验 Content-Length、half-close tail 断言 | Linux pytest RUN（6 passed） |
| PR-E #283（已合并）复审修订（helper/e2e 润色） | relay 释放块 hoist 出 `if node is not None:` 并注明不变式；finally 置 relay=None；e2e 两处注释/报错措辞 | Linux pytest RUN（e2e 3 passed） |
| PR-F #284（已合并）复审修订（mjs 润色） | 自测定时器 settle 后 clearTimeout | node --check RUN |
| PR-G #285（已合并）（scripts 子系统） | run-sidecar-e2e.sh 纳入 relay/bind 回归套件 | Linux pytest RUN |
| PR-H #288（relay 加固） | 并发连接上限 128：溢出快速关闭、双向 pump 结束才释放槽位、既有连接不受影响 | Linux pytest RUN（7 passed；旁路变异必失败） |
| PR-I #289（scripts 润色） | D5 根路径改 `fileURLToPath`（win32 可移植性） | Linux D5 RUN（PASS）；win32 NOT RUN |
| PR-K #292（relay 表驱动整合） | 管道生命周期表（4 参数：慢响应/keep-alive/半关闭/上游 FIN）+ 错误路径表（2 参数：死上游恢复/RST 隔离）+ bind 策略表（3 参数：TIME_WAIT/外占/REUSEADDR 外占新行） | Linux pytest RUN（表 10 passed，runner 14 passed；两项变异各自精确命中目标行） |
| PR-J #291（web 检测） | plan-B web 服务器 mid-session 退出的检测与法证日志（250ms 轮询，只记日志不重启，ADR §4.5 范围内） | Linux e2e RUN（runner 13 passed；禁用 watcher 的变异必失败） |
| PR-M #334（构建溯源） | bundle 盖 build-info.json 溯源戳（source_commit/apps_web_tree/relay_origin）；e2e 断言 apps/web 树哈希一致（分支无关），陈旧构建强制重建 | Linux RUN（无戳红/重建绿；e2e 5 passed） |
| PR #290（已合并） | #288 评审返工：B023 参数传递、immortal accept loop、确定性关闭 | 变异/探针验证 |
| PR #291（已合并） | plan-B web mid-session 退出检测（ADR §4.5 检测范围，仅日志不重启） | Linux e2e RUN（禁用 watcher 变异必失败） |
| PR #292（已合并） | relay/bind 表驱动矩阵整合（9 参数） | Linux pytest RUN（变异精确命中目标行） |
| PR #298（已合并） | D5 静态端口占用报因（EADDRINUSE + 处置提示，exit 1） | Linux RUN（占用 4173 红/绿） |
| PR #305（已合并） | 第 1 轮 MAJOR 返工：构造失败槽位泄漏（finally 兜底关闭+释放） | Linux pytest RUN（红/绿） |
| PR #319（待 lead） | #306 评审返工：告警断言注释精确化 + stderr 日志字面量去重 | Linux e2e RUN（4 passed） |
| Issue #299 | plan-B 壳工具页不可达（设计决策项，三候选方案，未擅改） | 证据链完整（§12b） |
| PR #407（已合并） | junction 7 测试非 MSYS 宿主干净跳过（原 7 failed） | Linux RUN（7 skipped） |
| PR #408（已合并） | traverse-only api_root 回退分支覆盖（0o111 探针） | Linux RUN（9 passed） |
| PR #414（待 lead） | assemble 跨 pid 崩溃残留清扫（.old-*/.tmp-*，85MB+/次） | Linux RUN（红/绿；getpid 补丁移除后不再虚过） |
| PR #415（待 lead） | dist 锁头文档范围修正（writer-writer，读者窗口残余记录） | bash -n + 锁套件 |
| Issue #300 | 双形态 CSP unsafe-inline 债务 nonce/hash 改造跟踪 | 无既有跟踪项 |
| 审计文档 | 本文件（随各轮审查增量更新；行号基线随合并轮次刷新） | 子代理抽查 30+ 引用（第 3 轮） |

## 12. 子代理审查记录（≥3 轮，结论落到文件/行）

- **第 1 轮（3 并行）**：#278/#279/#281 全部 APPROVE。行级产出：#278 —— read_response 不校验
  Content-Length（L123-137）、half-close 无 tail 断言、死上游注释把 RST 误写为 EOF（L285）、
  SO_LINGER 注释错误（L325）、两处 >100 列（L97/L209）；#279 —— relay 释放块嵌套易误读
  （L375-390）、finally 未置 None（L443）、e2e 探针注释过度声明（L526-538）、释放轮询报错未列
  外因（L593-606）；#281 —— 自测 setTimeout 未清理（L136）；审计文档 —— 7 处行号漂移
  （§1/§2/§3/§4/§8）+ §11 缺 mjs win32 可移植性残余。均已返工：PR #282/#283/#284 + 审计增量。
- **第 2 轮（2 并行）**：四个复审 PR 全部 APPROVE。逐条确认第 1 轮 findings 已修（含变异证明：
  截断 body 的 read_response 旧代码通过、新代码失败）；新 findings 仅 NIT 级（断言风格不统一、
  settleOnce 命名、runner 注释未写 sleep 开销），断言风格已在本轮统一提交（b0598b9）。
- **第 3 轮**：最终验收审查（1 子代理）——四个复审 PR 均已被 lead 合并（#282/#283/#284/#285），逐 PR 判定 MERGE_SAFE、审计报告判定 ACCURATE（12+ 处引用复核无误）；遗留一个孤儿提交（b0598b9，#282 合并后推送）→ 已开 #286 落地；审计表已标注 PR 号。无 BLOCKER/MAJOR。
- **续跑（2026-09-09，master 仍为 `37055ab`）**：#290（第 4 轮返工）待 lead 合并，master 暂未
  含其修正。在同步后的工作树上复跑全部串行证据：runner **12 passed**（24.1s：e2e 3 + relay 7
  + bind 2）、shell-core `cargo test` **9/9 套件 ok**；`package-sidecar.sh`（#274 后形态，
  私有 mktemp scratch + trap 清理）复核无新增问题。
- **0909 夜续跑（master `2d7f9e4`）**：#290/#291/#292/#298 全部合并；新增 #305（槽位泄漏
  MAJOR 返工，已合并）、#306（无虚假告警断言，已合并）、#319（评审返工，待 lead）。
  串行证据：runner **14 passed**（e2e 4 + relay 7 + bind 3）、cargo 9/9、D5 PASS。
- **第 4 轮（本轮新增产品代码 #288 的对抗审查，1 子代理）**：REQUEST_CHANGES——MAJOR：
  `_pump` 经由 accept 循环共享 cell 读取 `client`/`upstream`（B023 晚绑定，跨线程延迟读取
  理论上可泵入外来配对）；MINOR：外层 spawn 的 RuntimeError 会杀死 accept 循环、内层失败
  仅靠引用计数关套接字、饱和日志逐行无上限；测试 0.3s 睡眠是假阴性源。全部返工：
  套接字改为线程参数传递（B023 清零）、spawn 双层防护 + 确定性关闭、饱和日志 10s 限频、
  释放槽位改 5s 有界重试（FIN/RST 均视为未释放）→ PR #290。
- **0909 夜第 1 轮（#288 修复的对抗审查，REQUEST_CHANGES，1 子代理）**：MAJOR（探针证明）——
  #290 的 RuntimeError 防护未覆盖 `Thread(...)` 构造（MemoryError：内层槽位泄漏致容量递减/
  外层 accept 循环死亡）；MINOR：饱和日志逐行、注释过度声明。→ 返工 = PR #305（finally 兜底 +
  构造入守卫 + 限频日志 + 有界重试测试），已合并。
- **0909 夜第 2 轮（#298 审查，APPROVE，1 子代理）**：4 项行级——post-listen error 监听器吞错
  （master 语义回退）、exit 前未 reap 已 GO 的 helper、EACCES 误导性提示、非 ASCII 破折号。
  → 已返工（#298 增量提交 423318a）。
- **0909 夜第 3 轮（#306 审查，APPROVE，1 子代理）**：finding 1 告警断言注释高声明确定性；
  finding 3 文件名字面量重复。→ 已返工 = PR #319。
- **0909 深夜行级复审（E3-F2/F3 两提交，5d7a6a8/91f203f，无新缺陷）**：guarded closes 与
  claim-failure 路径形状一致；relay 线程 start 失败路径 terminate→wait→kill→wait（收尸）→
  双套接字关闭→降级，首线程已启动时第二线程失败亦正确（首线程随 close 退出）；zombie 收尸
  与 WebServer.close 对称。逐项核实，无需返工。
- **0909 夜第 4 轮（#305 状态复核，1 子代理）**：确认修复后套件 10/10、槽位清零、B023 清零；
  两参数构造失败回归的辨别力复核。
- **0909 夜第 5 轮（E3/E4 双中继新架构对抗审查 + #333 评审，APPROVE，1 子代理）**：复核
  helper 持有 announced 端口、双 byte-pipe、boot 双 poll、N4 settle-race 修复——无缺陷；
  #333 3 项 NIT（sendall 包错误处理、断言消息含 closed 态、子句顺序注释）→ 已返工（f4cce63）。
  另：限制器为每 relay 实例（announced/39443 各 128，总 256）——精确化记录于 §1。
- **0909 深夜稳定性与扩面（无子代理）**：
  - 连续 3 次 runner 全套（e2e 4 + relay 9 + bind 3）：**17/17 × 3 全绿**，耗时 28.57/28.67/28.91s
    （抖动 <1.2%）——e2e 稳定性证据；
  - 扩面审查 #312 新增 env 卫生测试（importlib 加载 helper、四名剥离 + PATH 伴行断言）——
    无恒真隐患；
  - 扩面审查 `scripts/start-native.ps1`（Windows 专用，本沙箱 NOT RUN）：fail-closed 构建、
    SHA256 拷贝校验、`$env:` 会话残留为既有 dev 工具形态——无缺陷。

## 10. 红/绿判别力抽查记录

- PR-A 每用例：以 `git stash` 撤产品代码跑旧实现必须失败（半关闭/透传/RST 用例针对 `_pipe` 的 OSError 吞噬与 shutdown 语义）。
- PR-B：撤销"失败即关 relay"补丁 → 39443 断言必须失败。
- PR-C：临时移除 mjs 包含检查 → 自测断言必须失败。

## 11. 残余/不做（记录）

- ~~relay 每连接 2 线程无上限~~ → 已修（#288 上限 128 + #290/#305 槽位泄漏防护，见 §1）；web 端口 bind-close TOCTOU（§2）；CSP unsafe-inline（§8）；
  `verify-static-origin.mjs` 仅测试用途；sequential-session e2e 依赖关闭时序，弱判别力，不采。
- ~~`verify-static-origin.mjs` 的 `new URL(..).pathname` win32 路径缺陷~~ → 已修（PR #289，
  `fileURLToPath`，POSIX 输出不变；win32 实跑仍 NOT RUN）。
- Windows 实机（WebView2/Job/EXCLUSIVEADDRUSE/安装器链）本沙箱不可达，全部 NOT RUN。

## 12b. 追加发现（web 相关，待 lead 决策，不擅改）

- **plan-B 形态下壳工具页不可达**：`shell/shell-tools.html`（日志导出/本地文件选择/回读）
  仅随静态导出拷入 `dist/frontend/`，经 `tauri.localhost/shell-tools.html`（local context，
  IPC 放行）可达；plan-B 的 WebView 加载 `http://127.0.0.1:<web>`（remote context，#254 已证
  IPC 拒绝），且 Next 应用无任何入口链接壳工具页 ⇒ plan-B 用户没有任何路径触发
  `desktop_export_logs` / `desktop_pick_file` / `desktop_read_picked_file`。静态导出形态
  （legacy）有此能力 ⇒ 相对功能回退。候选方案（均为产品设计决策，不擅动）：壳级菜单/托盘
  入口打开壳工具页、Next 应用加壳链接、或接受缺失直至原生客户端替代。**未修，仅上报。**



## 15. 20260911 夜（新一轮烧录，基线 `fe22969`）

- **master 推进**：#293–#306/#319/#330/#331/#334 全部合并；scope 内新落地：
  **#299 已解决**（src-tauri 新增 local-context `shell-tools` 窗口 + 菜单入口，capabilities
  windows 扩为 [main, shell-tools]——仍无 remote context，#254 契约不变）；`desktop_read_picked_file`
  转 async（20MiB 读+base64 不再冻结 UI 线程）；`DIALOG_OPEN` 单例对话框（#316）；
  helper `_validate_api_root`（#314）+ `_apply_app_environment` 强制 DISABLE_DOTENV（#313）；
  D5 lstat 符号链接围栏 + in-root symlink 自测（#317）；negative-pid kill 收尾加固。
- **本轮行级复审（上述新代码，无 BLOCKER）**：capabilities 契约不变；fail-closed journal
  路径；menu 失败走 stop_helper 记账；`_validate_api_root` 的 fake_channel 反遮蔽检查为
  字节精确——**Windows 大小写不敏感导入可被 `Fake_Channel.py` 遮蔽** → 本轮 PR #336 修复
  （lowercase 扫描 + 大小写变体测试行，红/绿验证）。
- **串行证据**：runner **17 passed**（4a0ebe6）；env/api-root **6 passed**（含大小写变体行）；
  e2e **5 passed**（溯源戳重建后）。#334 的陈旧 bundle 断言在 apps/web 变更后实证生效
  （stale tree hash → 响亮失败 → 重建恢复）。
- **PR**：#336（case-insensitive fake_channel shadow scan，OPEN）。
- **Issue**：#299 已由壳工具窗口落地（本节记录）；#300 CSP 债务仍开放。

## 13. 完成审计（final sweep，master = `f6e4687`）

- **同步**：`origin/master` 自本轮起点推进 `a52fca9` → `f6e4687`（#286 合入 + N1 final
  audit #287：rotation staging 安全、ownership 已收尸后仅组信号——`signal_group_only`
  防 pid 复用误伤；与本文所有声明无冲突，scope 文件零交集）。
- **最终证据（全部 RUN，Linux，串行）**：
  - `apps/desktop/scripts/run-sidecar-e2e.sh` → **11 passed**（23.7s：e2e 3 + relay 管道 6 + relay bind 2）；
  - shell-core `cargo test` → **9/9 套件 ok**（含 N1 新增用例）；
  - D5 全程 **PASS**（静态导出重建后：握手、围栏 404、origin 注入、直连 CORS 放行 API、渲染）。
  - 续跑追加：runner **13 passed**（27.2s，含新增 mid-session 检测 e2e）；#291 待 lead 合并。
  - 续跑 2：master 前进至 `2d7f9e4`（#290/#291/#292/#298 合并）→ runner **14 passed**（含
    #312 的 node env 卫生测试）；溯源戳后 e2e **5 passed**；陈旧 bundle（无 build-info.json）
    在新断言下响亮失败。#334 待 lead。
  - 0909 续跑：runner **17 passed**（28.0s，E3/E4 双中继架构 + 全部表驱动）；D5 PASS；
    cargo 9/9；#333（死后 announced 端口降级形态钉死）待 lead。
- **遗留 PR**：#286（断言统一）已由 lead 合并；本审计报告随 night 分支持久化
  （`night/n2-platform-burn-20260908`）。
- **收敛判定**：范围内（sidecar / scripts / plan-B 壳内 Web / static-export 围栏）无已知
  未修复 P0/P1；P2 级仅剩 §11 记录的既有债务（CSP unsafe-inline、relay 线程模型、
  web 端口 bind-close TOCTOU、D5 win32 可移植性），均有文档化理由。两夜累计合并
  #252/#253/#254/#259/#270/#278/#279/#281/#282/#283/#284/#285/#286。
- **未完事项**：无。Windows 实机验证保持 NOT RUN，待 D3 复验窗口。

## 14b. 20260911 夜续跑增量（master `37055ab`→`053b9e6`，PR #333/#334/#319 已合并）

- **新 PR #334（已合并）**：web bundle 构建溯源戳（build-info.json：source_commit /
  apps_web_tree / relay_origin）；e2e `_web_dist_dir()` 断言 apps/web 树哈希一致
  （分支无关）——陈旧 bundle 无法静默测试过期 UI。实证：本轮 reset 后陈旧 bundle 在
  新断言下响亮失败（stale tree hash），重建后恢复。
- **新断言（#333，已合并）**：死后 announced 端口降级形态（快速关闭、无 HTTP 字节；
  兼作 Windows co-bind 劫持绊线）。
- **串行证据（全部 RUN）**：runner **17/17**（4a0ebe6，28.7s）；cargo **9/9**；D5 **PASS**
  （含 path fence、in-root symlink fence、symlink 自测——#317）。
- **行级复审（无缺陷）**：E3-F2/F3 两提交（guarded closes、收尸对称）；#312 env 卫生测试
  （无恒真）；`start-native.ps1`（Release host 修正 + SHA256 校验，Windows NOT RUN）。

## 14b-2. 0911 夜第 1 轮审查（1 子代理，文件/行级，REQUEST_CHANGES → 已返工/上报）

- **F1 MAJOR（PR #336，已返工）**：`_validate_api_root` 的 `except OSError` 分支 fail-OPEN
  ——traverse-only（0o111）root 可通过 marker `is_file()` 但 `iterdir()` 抛错，旧字节精确
  检查反而不放行（探针证明）。修复：枚举失败回退字节精确探针（stat 经 +x 可用）。
- **F2 MINOR（已改写）**：平台矩阵注释两半皆误——NTFS `is_file()` 匹配本身大小写不敏感；
  POSIX 导入默认大小写敏感（PYTHONCASEOK 放宽）。扫描的真实价值：严格性 + 大小写敏感
  卷在放宽匹配下的覆盖。
- **F4 MAJOR（相邻，已由 PR #355 上报）**：capability 契约测试顶部枚举可被嵌套
  `capabilities/**` 文件绕过（tauri-build glob 为 `**`）。
- **F7 MINOR（已改写）**：docstring 修正为「wrong-tree heuristic, not hostile-tree
  defense」（launcher 拥有该参数）。
- **无发现区（行级核实）**：DIALOG 原子性与 Drop 顺序；async read 的 State 签名；
  shell-tools local-context 无新 IPC 面（#254 契约仍绿）；`set_menu` 仅 UX 残余；
  marker 集合完备性 vs 声明威胁模型（F7 修正）。
- **跨组上报（N1）**：`picker_policy::readback_fails_closed_when_swapped...` 在
  overlayfs（inode 复用）上间歇失败——#308 的 (st_dev, st_ino) 身份缝在 inode 复用
  文件系统不可靠；需 N1/lead 决策（补 size/ctime 比对或标记 fs 敏感）。本轮实测：
  全套件运行时 FAILED、单跑 PASSED（flaky）。
- **陈旧 bundle 检测实证（0911 深夜追加）**：master 前进（apps/web 变更）后，溯源断言在
  plan-b e2e 响亮失败（stale tree hash）→ 重建后 17/17 恢复——#334 的契约在真实漂移下
  按设计生效。

### 指定领域覆盖核对（审计 Mandate A，逐项）

| Mandate 条目 | 审计节 | 状态 |
| --- | --- | --- |
| relay 字节管道 | §1 | ✅ 超时/半关闭/FIN 透传/死上游/RST 隔离/并发上限，全部有红绿 |
| bind/REUSE/EXCLUSIVE | §2 | ✅ POSIX REUSEADDR（#253）+ Windows EXCLUSIVEADDRUSE（#256），红绿钉死 |
| loopback | §3 | ✅ API/READY/web/D5 四面回环不变量 + 非回环拒连 e2e |
| READY/journal | §4 | ✅ 身份白名单、原子写、#271 属权验证、stale 清扫 |
| e2e 超时 | §5 | ✅ 五阶段超时矩阵（READY 15s/健康 20s/web boot 10s/停机分级/job 120s） |
| npm shim / 构建溯源 | §6 | ✅ 平台解析 + relay 目标烤入校验 + build-info.json 溯源戳（source_commit/apps_web_tree/relay_origin，PR #334）——e2e 断言 apps/web 树哈希一致，陈旧 bundle 无法静默测试过期 UI |
| static export 路径围栏 | §7 | ✅ 包含检查（6a97625）+ D5 自测（#281）+ win32 根路径（#289） |
| CORS/CSP 与 helper 交互 | §8 | ✅ WEB_ORIGIN 派生/TrustedHost/plan-B 同源/withGlobalTauri ACL 契约

## 15. 20260911 深夜续（master 合并我方 #359/#360 后，基线 `fe22969`）

- **复审落地内容（文件/行级，无 BLOCKER）**：`_validate_api_root` 大小写扫描 + fail-closed
  回退（我方 #359 原样合入）、`_apply_app_environment` 强制 env（#313）、api-root marker
  校验 fail-closed journal（#314）、DIALOG_OPEN 单例 + `desktop_read_picked_file` 转 async
  （#316）、D5 lstat 符号链接围栏 + in-root symlink 自测（#317）、`shell-tools` 本地上下文
  窗口 + 菜单（**#299 解决**，capabilities windows=[main, shell-tools]，仍无 remote
  context——#254 契约测试 8/8 仍绿）。
- **Issue #300 Stage 0 落地**：`apps/web/next-config-csp.test.ts` 将 plan-B CSP 响应头
  钉进契约（unsafe-inline 为记录在案的债务；nonce 化留待后续）。
- **串行证据（全部 RUN）**：apps/web 变更后溯源断言按设计响亮失败 → 重建 bundle →
  runner **23 passed**（27.8s）；cargo **116 passed / 0 failed**；交付契约 **8/8**。
- **围栏探针扩展（PR #362，待 lead）**：D5 自测新增 sibling-prefix 探针——脚本自建
  `dist/frontend-sibling-probe/mark.txt` 夹具，raw socket 请求 `/../frontend-sibling-probe/...`，
  断言 404；专捕 `startsWith(root)` 丢 `+ sep` 的围栏回归（该形态下兄弟目录会被 200 伺服，
  变异红/绿已验证）。D5 全程 PASS（三探针 + 握手 + 注入 + 直连 API + 渲染）。
- **跨组上报维持**：picker swap-seam overlayfs flaky（N1）。


## 16. 20260912 夜（本轮，基线 `44e3945`）

- **同步**：`reset --hard origin/master && clean -fd`；审计自 `origin/night/n2-platform-burn-20260909`
  恢复（290 行全量）。
- **第 6 轮审查（1 子代理，文件/行级，APPROVE）**：对 `5a97b6f..44e3945` 新落地 scope 代码
  （dist-build-lock、junction-aware clone、staging swap、env 测试、mjs、main.rs、helper +18）
  ——4 项可操作 findings + 3 NIT，7 个无发现区逐行核实。运行者同时复核：合并态 34/34、
  cargo 126/0。
- **串行证据**：runner **17/17**（4a0ebe6→34/34 于 44e3945 加新测试后）、cargo **126/0**、
  溯源断言按设计触发（apps/web 变更 → 重建恢复）。
- **本轮 PR**：#407（junction 跳过，已合并）、#408（traverse-only 覆盖，已合并）、
  #414（assemble 残留清扫，待 lead）、#415（锁头文档范围，待 lead）。
- **跨组上报维持**：picker swap-seam overlayfs flaky（N1）；
  Issue #300 nonce/hash 阶段未开始（Stage 0 契约钉死已落地）。## 17. 20260912-wknd 续（基线 `a734bdb`，master 已并我方 #563/#565）

- **同步**：`reset --hard origin/master`（a734bdb）；审计自 origin/night/n2-platform-burn-20260912-wknd
  恢复（309 行全量）。
- **账本清理**：关闭被取代的重复 PR #562（journal link-guard 钉死，#560 已并）、#538
  （pid_starttime 锚定，#563 已并）——合并后 master 测试名逐一核对
  （`test_write_journal_refuses_links_and_writes_atomically`、
  `test_pid_starttime_reads_live_anchor_and_is_deterministic`、
  `test_pid_starttime_degrades_to_none_without_proc`）。
- **PR #566（guard fail-open 修复，待 lead）**：`guard-frontend-dist.mjs` 的 href/src 提取
  正则只匹配两种带引号形态；HTML5 无引号属性值（`src=/_next/static/chunks/app.js`）——
  引号剥离式 minifier 变换的合法产物——会令提取集为空，guard 对悬空占位符空泛通过
  （恰是其存在所要拦截的白屏安装包路径）。红/绿钉死：旧正则对无引号悬空页 rc=0
  （"0 local assets, all present"）；修复后拒绝并点名 chunk，同页落盘后放行计数 2。
- **第 8 轮审查（1 子代理，文件/行级，APPROVE + 3 findings，全部处置）**：
  - F1 MINOR（已返工）：无引号分支原用 JS `\s` 且排除引号，而 HTML5 tokenizer 将引号/
    `<`/非 ASCII 空格保留在值内（parse error 但属于值）——截断捕获会解析出"前缀"，
    前缀落盘时 guard 空泛通过而页面白屏。返工：字符类改为 tokenizer 终止集
    `[^\t\n\f >]`，新增前缀判别测试（仅落盘截断前缀必须拒绝 + 真名放行对照）。
  - F2 NIT（记录，不属本 PR）：`existsSync` 对目录返回 true——`src="foo/"` 引号形态
    master 已有同样行为，本 PR 仅新增一种语法；留作独立跟进。
  - F3 NIT（已修）：新测试 docstring "above" 方位词过时。
- **串行证据**：guard 套件 **9 passed**；本轮早前全量（基线 f97c186 树）runner **88 passed**、
  cargo **152/0**。
## 18. 20260912-wknd 续二（基线 `a734bdb`）

- **dist-lock 互锁审计（无缺陷，行级核实）**：`build-frontend-static.sh:23` 的
  `DIST_LOCK="$DESKTOP_ROOT/dist/.build.lock"` 与 `build-web-standalone.py:44` 的
  `DIST_LOCK_PATH = REPO/"apps/desktop/dist"/".build.lock"` 为同一路径——bash flock(1) 与
  python fcntl.flock 在同一 inode 上互锁（#383 所述同环境源前提在 CI/Linux 成立）；
  `dist-build-lock.sh` 的闩锁机制（F-NIT）、超时路径 fd9 关闭、noclobber pid 属主释放逐一复核。
  `build-frontend-static.sh` 的 tee 日志写在锁外（仅追加 static-build.log，非破坏性）；
  破坏段 L195-203 全程持锁。E2E runner 经 build-web-standalone.py 重建（python 锁）。无缺陷。
- **PR #568（D5 READY 读取加固，待 lead）**：`verify-static-origin.mjs` 就绪等待原以
  `stdout.once("data")` + `split("\n")[0]` 解析——READY 行分片到达时会解析出截断前缀并误报
  "bad ready line"；崩溃于 READY 之前的 helper（导入错误）则烧满 20s 且误报 "readiness
  timeout"。改为累积至首个换行 + exit 事件立即以真实退出状态拒绝。综合证据（合成新旧对照
  harness，每读取用独立子进程、监听器随 spawn 同步挂接，与脚本一致）：
  - 分片 READY：旧 `\"MANGAFLOW\"`（截断）→ 新完整行；
  - 静默退出(3)：旧等满计时器（生产 20s）→ 新立即 `helper exited before READY (code 3)`。
  - 全量 D5 两次 PASS（改动后含 rework）：三围栏探针 404、握手、渲染、直连 API 全绿。
- **第 9 轮审查（1 子代理，文件/行级，APPROVE + 4 NIT，2 项返工）**：
  - NIT-1（已返工）：pre-READY 缓冲无上界 + 每块 O(n²) 重扫——加 1 MiB 上界并按超时路径
    杀进程组（helper stdout 在 READY 前为纯协议输出，越界即 rogue import）。
  - NIT-2（已返工）：spawn 失败（无 python3）发 'error' 而非 'exit'，原会以未处理事件裸栈
    崩溃——等待内挂 `error` 监听并纳入 cleanup；修正 "bad interpreter" 注释措辞。
  - NIT-3（记录）：exit-before-data 回调顺序无契约保证，可能把已写 READY 的死亡误述为
    "exited before READY"——语义上握手失败成立且更快，不改。
  - NIT-4（记录，先于本 PR 存在）：Windows 文本管道 `\r\n` 下尾随 `\r` 保留——JSON.parse
    容忍尾随空白，比较均在解析值上进行，良性。
  - 评审同时行级核实：流模式（移除最后 data 监听后仍 flowing、零积压）、超时杀组路径完好、
    diff 仅两 hunk、移动的前缀检查逐字节一致。

## 19. 20260912-wknd 续三（基线 `a734bdb`）

- **PR #570（e2e runner venv bootstrap 自愈，待 lead）**：`run-sidecar-e2e.sh` 原 bootstrap 仅
  检查 `.venv-desktop/bin/python` 存在——pip 安装中途被打断（网络抖动/Ctrl-C）留下的**部分
  venv** 会被此后每次运行接受并在 import 期以费解错误崩溃，且永不自愈；该 runner 是本仓全部
  sidecar 契约的取证路径。改为 `ensure_e2e_venv`：戳文件记录最近**完成**安装的 requirements
  md5，缺失/过期（requirements 变更）即重装；`install ... || return $?` 保证失败绝不写戳。
  主体移入 `BASH_SOURCE` source-guard 使契约可离线测试（pip 步骤为具名函数供测试覆盖）。
  **首版重构引入的 `-r` 形状破坏（第二个 requirements 路径被 pip 解析为需求串）由实跑真
  runner 当场捕获**——恰是覆盖式测试看不见的类别；stub python 离线钉死 `-r f1 -r f2` 形状。
- **串行证据**：新套件 8 passed（含失败 venv 创建传播钉死）；全量经真 runner 路径
  **98 passed in 62.56s**；真 runner 首跑为戳写入后的第二次（无 pip 重装，离线安全）。
- **第 10 轮审查（1 子代理，文件/行级，APPROVE + 5 MINOR + 6 NIT，5 项返工）**：
  - R1/R8（已返工）：venv 创建步骤依赖环境 set -e，而 harness 的 if 上下文会抑制之——
    传播回归无法被现有测试捕获；加 `python3 -m venv ... || return $?` 并新增失败创建者
    shim 测试（ENSURE_RC=3 + 无戳）。
  - R5（已返工）：`${pip_args[@]}` 空数组 + `set -u` 在 bash ≤4.3（macOS 3.2/RHEL7 4.2）
    为 unbound variable——加 `${pip_args[@]+...}` 哨兵。
  - R6（已返工）：`cat "$@"` 零实参读 stdin 挂起——加显式用法守卫 return 2。
  - R7（已返工）：`_SCRIPT` 未 resolve 而 harness 先 cd——与兄弟路径对齐。
  - R9（已返工）：source-guard 测试断言位置错误（$0=-c 永远不会指向 tmp_path）+ docstring
    声称不存在的 export 断言——修正措辞与断言集。
  - R2/R3/R4/R10/R11（记录，不返工）：requirements 缺失时新硬失败（合理 delta）；并发
    bootstrap 竞窗无锁（AGENTS.md 禁并行 e2e，结果收敛、优于旧静默中毒；flock 留作后续）；
    bin/python 为目录时不自愈（与旧行为同等，响亮失败）；`$$` 计数器 PID 复用理论性；
    python3 -m venv 真创建 ~6s 成本可接受。
  - 评审同时行级核实：exec 路径行为逐段等同旧版（导出/cd/重建块/pytest 调用）、source-guard
    三种调用形态、md5sum 于 git-bash 可用、tests/test_pytest_collection_gate.py 仍绿
    （新文件不漏入裸 pytest）。

## 20. 20260912-wknd 续四（基线 0dc5957——master 已并我方 #566/#568 与他组 #567）

- **合并竞态发现与补救**：#566 于 19:23Z 从**返工前 tip（f21adb6）**被并——第 8 轮审查
  MINOR（无引号分支用 JS `\s` 且排除引号，截断捕获可解析出已落盘前缀而空泛通过）的修复
  b5677e4 未进 master。**PR #571（cherry-pick 原样重落，待 lead）**：逐字节核验
  （PR 自身两文件对 b5677e4 diff 为 0 字节；基座偏移仅 #567/#568 的无关文件）；新增前缀
  判别测试对 master 现行正则实证红（评审代理复跑：截断捕获 → "1 local assets, all
  present" rc=0 → 断言失败，与预测一致）。
- **PR #572（guard 目录引用拒绝，待 lead）**：`existsSync` 对目录返回 true（第 8 轮 F2
  NIT 转正）：引号 `src="chunks/"` 与无引号 `src=chunks/>`（捕获含尾斜杠）的目录引用
  通过 guard，而静态服务器（tauri 资产处理器同理）对目录 URL 404——坏页静默出货。
  改 `statSync().isFile()` + catch 即缺失（fail-closed）。红/绿实证：旧检查下两种形态
  rc=0（红），修复后拒绝并点名 `chunks/`；旁挂真实文件放行；既有 8 测试零回归。
- **第 11 轮审查（1 子代理，双 PR，文件/行级）**：
  - #571 **REQUEST_CHANGES → 已返工**：新测试正向对照创建 `chunk"quoted.js` 文件——
    `"` 在 NTFS/FAT 禁用字符集，Windows（本仓主平台）下套件直接报错。返工：两形态保留
    拒绝相（仅落盘合法前缀 `chunk`，红探针不变），正向对照仅对 NTFS 合法的 NBSP 名运行。
    9/9 复绿。
  - #572 **APPROVE**：评审行级核实——TOCTOU 为构建期固有且 catch fail-closed（LOW 记录）；
    statSync 与 existsSync 同为跟随型（符号链接行为零回归；D5 服务器拒链接属运行时威胁
    模型差异，非缺口）；跳过表先于 statSync 生效（无跳过项可达 stat）；两 PR 正交可叠加
    （#571 的 tokenizer 类下 `src=chunks/>` 捕获不变）。LOW 注记（头注释措辞过时）已修。
- **串行证据**：#571 分支套件 9 passed；#572 分支套件 9 passed；node --check 双绿。

## 21. 20260912-wknd 续五（基线 2dea2ec——#569/#570/#571 已并；#572 待 lead）

- **交叉审查（无缺陷）**：#569（他组 `_await_go` 双侧钉死，纯测试）与 #567（runtime 目录
  符号链接拒绝钉死）行级复核通过；#573 前 probe：e2e 已断言 manifest 39443（L712）+
  apps_web_tree（L734），build-info.relay_origin 与 manifest 检查冗余——无缺口。
- **PR #573（runner 识别 Windows venv 布局，待 lead）**：`run-sidecar-e2e.sh` 仅认
  `bin/python`——Windows 宿主 venv（git-bash 驱动 Windows python 产生 `Scripts/python.exe`，
  恰是 `start-desktop.cmd` L19 要求的布局）下 runner 每次重跑 `python3 -m venv` 且安装步骤
  因 bin/python 缺失失败（评审纠正因果链：死于 install 而非 exec）——而 setup-codex.ps1
  建的是另一个 venv（`.venv`），start-desktop.cmd 的补救链在 Windows 上死路。新增
  `resolve_venv_python`（bin 优先 → Scripts 回退 → 两者皆缺返回非零），安装/导出/重建/exec
  全部经其解析；POSIX 行为不变。
- **串行证据**：契约套件 10 passed；全量真 runner **102 passed in 63.22s**；Windows NOT RUN
  （Scripts 判定用 stub 可执行文件离线证明）。
- **第 12 轮审查（1 子代理，文件/行级，REQUEST_CHANGES → 已返工）**：
  - F1 HIGH（返工）：新 Scripts 布局测试对 master 也绿——Linux 上 `python3 -m venv` 会
    原位补出 bin/python（评审探针：master 恰好多调一次 venv 模块"治愈"布局），断言无法
    区分两侧。返工：失败型 python3 shim 作创建探针（识别测试：仅凭 Scripts/ 解析成功则
    shim 永不被调 → rc=0；bin-only 判定失败 → shim 触发 → ENSURE_RC=3），并按评审方法在
    scratch worktree 对 master runner 实证红（1 failed, 1 passed）。
  - F2 LOW（返工）：ensure 的 if 上下文中 resolve 的成功回显泄漏到调用方 stdout——
    `>/dev/null`。
  - F3 INFO（返工，doc）：因果链更正（install 步失败先于 exec）。
  - F4 INFO（记录）：git-bash 下导出的 MANGAFLOW_DESKTOP_PYTHON 为 POSIX 形路径——已核实
    全部消费方（测试断言剥离、helper 剥离、start-desktop.cmd 自设 Win32 路径）均不受影响；
    若未来喂给 Win32 消费方（cargo 测试）需转换——非本 PR 范围。

## 22. 20260912-wknd 续六（master e4f6bcf——#572/#573 已并，本轮 6 PR 全部合入）

- **合并状态**：#566（guard 无引号值+tokenizer 重落）、#568（D5 READY 读取加固）、
  #570（runner venv bootstrap 自愈）、#571（#566 竞态重落）、#572（guard 目录引用拒绝）、
  #573（runner Windows venv 布局）——本轮全部 merged。
- **Issue #575（新开）**：`measure-native-startup.ps1:70-76` 采样循环不查 `HasExited`——
  崩溃根进程的样本烧满 120s + 3s + 45s + 5s ≈ 165s/样本，默认 3 样本全坏 ≈ 8 分钟死等。
  建议在 Refresh 后加 `if ($proc.HasExited) { break }`（记录行保持诚实，L105 的
  `-not $exited` 门自然跳过杀树）。证据边界：沙箱无 pwsh，静态证明；运行时验证属
  Windows 测量主机（NOT RUN）。
- **静态复核（无缺陷）**：`start-desktop.cmd`（相对 REPO 路径、env 传递经 start、
  WEB_DIST 缺席降级路径均正确）；`tauri.conf.json`（bundle.resources 对缺失的 assembled
  树为 fail-closed；无 devUrl → "tauri dev" 非文档化流程，dev 守卫缺口不成立——全仓 grep
  无 tauri dev 用法）；`fake_channel.remove()`（has_table 守卫、定向删除、幂等、单事务）；
  `build-frontend-static.sh` 锁外 tee（追加型日志，非破坏性）。
- **串行证据（master e4f6bcf）**：真 runner **103 passed in 62.85s**（他组 #569 的
  `_await_go` 钉死入列）；shell-core cargo **153 passed / 0 failed**。

## 23. 20260912-wknd 续七（master e4f6bcf）

- **PR #576（guard 真·占位符 clean-clone 契约钉死，待 lead，test-only）**：套件此前只用
  合成页面测试 guard——其存在理由（#444：干净克隆下被 track 的占位符 index.html 引用
  全部 gitignored 的 chunk，tauri build 必须被拒）从未对真实工件钉死。测试从 git 重建
  clean-clone 状态（ls-files → 仅 index.html + shell-tools.html；`git show HEAD:` 字节级，
  对持有完整构建产物的 working tree 保持封闭）并要求拒绝 + 指名悬空 `_next/static/chunks/`
  引用 + 补救提示。占位符若将来自洽，钉子响亮失败、须有意识更新。
- **第 13 轮审查（1 子代理，文件/行级，REQUEST_CHANGES → 已返工）**：
  - F1 HIGH（返工，评审者实证复现）：`git ls-files` 裸 pathspec 按进程 cwd 解析——从
    apps/desktop/scripts 运行套件时匹配为空 → 假失败。返工：git 调用显式 `cwd=` 仓库根
    （对齐 provenance 测试先例），双 cwd 运行均 11 passed。
  - F2 LOW（返工）：`.split()` 按空白切分不抗路径内空格/非 ASCII（quotePath 八进制转义）
    ——改 `-z` + `\\0` 切分。
  - F3 LOW（返工）：pytestmark 增补 git 可用性 skipif（镜像既有 node skipif）。
  - F4 INFO（记录）：`missing[0]` 断言依赖占位符文档序首个引用为 chunk——是钉子的正确
    行为（变更时强制有意识更新），非假阳性风险。
  - 评审同时核实：字节保真（sha256 一致，含 UTF-8 中文）、污染克隆下仍封闭、无 vacuous
    通过路径、diff 仅测试文件。
