# V02-13A 可选 CLI 通道公共执行器契约设计

- 任务：Issue #53 / `[0.2.0][V02-13A] Design the optional CLI channel executor contract`
- 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（`origin/master`）
- 工作分支：`codex/v02-13a-cli-executor-contract`（worktree `D:\自媒体\漫画工作流-deepseek-v02-13a`）
- 任务性质：L3 架构设计草案（DeepSeek 起草，不拥有最终批准权；**不是已批准 ADR**，最终方案由 lead 集中复审确认）
- 约束：本文件只冻结设计契约，**不实现代码、迁移、测试；不编辑 `docs/roadmap.md` / `docs/development-progress.md` / `plan.md`；不读取凭据、不登录 CLI、不调用真实 CLI、不安装软件**
- 修订记录：（由 lead 接管收口时填写）

---

## 1. 执行摘要与通道定位

V02-13 把 Codex CLI、Antigravity CLI、Grok Build 等**外部命令行工具**视为可选的**图像执行通道**，而不是假装成普通 HTTP API 的供应商。本契约冻结公共执行边界：能力/版本/登录探测、工作目录隔离、结构化输入输出、进程树生命周期、安全与清理、状态机与测试拆分。

**通道定位红线**（对齐 V02-02 供应商平权）：

1. CLI 通道是**普通可选 provider/channel**，与 HTTP 供应商同入目录、同一可用性/路由公式；**不享有默认优先级、不预置 `VERIFIED`、不 auto-enable**。
2. CLI 通道的凭据形态是**第三方管理的外部登录会话**（`credential_source = CLI_SESSION`，本契约新增，见 §4.3）；应用不读取、不复制、不代理 CLI 的凭据。
3. **禁止静默 fallback**：CLI 通道不可用/未登录/失败时，任务必须进入明确终态并给出原因；绝不自动改走 HTTP 付费供应商（§8）。
4. 通道的具体 CLI（`codex`/`antigravity`/`grok build`）接入由 V02-14A/B/C 分别实施，各走本公共契约；本契约不绑定任何具体 CLI 的私有行为。

### 1.1 既有可复用设施（仓库证据）

| 设施 | 位置 | V02-13A 复用 |
| --- | --- | --- |
| 适配器契约与统一错误码 | `apps/api/app/model_adapters/base.py`（`ProviderAdapterError(code, retryable, retry_after_seconds)`；`ImageRequest/ModelResponse`） | CLI 执行器实现同一 `ImageModelAdapter` Protocol，错误码复用统一码集 |
| 通道绑定与可用性公式 | `apps/api/app/services/model_router.py`（protocol→adapter 绑定；`_require_available_credentials`）；`model_availability.py` | CLI 通道走同一绑定与公式 |
| 凭据形态契约 | V02-02 `docs/v02-provider-neutrality-audit.md` §4.1（`credential_source = CONNECTION_KEY | ENV_SERVICE_ACCOUNT`） | 新增第三种 `CLI_SESSION`（§4.3） |
| 协议能力声明 | `apps/api/app/services/provider_catalog.py`（Anthropic 图片限制、端点模板、模型操作） | CLI 通道的能力按探测结果声明（§4） |
| **Windows 进程树所有权** | `scripts/owned_processes.py`（`_winapi.CreateProcess` + `CREATE_SUSPENDED`、`AssignProcessToJobObject`、`TerminateJobObject`、持久化 journal、`recover_stopped_tree`、目录 token + symlink/junction 拒绝、环境白名单） | **本契约进程生命周期的事实基础**（§5/§9），直接复用其模式 |
| Windows Worker spawn | `apps/api/app/rq_windows.py`（WindowsSpawnWorker 替代 POSIX `os.setpgrp`） | CLI 执行器在 Windows 上的 spawn 先例 |
| 凭据脱敏 | `apps/api/app/services/credential_crypto.py:83-88`（`secret_hint` → `••••1234`） | 日志/命令脱敏复用该模式（§6） |
| 调用审计 | `apps/api/app/services/worker_handlers/model_call_audit.py`（独立事务、`dispatch_no` 单调、outcome 三态 SUCCEEDED/FAILED/NULL=crash） | CLI 调用同样进入 `ModelCallAttempt` 账本（§10） |
| 健康/探测 | `apps/api/app/models.py` `ModelProbe`（probe_type/status/latency/error_code/message）、连接健康字段 | CLI 能力/版本/登录探测写 `ModelProbe`（§4） |

