# V02-15A 模型用量账本扩展契约（设计草案）

- 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（origin/master）
- 工作分支：`codex/v02-15a-usage-ledger-contract`
- 任务性质：**L3 数据设计草案**，不实现代码/迁移/测试，不拥有最终批准权
- 前置：`docs/v02-provider-neutrality-audit.md` §4（账本字段沿用其统一契约）；`docs/architecture.md:113`（统一错误码分类）
- 定位：把现状"以 JSON 字典存放 usage、按读取时价格推导估算值"的账本，扩展为**计量单位结构化、来源分级（estimated/reported/billed/unknown）、多通道统一**的用量账本。所有"现状"结论均带基线文件/行号

## 1. 现状盘点

### 1.1 账本数据结构现状

| 对象 | 基线位置 | 关键字段与事实 |
| --- | --- | --- |
| `GenerationRecord` | `apps/api/app/models.py:413-439` | `job_id`:417、`provider`:420（**列默认 `"vertex-ai"`**，写入方 `page_generate.py:407-411`、`asset_generate.py:312+` 均显式传值）、`model_id`:421（上游模型 ID）、`catalog_model_id`:422、`usage`:435（JSON 字典）、`output_asset_ids`:436、`status`:437、`error_code/error_message`:438-439。每个 job 至多一行（成功终态） |
| `ModelCallAttempt` | `models.py:442-508` | 唯一键 `(job_id, job_attempt, dispatch_no)`:451-457；索引 job+started/outcome+started/catalog_model/project:458-461；CHECK 约束 outcome/dispatch/route_switch:462-475；`provider`:489、`model_id`:490、`catalog_model_id`:491、`connection_id`:494、`selected_key_id`:497、`duration_ms`:503、`usage`:504（JSON，可空）、`route_reason/route_score`:505-506、`error_code/error_message`:507-508（≤500 字符，`model_call_audit.py:113`）。**每次真实派发一行**（含重试与换 Key 替代派发，`worker_handlers/provider.py:248-276` `route_switched` 标记） |
| `ModelProbe` | `models.py:985-1001` | 连接/模型测试探测（probe_type/status/latency_ms/metrics:998/error_code），非用量账本；计费探测（图片 1K 冒烟）的费用不在任何账本中 |
| `ModelPricingVersion` | `models.py:511-578` | 不可变、生效区间版本（`provider+model_id+pricing_version` 唯一 :516-521，区间重叠检查 `model_costs.py:50-69`）；`currency`:561、`effective_from/to`:562-565、四种费率 `input/output_tokens_per_million`、`output_image_each`、`request_each`:566-577（CHECK 保证至少一个费率 :548-554）。文档明示"仅用于显式估算，非供应商账单"（`models.py:512`） |

### 1.2 写入路径现状

- `begin_model_call_attempt`（`worker_handlers/model_call_audit.py:38-74`）：独立 `SessionLocal` 事务先建行，`dispatch_no` 取 `(job_id, job_attempt)` 内 max+1，唯一约束为竞态硬闸；付费调用**之前**落行，崩溃留下 `outcome IS NULL` 的未决行。
- `finalize_model_call_attempt`（`model_call_audit.py:77-114`）：独立事务收尾；`usage/request_id` **仅在有值时写入**——文本/多模态适配器返回 schema 对象不携带 usage，这些调用 `usage` 保持 `NULL`（docstring :87-91）。
- usage 来源：图片调用来自适配器 `ModelResponse.usage`（`model_adapters/vertex.py:176-185` 转储 `usage_metadata`；`model_adapters/google.py:175-185` 同构；OpenAI 兼容 `compatible.py:491` 透传响应体 `usage`），经 `provider.py:236-242` finalize 写入。
- `GenerationRecord` 只在成功产物落库时创建（`page_generate.py:407-437`、`asset_generate.py:312+`），其 `usage` 取自最终成功响应（`page_generate.py:434`）；**失败尝试永远没有 GenerationRecord，只存在于 attempts**——重试计量已天然逐次保留。
- 关联维度现状：attempt 仅带 `job_id/project_id`（:479-484）；页面/格子/候选需经 `GenerationJob.target_type/target_id`（`models.py:355-356`）间接回溯；**没有章节/页面/格子/候选的直接外键**。

