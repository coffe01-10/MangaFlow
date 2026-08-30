# V02-44A 生图编辑能力与失败恢复矩阵

- 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（origin/master）
- 工作分支：`codex/v02-44a-image-edit-capability-matrix`
- 任务性质：审计/设计文档，不实现代码、迁移或测试
- 引用规则：官方供应商资料仅引用官方域名页面并记录访问日期（本次 2026-08-30）；无法访问或未在文档确认的能力一律标 `UNKNOWN` / `NOT VERIFIED`。仓库内模型 ID（如 `gemini-3.5-flash`）是配置值，本矩阵按**协议+适配器实现**盘点能力，不假设具体模型版本
- 依赖与待决（**非已批准契约**）：Issue #47（命令/血缘草案）、#49（账本草案）、#51（局部重抽卡审计）未经审阅，仅在相关处列为依赖

## 1. 适配器与可配置供应商能力盘点（代码证据）

### 1.1 图片生成适配器族

| 适配器 | 代码 | 图片调用形态 |
| --- | --- | --- |
| `VertexImageAdapter`（VERTEX_NATIVE） | `apps/api/app/model_adapters/vertex.py:118-191` | genai SDK `generate_content`，`response_modalities=[TEXT, IMAGE]` + `image_config(aspect_ratio, image_size)` :157-163；参考图 `Part.from_bytes` :146-150 |
| `GoogleImageAdapter`（GOOGLE_NATIVE） | `apps/api/app/model_adapters/google.py:118-186` | 与 Vertex 同构；分辨率白名单取 `runtime.capabilities` :136-137 |
| `OpenAICompatibleAdapter`（OPENAI 协议） | `apps/api/app/model_adapters/compatible.py:438-511` | 有参考图 → multipart `POST {images_edit}`（字段仅 `model/prompt/size` + `image[]` 文件）:459-469；无参考图 → JSON `POST {images_generate}`（`model/prompt/n=1/size` + extra_body）:471-493 |
| `AnthropicCompatibleAdapter`（ANTHROPIC 协议） | `compatible.py:555-645` | **无任何图片生成方法**（仅 text+vision 输入） |
| `FakeAcceptanceImageAdapter` | `apps/api/app/model_adapters/fake_acceptance.py:35-66` | 测试假适配器 |

### 1.2 能力维度矩阵（●=实现/声明，◐=部分/条件，○=无，?=UNKNOWN）

| 能力 | Vertex | GoogleNative | OpenAI 兼容 | Anthropic |
| --- | --- | --- | --- | --- |
| text-to-image | ●（无参考图走同路径） | ● | ●（images_generate，`n=1` 固定 :474） | ○ |
| image-to-image（整图编辑） | ●（参考图即原图，`edit_region` :128-131 需 ≥1 参考） | ●（`google.py:128-131`） | ◐（`edit_region` :444-447 需 ≥1 参考，实现仍是带图生成，无编辑语义字段） | ○ |
| 原生 mask / inpaint | ○（适配器无 mask 参数）+ ?（官方文档确认生态存在，见 §6；未接入） | ○ + ? | ○（`images_edit` 请求体无 mask 字段 :463-467）+ ? | ○（供应商无此能力，官方文档确认 §6） |
| 多图参考 | ●（上限 `max_reference_images=14`，`provider_presets.py:416-420`；容量校验 `vertex.py:141-142`） | ◐（上限来自模型 capabilities，发现默认 1，`provider_catalog.py:705`） | ◐（multipart `image[]` 逐张 :453-458；上限同左 1/14 由目录声明） | ○ |
| 分辨率/比例 | ◐（`resolutions` 白名单 1K/2K/4K + `UNSUPPORTED_CAPABILITY` :136-140；aspect_ratio 透传） | ◐（同构） | ◐（`size_map` 映射，缺省 `1024x1536` :513-516、`provider_catalog.py:705`；无独立 aspect_ratio 参数） | ○ |
| seed / 确定性 | ○（全库无 seed 传参，grep 0 命中） | ○ | ○ | ○ |
| 结构化文本输出 | ●（`response_schema` :72-79） | ●（`google.py:67-79`） | ◐（`JSON_MODE`/`STRICT_SCHEMA`，`compatible.py:518-529`） | ◐（`output_config` :569-575） |

能力数据来源与推断：发现模型按名称/声明推断类型与能力（`provider_catalog.py:663-718`，图片默认 `resolutions=["1K"]、max_reference_images=1、size_map` :703-706）；置信度 `DECLARED/INFERRED/VERIFIED/PARTIAL/MANUAL`（:715、`models.py:975`）；能力测试写入 `verified_operations`（`providers.py:359-368`）。

## 2. capability 发现、缓存、版本变化与不可用状态