---

## 2. 当前仓库证据（V02-13A 相关现状盘点）

1. `base.py:7-20`：`ProviderAdapterError` 是唯一适配器异常面，`code` 为统一错误码、`retryable` 控制重试——CLI 执行器所有失败必须归入此面，不得抛出裸 `RuntimeError` 或 CLI 私有异常。
2. `base.py:70-74`：`ImageModelAdapter` Protocol（`generate_page/generate_asset/edit_region/capabilities`）——CLI 通道实现该 Protocol，工作流/生成台不需要感知通道差异。
3. `model_router.py`（V02-02 已盘点）：通道按 `connection.protocol` 绑定适配器；`_require_available_credentials` 按 `credential_source` 判定凭据就绪。CLI 通道需在此扩展 `CLI_SESSION` 分支。
4. `owned_processes.py`：**完整可用的 Windows 进程树所有权基础设施**——Job Object 归组、CREATE_SUSPENDED 防逃逸、TerminateJobObject 整树终止、journal 持久化（`state/processes/pid/exit_code/error`）、`recover_stopped_tree` 拒绝杀死活动 controller、目录 token + symlink/junction 拒绝（`:176,:193,:208`）、环境白名单（`:335-340` 保护 SystemRoot/WINDIR/TEMP/TMP/PYTHON*，拒绝环境拷贝继承）。V02-13A 的进程边界完全建立在该模式上。
5. `rq_windows.py`：Windows 上 Worker spawn 不能依赖 POSIX 进程组——CLI 执行器同理，不能假设 `os.setpgid`/`killpg` 可用。
6. `model_call_audit.py`：每次实际派发独立审计行、`dispatch_no` 单调、crash 时 `outcome=NULL` 保留证据——CLI 调用（含真实 CLI 的子进程派发）应同样入账。
7. 现状**没有**任何 CLI 通道实现、没有 CLI 协议、没有外部进程调用适配器——本契约是纯新增设计，不改动既有 HTTP 通道行为。

---

## 3. 通道即 provider/channel：注册与优先级

### 3.1 协议与目录

- 新增协议枚举值（`provider_schemas`/能力表）：`CLI_CODEX`、`CLI_ANTIGRAVITY`、`CLI_GROK_BUILD`（V02-14A/B/C 各注册一个）。
- 每个 CLI 通道注册为 `ProviderProfile`（`built_in=true`，预设种子）+ `ProviderConnection`（`base_url` 置空或 `cli://<name>` 伪 URL，对齐 V02-02 P5 的"无 HTTP 端点的原生连接"显式化）+ 目录 `AIModel` 行（`confidence=DECLARED`、`enabled` 由状态机决定）。
- **平权**：不预置 `VERIFIED`、不给 `priority` 加成、不隐藏默认、不作为 auto 路由兜底。CLI 通道模型只有探测确认（§4）后才可能参与显式选择；`VERIFIED` 只能由统一模型冒烟验证写入（对齐 V02-02 M3/M4）。
- `auto_enable_pending` 状态机不适用于 CLI 通道（那是账号型 HTTP 连接的一次性机制）；CLI 通道启用由「CLI 存在 + 已登录」的探测结果驱动（§4.4）。

### 3.2 无默认优先级

- 自动路由（`model_router.py` auto）候选集对 CLI 通道**不施加任何加权/优先**；CLI 通道模型与 HTTP 模型按同一 `confidence/health` 公式竞争。
- 用户显式选择 CLI 通道的任务，通道失败不得自动重路由到 HTTP 通道（§8）。

---

## 4. capability / version / login 探测与状态

### 4.1 探测项

