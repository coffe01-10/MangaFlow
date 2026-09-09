# N2 Sidecar / 壳内 Web 审计报告（2026-09-08 夜）

- 基线：`origin/master` = `0534def`（含 #270/#271/#273/#274）；工作树
  `night/n2-platform-burn-20260908`。
- 范围：`apps/desktop/sidecar/**`、`apps/desktop/scripts/**`、plan-B 壳内 Web 非 native 面；
  shell-core 仅在交互点核对（留给 N1）。
- 硬边界：未触碰 `apps/desktop/native/**`、`apps/desktop/native-tests/**`。
- 状态标记：✅=已有代码+测试覆盖；⚠️=缺陷（本轮开 PR）；ℹ️=记录在案的残余/设计决定。

## 1. relay 字节管道（`sidecar/mangaflow_desktop_helper.py`）

| 项 | 位置 | 结论 |
| --- | --- | --- |
| 连接超时与管道隔离 | `_serve_relay` L155-204；`create_connection(timeout=5)` 后 `upstream.settimeout(None)`（PR #252，已合并） | ✅ 连接超时只约束 CONNECT；双向 pump 无读死限。客户端侧由 timeout-mode listener accept 出 blocking socket，无需显式设置。 |
| 慢响应 / keep-alive | `test_sidecar_relay.py::test_relay_delivers_a_response_slower_than_the_connect_timeout`（上游 TTFB 5.6s）、`::test_relay_serves_a_second_request_after_a_long_keep_alive_gap`（空闲 5.5s 后复用） | ✅ 红/绿已验证（旧代码必失败）。 |
| 半关闭语义 | `_pipe` finally `dst.shutdown(SHUT_WR)`（L197-201） | ✅ 半关闭=向对端转发 EOF；两个方向对称。**测试缺口：无客户端半关闭/上游 FIN 的红绿用例 → 本轮 PR-A 补表驱动用例。** |
| 单连接故障隔离 | `_pipe` `except OSError: pass`（L195-196） + accept 循环 `except OSError: return`（L170-171） | ℹ️ 单连接 RST/ECONNRESET 只死 pump 线程，accept 循环存活。**测试缺口：RST 后 relay 仍可服务下一连接 → PR-A 补。** |
| 上游拒连（错误路径） | `_serve_relay` `create_connection` 失败 → `client.close(); continue`（L173-176） | ℹ️ uvicorn 未起时 node 代理请求快速失败。**测试缺口：上游端口死掉时 relay 存活且下一连接可服务 → PR-A 补。** |
| 线程模型 | 每连接 2 pump 线程 + 1 joiner（`_pump`），并发上限 `WEB_RELAY_MAX_CONNECTIONS=128`；溢出连接立即关闭并记日志，槽位在双向 pump 均结束后释放 | ✅ 单元红绿：`test_relay_caps_concurrent_connections`（cap=2：占满→第 3 连接无响应关闭→释放槽位→新客户端可服务；旁路 limiter 的变异必失败）。PR #288。 |

## 2. bind / REUSE / EXCLUSIVE（`_bind_loopback` L87-104、`_bind_relay` L116-152）

| 平台/项 | 语义 | 状态 |
| --- | --- | --- |
| API 动态端口（L87） | Unix `SO_REUSEADDR`；Windows `SO_EXCLUSIVEADDRUSE`（#256） | ✅ 端口 0 无冲突面；Windows 防 SO_REUSEADDR 抢绑。 |
| relay 固定端口 39443（L139-151） | Unix `SO_REUSEADDR`（跨自身 TIME_WAIT 重绑，#253）；Windows `SO_EXCLUSIVEADDRUSE`（#256，保留 TIME_WAIT 残余并记录于 docstring） | ✅ 单元红绿：`test_sidecar_relay_bind.py::test_relay_bind_survives_its_own_time_wait_remnants`（旧代码失败）、`::test_relay_bind_stays_fail_closed_against_a_foreign_live_listener`（前后皆过，钉死对外 fail-closed）。 |
| web 端口 claim-bind-close | `_spawn_web_server` L499-504 | ℹ️ bind(0)→close→node bind 的极小 TOCTOU，plan-B 设计稿 §6 记录在案；#271 的 `_await_web_server` 已把"宣布未持有端口"的最坏后果关闭（见 §4）。 |
| 已知缺陷 | **web boot 失败路径不释放 relay**：`_spawn_web_server` 仅在 node spawn OSError 时 `relay.close()`（L540）；`_await_web_server` 失败（L383 调用，L554-603 实现）reap node 后返回 `(None, None)`，relay 仍绑 39443 且 pump 线程存活整会话；`_run_app` finally（L418-430）无 relay 句柄。 | ⚠️ **PR-B**：`_spawn_web_server` 返回 relay 句柄；`_await_web_server` 失败即关闭；e2e 断言降级会话 39443 不再监听（红/绿）。 |