- **发现**：`discover_models`（`provider_catalog.py:492-599`）——OPENAI 走 `/models` 端点（:524-576），GOOGLE_NATIVE 走 SDK list（:512-523），VERTEX_NATIVE 不发现、返回目录现有模型（:502-506）。
- **缓存/版本变化**：无独立能力缓存；能力即 `AIModel.capabilities` JSON。重新发现时按"能力未变则保留 `VERIFIED/PARTIAL` 置信度与 `verified_operations`，能力变化则清 `last_verified_at`"处理（`_upsert_discovered_models` :631-657）——这是现状唯一的"能力版本变化"语义。
- **不可用状态**：连接级 `health_state`（`models.py:913`）+ Key 冷却（`models.py` provider_keys）+ `model.enabled/confidence`；**模型级"临时不可用"（限流/上游故障）不改变能力声明**，只在账本与连接健康体现。
- 设计需求（待契约 Issue）：把 mask/seed/多参考等位引入 `capabilities` 声明；发现结果缓存与 TTL；能力位变化视为模型版本事件；不可用表达区分"声明不具备"（永久，入口禁用）与"暂时不可用"（可重试）。

## 3. 能力不匹配时的确定性错误（现状）

| 不匹配 | 错误 | 证据 |
| --- | --- | --- |
| 分辨率不在模型 `resolutions` | `UNSUPPORTED_CAPABILITY`（适配器内） | `vertex.py:136-140`、`google.py:138-142` |
| 分辨率在路由/任务层预检失败 | `UNSUPPORTED_CAPABILITY` | `page_generate.py:369-372`、`asset_generate.py:287-290`、`model_router.py:53-55` |
| 参考图数超 `max_reference_images` | `INVALID_INPUT` | `vertex.py:141-142`、`worker_handlers/provider.py:287-293` |
| 编辑类无参考图 | `INVALID_INPUT` | `vertex.py:128-130`、`google.py:128-130`、`compatible.py:444-446` |
| ANTHROPIC 协议请求图片 | 422（协议拒绝） | `model_router.py:221-222`、`provider_catalog.py:410-416` |
| 图片任务使用 `auto` | 422（必须显式选模） | `workflow/generation.py:79-83`、`asset_generation.py:677` |

**禁止静默退化红线**：现状最大缺口是"编辑"在所有适配器中都实现为"带参考图的生成"（`compatible.py:444-447` 尤其如此），且无 mask 能力位——调用方无法区分"模型原生支持局部编辑"与"整图重画"。要求：能力位（native_mask_edit 等，见 §2）声明化后，不匹配必须返回上表确定性错误并在 UI 禁用入口；禁止自动改用其他模型/provider/整图生成兜底（与 V02-10 系列供应商平权原则一致，依赖未批准的 Issue #51 §7）。

## 4. 失败恢复矩阵

前置事实：`httpx` 客户端请求超时 90s/连接 10s（`compatible.py:256`）；进度提交与取消检查由执行外壳负责（`worker_handlers/execution.py:16` `StaleStoryboardVersionError`、:36 `_ensure_job_not_cancelled`、:49 `_commit_owned_progress`）；attempt 行在付费调用前独立事务落库（`model_call_audit.py:38-74`）。

| 结果 | 触发点 | 账本证据 | 候选/产物 | 恢复行为 |
| --- | --- | --- | --- | --- |
| 请求前失败（分镜过期/参考失效/能力不匹配/无 Key） | `page_generate.py:258-271,317-324,374-386`、`execution.py:16` | attempt：无（未进入 `_invoke_provider`）或 FAILED（能力错误在调用 lambda 内） | Worker 失败收敛后候选 FAILED，无产物 | 仅 retryable 错误按 `max_attempts` 重试；确定性输入/能力错误不自动重试 |
| 请求后未知结果（崩溃/断网） | 进程死亡，finalize 未执行 | attempt `outcome IS NULL`（`model_call_audit.py:44-47` docstring 承认） | 候选停留 GENERATING | 无补偿扫描（缺口）；人工 `reset_for_retry`（`job_service.py:485+`）；费用不可知 → 账本 unknown（依赖 #49 草案） |
| 部分产物成功（多图返回） | `page_generate.py:406` 取 `response.images[0]` | GenerationRecord.usage/output_asset_ids 只含采用图 :434-436 | 其余图片丢弃，不落盘 | 无缺口修复需求；如需保留多候选需新契约（待决） |
| 下载失败（URL 结果） | `compatible.py:499-511` → `INVALID_OUTPUT` | attempt FAILED + error_code；无 usage（响应未成图） | 无产物 | Job FAILED → 手动重试 |
| 落盘失败 | `_save_generated_asset` 异常清理 :250-253 / `asset_generate.py:98-102` | attempt 已 SUCCEEDED、GenerationRecord 未建 | Worker 失败收敛后候选 FAILED，半成品清理 | 重试会重新付费；账本必须标识“上游成功、产物持久化失败” |
| 取消 | `cancel_job` + 调用前后检查点 | 调用前取消无 attempt；调用期间取消且上游已返回时 attempt 可为 SUCCEEDED | 取消检查早于资产保存：无 GenerationRecord、无持久输出，候选 CANCELLED | 无退款；不得声称产物正常落库 |
| 超时 | `httpx.Timeout(90)` / `job_timeout_seconds` | 客户端明确抛出的 timeout → FAILED；controller/进程死亡未 finalize → outcome NULL | 可观测 timeout 候选 FAILED；仅无法 finalize 的进程死亡保持未知 | 租约恢复不得把明确 FAILED 改写成 unknown |