| 探测 | 手段 | 输出 |
| --- | --- | --- |
| 存在性 | `where <cli>`（Windows `where.exe`）或配置的绝对路径存在性检查 | `UNAVAILABLE` 判定 |
| 版本 | `<cli> --version`（或等价） | 版本字符串，写入 `ModelProbe.metrics.version` / 连接 `nonsecret_config` |
| 登录态 | `<cli> auth status`（或等价，**只读**） | `UNAUTHENTICATED` 判定 |
| 生图能力 | CLI 能力表 / `<cli> ... --help` 能力探测 | `UNSUPPORTED` 判定（该 CLI 不支持 image_generate 时） |
| 冒烟 | 可选低 token 冒烟（V02-13 不做付费冒烟，标记 NOT RUN） | `VERIFIED` 资格 |

探测全部走 `ModelProbe`（`models.py:985-1001`）持久化：`probe_type ∈ {CLI_PRESENCE, CLI_VERSION, CLI_LOGIN, CLI_CAPABILITY}`，`status ∈ {PASSED, FAILED, UNKNOWN}`，`error_code/message` 记录可执行失败原因。

### 4.2 通道状态机（连接级）

```text
UNKNOWN ──探测──> PROBING ──┬─ 全部通过 ───────────────> AVAILABLE
                            ├─ CLI 不存在 ──────────────> UNAVAILABLE
                            ├─ 存在但未登录 ────────────> UNAUTHENTICATED
                            └─ 存在且登录但缺生图能力 ──> UNSUPPORTED
AVAILABLE ──探测重跑──> PROBING（循环）
AVAILABLE / UNAVAILABLE / UNAUTHENTICATED / UNSUPPORTED ──失败错误码持久化──> 同态 + error_code
```

UI 文案（对齐 V02-11 文案原则，枚举不进主界面）：`未安装` / `未登录 CLI` / `不支持图片生成` / `就绪`。**不可用即不可调**：非 `AVAILABLE` 的通道，显式选择与 auto 路由均排除。

### 4.3 `credential_source = CLI_SESSION`（契约扩展）

- V02-02 定义了 `CONNECTION_KEY | ENV_SERVICE_ACCOUNT`。本契约新增 **`CLI_SESSION`**：凭据由外部 CLI 自行管理（用户已在终端登录），应用不持有、不读取、不复制 CLI 的 token。
- `credential_ready` 派生：`CLI_SESSION` → CLI 存在且登录态 `PASSED`（探测结果）。
- 应用侧**不提供**密钥表单（UI 显示"由外部 CLI 会话管理"），不调用 `auth login`（禁止代表用户登录）。

### 4.4 启用语义

- 通道 `enabled` 由探测驱动：首次探测 `AVAILABLE` → `enabled=true`（`confidence=DECLARED`）；探测 `UNAVAILABLE/UNAUTHENTICATED/UNSUPPORTED` → `enabled=false` 并持久化原因。
- 人工 `enable/disable` 覆盖探测结果（用户可显式停用通道）；探测失败不自动 `enabled=true→false`（对齐账号型"凭据暂时缺失不自动停用"的既有语义）。

---

## 5. 工作目录隔离与输入输出文件所有权

### 5.1 run 工作目录

每次调用分配 `storage/cli_runs/{run_id}/`（`run_id` 为随机 UUID，非递增可猜值）：

```text
storage/cli_runs/{run_id}/
  input/request.json        # 结构化请求（§6.1）
  input/references/         # 参考图（显式硬拷贝/受控复制，禁止 CLI 直接读 uploads 全目录）
  workspace/                # CLI 允许写入的临时空间（工作目录，进程 cwd）
  output/result.json        # 结构化结果（§6.2）
  output/images/            # CLI 生成图片（所有权校验）
  journal.json              # 进程所有权 journal（对齐 owned_processes）
```