### 1.3 成本估算消费面现状

- `estimate_jobs`（`services/model_costs.py:100-140`）按 attempt 逐次取"派发时刻生效价格"（`_active_price`:217-227）累加；usage 键名别名归一（`_USAGE_ALIASES`:23-31：`input_tokens/prompt_tokens/prompt_token_count` 等）；**未知非零 usage 键触发 PARTIAL**（`_normalized_usage`:270-275）。
- 输出三态 `AVAILABLE/PARTIAL/UNAVAILABLE`（:196-214）：多币种不合并、缺 usage 或缺价格版本 → 不可估算；`complete` 判定见 `_estimate_attempt`:230-255（价格存在但数量缺失 → 不完整）。**任何缺失都不会被写成 0 计入总价**——数量缺失时该项不计入金额并降级 PARTIAL。
- API/前端：`JobRead.estimated_cost/currency/status/pricing_versions/note`（`schemas.py:789-793`），由 `workflow/jobs.py:35-48` 组装；`jobs-section.tsx:20-31` 按三态渲染并始终附注"估算值不等于供应商账单"。
- **通道**：当前只有 HTTP API；CLI 通道尚不存在（`docs/roadmap.md` V02-12 规划）。图片探测（能力测试 1K 调用，`providers.py:323-345`）不进任何账本。

## 2. 目标契约：统一用量账本

### 2.1 维度与标识

| 维度 | 契约 | 现状差距 |
| --- | --- | --- |
| 真实 provider | attempt.provider（preset_key 优先，`provider.py:119-121`） | 已有；`GenerationRecord.provider` 列默认值删除（V02-02 R13 已立项，不在本轮） |
| channel | 新增 `channel`：`HTTP_API` \| `CLI` | 新增列；现有行回填 `HTTP_API` |
| model_id / catalog_model_id | attempt 已有双 ID（:490-491） | 已有；账本聚合以 `(provider, model_id)` 对（估算现状 `model_costs.py:114`） |
| 项目/章节/页面/格子/任务/候选 | 新增冗余维度列 `chapter_id/page_id/panel_id/candidate_id`（可空），创建时从 `target_type/target_id` 解析一次 | 现状只有 `job_id/project_id`（:479-484）；避免账本查询回溯 job |
| 计量单位 | `unit_kind`：`TEXT_TOKENS` \| `IMAGES` \| `MIXED` \| `UNKNOWN` | 新增列 |

### 2.2 计量字段（attempt 级结构化列）

```text
usage_status        = UNKNOWN | PARTIAL | COMPLETE      # 计量完整性，与费用三态解耦
usage_source        = ADAPTER_ESTIMATED | PROVIDER_REPORTED | OPERATOR_BILLED
input_tokens        Numeric(20,0) NULL
output_tokens       Numeric(20,0) NULL
cached_input_tokens Numeric(20,0) NULL                  # 缓存命中的输入 token
cache_hit           Boolean NULL                        # cached_input_tokens > 0 即 true
output_images       Numeric(20,0) NULL
output_image_dims   JSON NULL                            # [{asset_id,width,height,quality}]，来自输出资产（page_generate.py:212-236）
output_asset_ids    JSON NULL                            # 关联输出资产，计费单位追溯
```

`output_asset_ids/output_image_dims` 不能在 provider finalize 时一次写完：上游响应早于资产落盘。新增幂等 `attach_attempt_outputs(attempt_id, asset_ids, dims)` 第二阶段更新，仅在资产事务提交后调用。落盘失败保持 attempt SUCCEEDED 但输出为空，并记录持久化失败事件，不伪造资产 ID。