账本与费用证据要求（设计约束，依赖 #49 草案未批准）：上述每种结果下 attempt 行必须存在且携带 `outcome/error_code/usage(可得时)/duration_ms`；**费用证据 = attempt 粒度逐次保留**（重试与换 Key 替代派发不合并，`provider.py:248-276`）；未决行的费用标 unknown 禁止 0。

## 5. 需人工确认的破坏性操作

1. 付费图片能力测试（1K 计费调用）：`window.confirm`（`provider-management.tsx:176`）+ 后端 `acknowledge_cost`（`api.ts:833`）。
2. 删除候选（软删，连带收藏）：confirm（`generate-section.tsx:142`）。
3. 采用候选：人工文字校对确认门槛（`generation.py:237-244`）+ 过期候选显式接受（:245-255）。
4. 整页修复/升清本身当前**无**付费确认弹窗（与能力测试不一致，列待决）。

## 6. 官方资料引用（访问日期 2026-08-30）

| 来源 | URL | 获取结果 | 支撑结论 |
| --- | --- | --- | --- |
| Google Cloud Vertex AI 文档（Image overview） | `https://docs.cloud.google.com/vertex-ai/generative-ai/docs/image/overview` | 成功 | Gemini 图像"mask-free editing/多轮对话式编辑"、Imagen 提供 mask inpaint/outpaint；`image_size="2K"` 参数存在；Imagen 有"生成确定性图像"（seed）能力；参考图数量上限该页未给出（UNKNOWN）；旧 Imagen 端点弃用、引导迁移 `gemini-2.5-flash-image` |
| OpenAI Images API 参考 | `https://platform.openai.com/docs/api-reference/images` | **403（反爬，未取得内容）** | OpenAI 兼容能力位全部标 `UNKNOWN/NOT VERIFIED`；仓库行为仅以代码为准（`images_generate/images_edit` 端点模板） |
| Google Gemini API 文档 | `https://ai.google.dev/gemini-api/docs/image-generation` | **超时 ×2（未取得内容）** | GOOGLE_NATIVE 细粒度能力（参考图上限/seed）标 `UNKNOWN` |
| Anthropic Claude 平台文档（Build with Claude overview） | `https://platform.claude.com/docs/en/build-with-claude/overview` | 成功 | 文档化能力为视觉理解/PDF/文件输入，**无文本生成图片能力**——与代码一致（ANTHROPIC 无图片路径） |

## 7. UNKNOWN / NOT RUN 清单

1. `UNKNOWN`：OpenAI 兼容供应商的 mask/seed/尺寸枚举/多参考上限（官方文档未取得）；GOOGLE_NATIVE 参考图数量上限；各具体模型版本的分辨率枚举（仓库只声明 1K/2K/4K）。
2. **冻结决策**：编辑能力挂在具体目录模型上，协议/适配器只声明可表达的请求表面。模型字段至少拆分 `accepts_explicit_mask`、`supports_instruction_region_edit`、`preserves_outside_region`、`whole_image_reference_only`，每项带 `source=DECLARED|DISCOVERED|VERIFIED`；未知值不得按 false 或 true 猜测。
3. `NOT RUN`：未调用任何真实供应商（无 mask/inpaint 实测、无计费实测）；未运行真实 PostgreSQL/Redis/生产 Worker；未运行浏览器 E2E。
4. "落盘失败后重试重新付费"与"未决 attempt 无补偿扫描"为现状已知缺口，本轮只记录不修复。

## 8. 验收矩阵（可拆实现 Issue）

| # | 层 | 场景 | 环境 |
| --- | --- | --- | --- |
| M1 | 单元 | 能力不匹配错误逐条回归（§3 表全部行），错误码与用户文案不变 | 隔离 SQLite |
| M2 | 单元 | 新能力位声明后：不支持 mask 的模型收到区域请求 → 确定性错误且无付费调用；入口禁用逻辑 | 隔离 SQLite + mock |
| M3 | 服务 | 发现更新能力位变化 → `verified_operations`/置信度按现状规则收敛（回归 `provider_catalog.py:631-657`） | 隔离 SQLite |
| M4 | 服务 | 恢复矩阵逐行断言（§4 表）：attempt/候选/GenerationRecord 三方状态一致 | 隔离 SQLite |
| M5 | 服务 | 未决 attempt 补偿扫描（若立项）：终态收敛且费用标 unknown | 隔离 SQLite；真实 Worker NOT RUN |
| M6 | 并发 | 双 Worker 租约过期后同一 attempt 不双写 | 真实 PostgreSQL（NOT RUN 边界） |
| M7 | 前端 | 不匹配能力的入口禁用 + 原因展示；付费确认弹窗覆盖修复/升清（若立项） | Vitest |
| M8 | E2E | 生成→失败→重试→成功全链证据完整性（账本三表对账） | 浏览器 E2E（NOT RUN 边界） |