- 目录创建后写入随机 `token` 到 `journal.json`；后续所有路径解析（输入/输出/回收）用 token 校验目录归属，拒绝 symlink/junction 逃逸（§9.3）。
- **允许访问范围**：CLI 进程只能看到 `workspace/`（cwd）+ `input/`（只读）；**不得**把 `uploads/`、`storage/generated/`、源码目录、凭据文件等任何项目路径传入。参考图通过 `input/references/` 受控复制（sha256 去重 + 尺寸上限复用 `Asset` 安全面）。

### 5.2 输入输出文件所有权

- **输入所有权**：`request.json` 由服务端生成并锁定（运行期间只读）；参考图是 `Asset` 文件的受控副本，与 `JobAssetReference` 租约联动（参考图被活动任务删除时该 run 的副本仍在工作目录，不受影响）。
- **输出所有权**：服务端只承认满足以下条件的输出：
  1. 文件解析路径在 `storage/cli_runs/{run_id}/output/` 内（`resolve()` 后 `is_relative_to` 校验）；
  2. 已登记到 `journal.json` 的输出清单（CLI 通过 `output_spec` 写回 `output/result.json` 的文件名列表）——**未登记的文件不采用**；
  3. 每个输出文件计算 sha256 并校验可解码（图片用 Pillow 校验，复用上传安全面）。
- 部分输出/多余文件：不采用未登记文件，不采用解码失败文件（§7 无效 JSON/部分输出）。

---

## 6. 结构化请求、结构化结果、stderr/stdout 编码、日志脱敏

### 6.1 结构化请求（`input/request.json`）

```json
{
  "schema_version": 1,
  "operation": "image_generate",
  "prompt": "…",
  "parameters": { "resolution": "1K", "aspect_ratio": "3:4" },
  "reference_images": ["input/references/<sha256>.png"],
  "output_spec": { "images": ["output/images/out_001.png"], "max_images": 1 }
}
```

- `schema_version` 由契约维护；V02-14A/B/C 的 CLI 侧适配各自实现读写，但**格式唯一**。

### 6.2 结构化结果（`output/result.json`）

```json
{
  "schema_version": 1,
  "status": "SUCCEEDED" | "FAILED" | "PARTIAL",
  "images": ["output/images/out_001.png"],
  "usage": { "estimated_cost": null },
  "error": { "code": "…", "message": "…" }
}
```

- **业务数据只从 `result.json` 解析**；stdout/stderr 是诊断日志，不承载业务语义（防 CLI 混入 ANSI/进度文本）。

### 6.3 stdout/stderr 编码与日志脱敏

- **编码强制 UTF-8**：Windows PowerShell 5.1/部分 CLI 默认 GBK/CP936，命令输出需以 `errors="replace"` + UTF-8 解码读取，防止混合编码损坏日志（V02-02 已记录 Windows 混合编码陷阱；对齐 AGENTS.md 对 Grok 的混合编码审查项）。
- **日志脱敏**：
  - 命令字符串只记录**参数化后的 argv 摘要**（`<cli> <op> <run_id>`），不记录完整命令行含秘密参数；
  - stdout/stderr 截断（≤8KB）并跳过疑似 token 行（含 `sk-`、`token`、`authorization` 前缀，复用 `secret_hint`/脱敏模式）；
  - `journal.json`/`ModelCallAttempt.error_message` 只存脱敏文本，禁止完整命令与输出。

---

## 7. 进程生命周期：Windows launcher PID、真实子进程、取消、超时、退出码

### 7.1 launcher 与真实进程

- CLI 常是 shim/launcher（npm 全局 bin、`grok` 包装器等），真实进程是其后代。**只杀 launcher PID 会留下孤儿树**。
- 契约：以 **Windows Job Object** 归组整棵进程树（`AssignProcessToJobObject`，无 breakaway），取消/超时用 `TerminateJobObject` 整树终止（对齐 `owned_processes.py:373-406`）。**禁止 `taskkill` / 按端口杀进程 / 依赖 PID 单向 kill**。

### 7.2 取消与超时