规则：

- **缓存**：`cached_input_tokens` 是 `input_tokens` 的子集（命中部分），计费按"未缓存输入价 + 缓存价"拆分——价格版本扩展 `cached_input_tokens_per_million`（可空，NULL=按全价估算并降级 PARTIAL，与 `_estimate_attempt`:245-254 的缺率语义一致）。别名归一增加 `cached_content_token_count`（Gemini）与 `prompt_tokens_details.cached_tokens`（OpenAI，取嵌套值）。
- **图片计量**：`output_images` 计数来自 usage 或响应图片数（`ModelResponse.images` 长度，`base.py:52-57`）；尺寸/质量从输出资产读取，**不做计费单位**——v1 价格模型仍是"每张"（`output_image_each`），按尺寸/像素计价留作价格版本未来列，标注 NOT DESIGNED。
- **缺失计量必须 unknown**：字段缺失即 NULL 且 `usage_status=UNKNOWN/PARTIAL`；UI 与聚合**禁止显示 0**（延续 `model_costs.py` 现状：缺失数量不折算、整体降级）。`0` 只在供应商明确上报 0 时写入。
- **聚合 token**：`total_tokens` 类键只作完整性参考（现状 :35 已豁免），不参与费用。

### 2.3 estimated / reported / billed / unknown 语义

| 状态 | 含义 | 来源 | UI 呈现 |
| --- | --- | --- | --- |
| `estimated`（usage_source=ADAPTER_ESTIMATED） | 本地按生效价格版本推导 | `model_costs.py` 现行逻辑 | 金额 + "估算值不等于供应商账单"（现状 :203,211-213 文案） |
| `reported`（PROVIDER_REPORTED） | 供应商响应内联返回的用量 | 适配器 usage | 直接展示数量；金额仍为估算 |
| `billed`（OPERATOR_BILLED） | 运营者对账后录入的账单事实 | 新 reconciliation 表（§2.5） | 金额标注"账单"，永不与估算合并显示 |
| `unknown` | 无 usage 或无价格版本 | usage NULL / 价格缺失 | 显示"未知"，禁止 0（本契约红线） |

- 现状三态 `AVAILABLE/PARTIAL/UNAVAILABLE` 是**费用可估算性**；新增的 `usage_status/usage_source` 是**计量完整性/来源**。两者正交，API 同时输出。

### 2.4 延迟、重试、取消与终态失败

- 延迟：`duration_ms` 已有（:503，finalize 计算 :103-105）；连接级延迟另有 `ModelProbe/ProviderConnection.latency_ms`，账本不重复存储。
- 重试：每次尝试一行（`dispatch_no` 递增），`route_switched` 区分换 Key 替代派发（`provider.py:248-276`）——**重试成本已逐次保留，禁止任何"只留最终成功"的合并视图**；聚合接口按 attempt 粒度输出并在 UI 注明次数。
- 取消与终态失败：`outcome=NULL`（崩溃/未知）在聚合中单列"未决"，不计入费用也不显示 0；`outcome=FAILED` 行保留 usage（供应商可能已计量）。
- 修复链（attempt 2+）通过 `job_attempt` 区分调度尝试，血缘（V02-03A §7）提供父候选追溯。

### 2.5 对账（billed）设计

```text
ProviderUsageReconciliation
  id, provider, model_id, channel, connection_id,
  billing_account_id, import_batch_id, idempotency_key,
  period_start / period_end,
  currency, billed_amount Numeric(20,8),
  source_note (脱敏, ≤500 字符),
  entered_by / created_at
  UNIQUE (billing_account_id, import_batch_id, idempotency_key)
```

- 手工录入，不自动抓取账单。相同 billing account + provider/model/channel 的周期不得重叠：PostgreSQL 用 exclusion constraint，SQLite 由同事务区间查询与串行化测试保证。任意查询区间首发只纳入完整账期，不做隐式分摊。
- 真实账单核对未运行前，一切 billed 数据不存在，账本输出恒为 estimated/unknown——**标记 NOT RUN**（§8）。