## 3. loopback 不变量

- READY/api_origin：`_run_app` L385-396 只发布 `127.0.0.1:<dynamic>`；shell 侧 `is_loopback_origin`（shell-core protocol.rs）双向校验 api_origin/web_origin。✅
- web 服务器 bind：helper 以 `HOSTNAME=127.0.0.1` 启 node（L508），e2e `test_sidecar_plan_b_web_server_loop` 在存活期探测非回环本地 IPv4 拒连（#266）。✅
- D5 静态服务器：`verify-static-origin.mjs` L111 仅绑 `127.0.0.1:4173`（处理器围栏在 L88-107）。✅
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

- `resolve(root, "." + decodeURIComponent(path))` + `startsWith(root + sep)` 包含检查，`curl --path-asis` 式 `..`/`%2e%2e` 逃逸 → 404（commit `6a97625`）。✅
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
| PR-J #291（web 检测） | plan-B web 服务器 mid-session 退出的检测与法证日志（250ms 轮询，只记日志不重启，ADR §4.5 范围内） | Linux e2e RUN（runner 13 passed；禁用 watcher 的变异必失败） |
| 审计文档 | 本文件（随各轮审查增量更新；首轮子代理审查修订 7 处行号引用 + §11 新增 win32 可移植性残余） | 子代理抽查 30+ 引用 |

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
- **第 4 轮（本轮新增产品代码 #288 的对抗审查，1 子代理）**：REQUEST_CHANGES——MAJOR：
  `_pump` 经由 accept 循环共享 cell 读取 `client`/`upstream`（B023 晚绑定，跨线程延迟读取
  理论上可泵入外来配对）；MINOR：外层 spawn 的 RuntimeError 会杀死 accept 循环、内层失败
  仅靠引用计数关套接字、饱和日志逐行无上限；测试 0.3s 睡眠是假阴性源。全部返工：
  套接字改为线程参数传递（B023 清零）、spawn 双层防护 + 确定性关闭、饱和日志 10s 限频、
  释放槽位改 5s 有界重试（FIN/RST 均视为未释放）→ PR #290。

## 10. 红/绿判别力抽查记录

- PR-A 每用例：以 `git stash` 撤产品代码跑旧实现必须失败（半关闭/透传/RST 用例针对 `_pipe` 的 OSError 吞噬与 shutdown 语义）。
- PR-B：撤销"失败即关 relay"补丁 → 39443 断言必须失败。
- PR-C：临时移除 mjs 包含检查 → 自测断言必须失败。

## 11. 残余/不做（记录）

- relay 每连接 2 线程无上限（§1）；web 端口 bind-close TOCTOU（§2）；CSP unsafe-inline（§8）；
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

## 13. 完成审计（final sweep，master = `f6e4687`）

- **同步**：`origin/master` 自本轮起点推进 `a52fca9` → `f6e4687`（#286 合入 + N1 final
  audit #287：rotation staging 安全、ownership 已收尸后仅组信号——`signal_group_only`
  防 pid 复用误伤；与本文所有声明无冲突，scope 文件零交集）。
- **最终证据（全部 RUN，Linux，串行）**：
  - `apps/desktop/scripts/run-sidecar-e2e.sh` → **11 passed**（23.7s：e2e 3 + relay 管道 6 + relay bind 2）；
  - shell-core `cargo test` → **9/9 套件 ok**（含 N1 新增用例）；
  - D5 全程 **PASS**（静态导出重建后：握手、围栏 404、origin 注入、直连 CORS 放行 API、渲染）。
  - 续跑追加：runner **13 passed**（27.2s，含新增 mid-session 检测 e2e）；#291 待 lead 合并。
- **遗留 PR**：#286（断言统一）已由 lead 合并；本审计报告随 night 分支持久化
  （`night/n2-platform-burn-20260908`）。
- **收敛判定**：范围内（sidecar / scripts / plan-B 壳内 Web / static-export 围栏）无已知
  未修复 P0/P1；P2 级仅剩 §11 记录的既有债务（CSP unsafe-inline、relay 线程模型、
  web 端口 bind-close TOCTOU、D5 win32 可移植性），均有文档化理由。两夜累计合并
  #252/#253/#254/#259/#270/#278/#279/#281/#282/#283/#284/#285/#286。
- **未完事项**：无。Windows 实机验证保持 NOT RUN，待 D3 复验窗口。