- 请求级超时（默认按操作：图片生成 120s）→ 进程级硬超时（超时 + 宽限 5s）→ `TerminateJobObject(125)` → 状态 `FAILED(TIMEOUT)`。
- 取消：任务取消（既有 `GenerationJob.cancelled_at` 语义）→ 同硬超时路径终止进程树；取消后 CLI 晚返回的输出**不采用、不写候选**（对齐 P1-7 既有租约/取消保护）。

### 7.3 退出码与崩溃

- 退出码 `0` + `result.json SUCCEEDED` = 成功；非零退出码映射到统一错误分类器（`CONFIGURATION`/`UPSTREAM`/`RATE_LIMIT` 等，复用码集）；**退出码为 0 但 `result.json` 缺失/无效** = `FAILED(UNKNOWN_RESULT)`（§7.4）。
- 崩溃（无退出码、进程消失）：journal 标记 `state=crashed`，工作目录保留证据；`ModelCallAttempt.outcome=NULL`（crash 未收尾），下次启动 `recover_stopped_tree` 类恢复扫描。

### 7.4 失败与部分输出语义

| 场景 | 判定 | 采用 |
| --- | --- | --- |
| 崩溃/挂起 | `FAILED(CRASH)` / `FAILED(TIMEOUT)` | 不采用 |
| 部分输出（result.json 成功但 images 缺失/数量不足） | `FAILED(PARTIAL_OUTPUT)` | 不采用部分图 |
| 无效 JSON（result.json 解析失败） | `FAILED(INVALID_OUTPUT)` | 不采用 |
| 未知结果（进程退出但无 result.json） | `FAILED(UNKNOWN_RESULT)` | 不采用，保留工作目录证据 |
| 输出文件未登记/解码失败 | `FAILED(INVALID_OUTPUT)` | 不采用 |

### 7.5 重试语义

- 可重试：`RATE_LIMIT`、`TIMEOUT`、`UPSTREAM`（瞬时）→ 走既有 `max_attempts` 退避（复用 Worker 重试机制）。
- 不可重试：`UNAVAILABLE`、`UNAUTHENTICATED`、`UNSUPPORTED`、`INVALID_OUTPUT`、`PARTIAL_OUTPUT`、`UNKNOWN_RESULT`（确定性失败）→ 直接终态，不消耗无谓重试。
- **重试不得改走其他通道/模型**（§8）。

---

## 8. 禁止静默 fallback（硬红线）

1. 任务显式路由到 CLI 通道 → 通道 `UNAVAILABLE/UNAUTHENTICATED/UNSUPPORTED` 或执行失败 → **任务终态 `FAILED`**，错误码/原因精确可读，UI 给出「改用其他通道/模型」的**显式用户动作**（如"换用已配置的 OpenAI 通道重试"按钮）。
2. auto 路由候选集**不含** `UNAVAILABLE` 的 CLI 通道模型（探测驱动，§4.4）；auto 不会把 CLI 通道作为 HTTP 通道失败后的兜底。
3. 任何代码路径**不得**在 CLI 派发失败后自动改调 HTTP 付费供应商；路由切换只发生在任务创建时（路由策略），不发生在执行中失败后自动切换（对齐 V02-02 M9 的 route_switched 审计语义——即使有 `route_switched` 记录，也必须是**已批准的显式策略**，且入账）。
4. 测试矩阵必须断言：CLI 失败后 HTTP 通道调用数为 0（§12 C7/C8）。

---

## 9. 并发限制、资源清理、残留进程检测、清理失败传播

### 9.1 并发限制

- 全局 CLI 通道并发上限（默认 1，可配置；每通道独立，如 codex/antigravity/grok 各 1），超出进入 `WAITING`（复用项目并发语义，`CONCURRENCY_LIMIT`），不产生额外进程。
- 并发窗口与账号型通道隔离；不允许 CLI 通道与 HTTP 通道并发争用同一项目并发名额的既有语义被破坏。

### 9.2 资源清理

- **每次 run 结束（成功与失败）都清理工作目录**，除非失败需要保留现场（此时登记 run 目录到 journal 供诊断，后续手动清理）。
- 清理删除 `storage/cli_runs/{run_id}/` 全目录（先校验 token 归属，再 `shutil.rmtree`，拒绝越界）。