### 2.6 通道统一边界（HTTP API 与 CLI）

- 账本写入只经唯一服务层入口（现 `begin/finalize_model_call_attempt` 的扩展）；CLI 通道（V02-12 后）必须复用同一入口与同一 `channel` 枚举，禁止旁路文件日志或前端本地账本。
- 读取端：HTTP API 提供 job/项目/区间聚合；CLI 只消费同一 API，不直连数据库。
- 计费性探测（图片能力测试）纳入账本：现有 `create_probe`（`providers.py:374-386`）的付费调用补建 attempt 行（`probe_id` 关联），修复"探测费用不可见"缺口。

### 2.7 脱敏边界

- 现状已保证：审计行是"redacted"设计（`models.py:443-447` docstring），`error_message` 截断 500（`model_call_audit.py:113`），调用方传固定脱敏文案、原始驱动错误只进异常链（`worker_handlers/provider.py:134-156,181-185`）。
- 契约扩展：`request_parameters`/prompt 全文**禁止进入账本表**（现已在 `GenerationRecord.prompt_checksum`+模板版本 :427-429 的抽象之下，维持不变）；`ProviderUsageReconciliation.source_note` 只允许运营者手写摘要；任何 provider 凭据、Key 提示、`reference_selections` 用户内容不得写入新增列。
- 保留现有检查：账本相关端点不回显凭据路径/密钥（`test_vertex_health_and_settings.py:220` 语义延伸）。

### 2.8 幂等、唯一键、并发与旧 Worker

- 幂等：`dispatch_no=MAX+1` 只是序号分配，不是业务幂等。begin 必须接收稳定 `dispatch_request_id` 并建立唯一约束；相同派发重放返回既有 attempt。对账 POST 必须携带 idempotency_key。SQLite 使用 `ON CONFLICT DO NOTHING`/显式查询，PostgreSQL 使用对应 dialect；禁止把 `INSERT OR IGNORE` 写成跨库契约。
- 并发：begin/finalize 的独立会话事务（`model_call_audit.py:47,93`）保持；finalize 是**按 attempt_id 的单行 UPDATE**，无读改写竞态；dispatch_no 分配依赖唯一约束失败重取（现状注释 :42-44）。
- 旧 Worker：全部新增列可空，旧 finalize 不写新列 → `usage_status` 由回填/读取端推导，行为向后兼容（docstring :87-91 已定义"未提供不写入"语义）。

### 2.9 迁移、回填、回滚与保留

- 迁移（V02-15 实现 PR）：①新增 attempt 列（全部 NULL 允许）；②新增 `channel`（`server_default="HTTP_API"`）与维度列；③新增 `ProviderUsageReconciliation` 表与价格版本 `cached_input_tokens_per_million` 列。禁止改动既有列语义。
- 回填：现有 attempt.usage 来自适配器透传供应商响应，能解析的行写 `usage_source=PROVIDER_REPORTED`；只有明确由本地规则推算的字段才是 ADAPTER_ESTIMATED。无法判断来源时标 UNKNOWN，不得统一降格为 estimated。回填按 attempt id + 内容 checksum 幂等。
- 回滚：down 分支仅删除新增列/表；已回填数据随列删除，不保留影子表（单用户产品可接受，写明）。
- 保留策略：单用户产品默认**永久保留**账本；如需清理仅允许按归档导出后整批删除 attempt 行，禁止静默过期。

### 2.10 聚合、索引与看板消费接口