### 9.3 残留进程检测

- 启动时扫描 `storage/cli_runs/` 的 journal：`state=crashed`/`state=running` 但 controller 已死的 → 按 `recover_stopped_tree` 语义恢复（拒绝杀死活动 controller；Job Object 归组的进程在 controller 死后自动终止，无需 taskkill）。
- 对 Job Object 归组的进程，controller 死亡本身即终止整树（Windows Job 语义），无需 PID 级清理。

### 9.4 清理失败传播

- 清理失败**不掩盖任务结果**：任务按 §7.4 落终态；清理失败以 `ExceptionGroup`/日志形式传播并记录（对齐 `owned_processes.py:415-426` 的 `__exit__` 语义），进入后续清理队列重试，不静默吞掉。

---

## 10. 安全边界

### 10.1 凭据

- 应用**不读取、不存储、不代理** CLI 的登录凭据；不调用 `auth login`；不向 CLI 进程注入 API token（CLI 用自己的会话）。
- CLI 输出的 `result.json`/日志含疑似 token 一律脱敏（§6.3）。

### 10.2 环境变量

- CLI 进程 env 用**显式白名单**构造（SystemRoot/WINDIR/TEMP/TMP/PYTHONDONTWRITEBYTECODE + 最小 PATH），**不继承完整父环境**；禁止覆盖受保护键（对齐 `owned_processes.py:335-340`）。
- 代理/镜像变量（HTTP_PROXY 等）按项目既有设置决定是否注入，不默认继承。

### 10.3 命令注入

- 所有 CLI 参数以 **argv 数组**传递（`_winapi.CreateProcess(lpCommandLine)` / `subprocess.list2cmdline` 天然参数化），**禁止拼接 shell 字符串**、禁止经过 `cmd /c`/PowerShell 解释层拼接用户输入。
- `prompt`/参数写入 `request.json`（数据文件），不进命令行——CLI 从文件读，杜绝命令行注入面。

### 10.4 路径穿越与符号链接/重解析点

- 工作目录/输入/输出文件解析后必须 `resolve()` 且落在 `storage/cli_runs/{run_id}/` 内；**拒绝 symlink、junction、reparse point**（对齐 `owned_processes.py:176,193,208` 的检查）。
- 参考图复制目标为 `input/references/<sha256>.<ext>`，源文件解析后必须在 `uploads/` 合法集合内（复用上传路径校验）。
- CLI 生成的输出路径若 `..` 逃逸或指向已有文件（覆盖攻击），拒绝采用并记 `INVALID_OUTPUT`。

### 10.5 其他

- 单次 run 输出体积上限（复用上传安全面的像素/字节限制，如 ≤20MB/图、像素上限）；stdout/stderr 截断（§6.3）；`request.json` 体积上限。

---

## 11. 调用审计与账本

- CLI 每次实际派发进入 `ModelCallAttempt`（`worker_handlers/model_call_audit.py`）：`provider=<preset_key>`、`model_id=<CLI 模型 ID>`、`dispatch_no` 单调、`outcome` 三态。
- `route_switched` 语义保留：CLI→HTTP 的切换**只允许**发生在任务创建时的已批准路由策略，且入账；执行中自动切换禁止（§8）。
- CLI 通道的成本估算：外部 CLI 计费由 CLI 供应商管理，应用 `estimated_cost=UNKNOWN`（不伪造零成本，对齐 V02-15 的 unknown 语义），UI 标注"费用由外部 CLI 计费"。

---

## 12. 状态机与测试矩阵

### 12.1 状态机汇总

```text
通道级： UNKNOWN → PROBING → AVAILABLE | UNAVAILABLE | UNAUTHENTICATED | UNSUPPORTED
调用级： QUEUED → PREPARING(工作目录/输入) → RUNNING(Job Object) → COMPLETED | FAILED(code)
调用失败终态 code：UNAVAILABLE | UNAUTHENTICATED | UNSUPPORTED | TIMEOUT | CRASH
                  | PARTIAL_OUTPUT | INVALID_OUTPUT | UNKNOWN_RESULT | RATE_LIMIT | UPSTREAM | CONFIGURATION
```

### 12.2 测试矩阵（拆 V02-13 公共执行器 / V02-14A/B/C 具体通道）

**V02-13（公共执行器，L3；全离线、假 CLI 可执行文件）**

| 编号 | 场景 | 类型 |
| --- | --- | --- |
| C1 | 探测状态机：假 CLI（存在/缺失/未登录/不支持生图）→ AVAILABLE/UNAVAILABLE/UNAUTHENTICATED/UNSUPPORTED 各分支；`ModelProbe` 记录 probe_type/status/error_code | 新增 pytest（假可执行文件 + PATH 注入） |
| C2 | 工作目录隔离：run 目录创建/token 校验/输入复制；CLI 访问范围不含 uploads/源码路径 | 新增 pytest |
| C3 | 输出所有权：登记文件采用、未登记/越界/解码失败拒绝；sha256 校验 | 新增 pytest |
| C4 | 结构化 IO：request.json/result.json 读写；无效 JSON/缺失 result → INVALID_OUTPUT/UNKNOWN_RESULT | 新增 pytest |
| C5 | 进程生命周期（Windows）：Job Object 归组、TerminateJobObject 取消、硬超时、退出码映射；**禁止 taskkill** 断言 | 新增 pytest（仅 Windows；非 Windows 跳过并记录 NOT RUN） |
| C6 | 重试：可重试错误退避重跑；不可重试直接终态；重试不改通道 | 新增 pytest |
| C7 | **禁止 fallback**：CLI 失败后断言 HTTP 通道调用数 0；任务 FAILED + 原因 | 新增 pytest |
| C8 | 并发/清理：并发上限 WAITING；run 目录成功与失败均清理；残留 journal 恢复；清理失败传播不掩盖结果 | 新增 pytest |
| C9 | 安全：argv 参数化（无 shell 拼接）、env 白名单/禁止覆盖保护键、symlink/junction 拒绝、路径穿越拒绝、stdout 截断脱敏 | 新增 pytest |
| C10 | 审计：CLI 派发进 ModelCallAttempt，outcome 三态；estimated_cost=UNKNOWN | 新增 pytest（扩展 `test_model_call_audit`） |

**V02-14A/B/C（各 CLI 通道接入；真实 CLI 未验证）**

| 编号 | 场景 | 类型 |
| --- | --- | --- |
| D1 | 该 CLI 的探测适配：真实 `--version`/`auth status` 输出解析（假输出夹具） | 新增 pytest |
| D2 | 该 CLI 的请求/结果映射（假 CLI 脚本模拟生图输出到 output_spec 指定路径） | 新增 pytest |
| D3 | 能力表：该 CLI 是否支持 image_generate/image_edit；不支持时 UI 禁用 | 组件测试 |
| D4 | `npm run check` 全绿；`git diff --check` 通过 | 门禁 |

---

## 13. 未验证边界（NOT RUN / NOT VERIFIED）

1. **所有真实 CLI 调用未运行**：`codex`/`antigravity`/`grok build` 的真实安装、登录、生图、版本输出、退出码、超时、取消行为全部标记 **NOT RUN**（本任务禁止安装软件/登录 CLI；V02-14A/B/C 在用户授权环境单独验收）。
2. **Windows 进程生命周期真实验证未运行**：Job Object 归组/取消/超时在真实 Windows + 真实 CLI 上的行为标记 NOT RUN（本任务只以 `owned_processes.py` 既有模式为设计依据，C5 仅覆盖假 CLI）。
3. **真实 PostgreSQL 未运行**：CLI 调用账本/探测持久化的 PG 变体需独立环境，SQLite 往返不替代（沿用项目既有真实 PG 边界）。
4. **CLI 计费/成本语义未验证**：外部 CLI 计费规则、用量回传、配额超出行为未与任何 CLI 供应商确认，`estimated_cost=UNKNOWN` 为契约默认。