- 既有索引已覆盖 job/outcome/catalog/project 维度（:458-461）；新增 `ix_model_call_attempts_channel_started (channel, started_at)` 与维度列过滤索引按实际查询确认。
- 分页：keyset（`started_at, id`）游标，禁止大偏移。
- 新端点（V02-15 实现，设计签名）：
  - `GET /api/v1/usage/attempts?project_id&job_id&channel&since&until&cursor`（attempt 粒度分页）
  - `GET /api/v1/usage/summary?project_id|period`（按 provider/model/channel/日聚合：次数、token、图片、estimated 金额、usage_status 分布、billed 对比）
  - `POST /api/v1/usage/reconciliations`、`GET .../reconciliations`（管理端）
- 看板契约：延续 `jobs-section.tsx:20-31` 的三态渲染 + 强制免责注记；新增"unknown ≠ 0"断言进 UI 测试（§7 U 组）。

## 3. 测试矩阵（可拆为 V02-15 实现 / V02-16 UI）

### V02-15 账本实现

| # | 层 | 场景 | 环境 |
| --- | --- | --- | --- |
| T1 | 单元 | usage 归一：三家族别名 + 缓存键 + 未知键 → usage_status/单位正确；缺失 = NULL 非 0 | 隔离 SQLite |
| T2 | 单元 | `_estimate_attempt` 扩展：缓存价拆分、缺缓存价降级 PARTIAL、`request_each` 组合 | 隔离 SQLite |
| T3 | 服务 | begin/finalize 并发：唯一键冲突重取 dispatch_no；finalize 单行语义 | 隔离 SQLite |
| T4 | 服务 | 重试链：attempt 2+、route_switched 全部逐行保留，聚合不分摊合并 | 隔离 SQLite |
| T5 | 服务 | 探测计费入账：图片能力测试产生 attempt 行且带 probe 关联 | 隔离 SQLite |
| T6 | 服务 | 维度列解析：PAGE/ASSET/STYLE 等 target_type → chapter/page/panel/candidate 正确，未知名单 NULL | 隔离 SQLite |
| T7 | 迁移 | 升级/回滚往返；回填幂等（二次运行零差异）；旧 JSON 无法解析键保留并 PARTIAL | Alembic + 真实 PostgreSQL（NOT RUN 边界） |
| T8 | API | attempts/summary 分页 keyset、过滤组合、空结果语义 | 隔离 SQLite |
| T9 | API | 对账 CRUD：区间重叠 409、币种独立、billed 永不并入 estimated 金额 | 隔离 SQLite |
| T10 | 并发 | 双 Worker 同时 finalize 同一 attempt / 同时 begin 同一 (job,attempt) | 真实 PostgreSQL + 双进程（NOT RUN 边界） |

### V02-16 看板 UI

| # | 层 | 场景 | 环境 |
| --- | --- | --- | --- |
| U1 | 组件 | 三态 + unknown 渲染：缺失计量显示"未知"，**任何路径不显示 0** | Vitest |
| U2 | 组件 | billed 与 estimated 并排、永不相加；免责注记常驻 | Vitest |
| U3 | 组件 | attempt 粒度展开：重试次数、route_switched 标记、缓存命中展示 | Vitest |
| U4 | 组件 | 分页游标加载与空态 | Vitest |
| U5 | E2E | 任务列表 → 用情明细 → 对账入口全链 | 浏览器 E2E（NOT RUN 边界） |

## 4. 未验证边界（NOT RUN）

1. **真实供应商账单核对未运行**：`billed` 通路、费率与实际计费单位（含缓存折扣、图片按张计价假设）未经任何真实账单验证；本轮未调用任何供应商。
2. 真实 PostgreSQL 的迁移往返与双 Worker 并发（T7/T10）未运行；隔离 SQLite 结果不替代。
3. CLI 通道未存在，`channel=CLI` 行为仅为契约预留，未验证。
4. OpenAI 兼容图片端点的 usage 返回形态（`compatible.py:491` 路径）未经真实供应商响应验证。
5. 浏览器 E2E 与性能门禁未运行（`architecture.md:131`）。
6. 本契约未获组长最终批准，不构成 V02-15/V02-16 的实现授权。
