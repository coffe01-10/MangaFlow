# V02-12A 可展示模型服务端持久化契约设计

- 任务：Issue `[0.2.0][V02-12A] Design the persistent creator-model visibility contract`
- 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（分支 `codex/v02-12a-model-visibility-contract`）
- 任务性质：L3 设计草案（DeepSeek 起草，不拥有最终批准权；最终方案由 lead 集中复审确认）
- 契约复审输入：已合并的 V02-02 审计 `5ac0383:docs/v02-provider-neutrality-audit.md`、V02-11 前置审计 `2ab048f:docs/v02-provider-settings-ui-audit.md`
- 约束：本文件只冻结设计契约，**不实现代码、不写迁移、不编辑 `docs/roadmap.md` / `docs/development-progress.md`**
- 修订记录：（由 lead 接管收口时填写）

---

## 1. 执行摘要与可验证定义

### 1.1 执行摘要

「可展示模型」是用户在设置页建立的**展示偏好**：某些模型不希望在创作界面（生成台、工作流、项目默认模型选择器）中作为选项出现。V02-12 的硬性红线是：该偏好必须服务端持久化、独立于 `AIModel.enabled`（可调用性）和派生 `/models.enabled`（可用性），且隐藏不改变模型的可调用性、自动路由、历史审计与重放信息。

现状盘点（基线 `2613d97`）：

- `AIModel.enabled`（`models.py:976`）是**可调用性**：模型、连接、供应商三者 enabled 才可用（`model_availability.py:28-32`），自动路由资格另要求 `confidence == "VERIFIED"` 且连接 `HEALTHY`（`model_router.py:223-226`）。
- `GET /models` 的 `ModelCapability.enabled`（`routes/models.py:62`）是**派生可用性**，由 `catalog_model_is_available` 计算，非持久偏好。
- 前端三个消费点（`generate-section.tsx`、`workflow-studio.tsx:205-206`、`projects/[id]/settings/page.tsx:107`）目前统一用 `ModelCapability.enabled` 过滤模型列表——它们是 V02-12C 要改为「可用性 && 展示偏好」的下游。
- 设置页 `provider-management.tsx` 的显隐控件当前必须呈禁用态（V02-11 §9.3："展示偏好写入契约未落地时，显隐控件显示禁用说明；不提供只在本地生效的假保存，不写 `AIModel.enabled`"）。本契约落地后由 V02-12C 启用。

本设计把展示偏好定义为一个**全局、每模型一条、二元**的持久字段 `AIModel.display_enabled`（默认显示），并提供单条与批量管理 API、一个管理读端点，以及下游消费过滤规则。**后端路由、可用性、自动路由、任务创建与审计零改动**——`display_enabled` 只进入 UI 选择器过滤与设置页管理写入。

### 1.2 可验证定义（红线，自动化断言）

以下五条每条都可由自动测试判定，全部通过即视为 V02-12 契约成立：

1. **偏好独立持久**：`display_enabled` 是 `ai_models` 表的持久列，取值 `true`/`false`，与 `AIModel.enabled` 相互独立。判定：同一模型可同时满足「`display_enabled=false` 且 `enabled=true`」或反之；`GET /models` 的 `ModelCapability.enabled` 与 `display_enabled` 是两个独立字段，`enabled` 派生公式（`catalog_model_is_available`）不读取 `display_enabled`。
2. **隐藏不改可调用性**：隐藏模型仍可被显式 `resolve_model`、被自动路由候选、被任务创建引用；`GenerationRecord`/`ModelCallAttempt` 中的真实 provider/model ID 不因隐藏而改写或消失。判定：M3/M6（§7）覆盖。
3. **隐藏不改历史**：已采用候选、已绑定工作流节点、项目 `default_text_model_id` 指向隐藏模型时引用继续有效，UI 对当前已选模型豁免显示。判定：M7/M8 覆盖。
4. **默认全显示**：既有与新建模型默认 `display_enabled=true`；迁移回填不丢失任何模型、不改动其他列。判定：M1 覆盖。
5. **唯一写入入口**：展示偏好的持久化只经本契约定义的两个端点（单条 `PATCH /providers/models/{id}` 的 `display_enabled`、批量 `PATCH /providers/models/visibility`）；任何地方都不得写回 `AIModel.enabled` 来表达展示偏好。判定：M4/M5 断言写入目标。

---

## 2. 领域语义：三种「enabled」分离

| 概念 | 字段/来源 | 语义 | 写入方 | 影响 |
| --- | --- | --- | --- | --- |
| 可调用性 | `AIModel.enabled`（持久） | 模型是否允许被调用 | 设置页能力管理（`PATCH /providers/models/{id}`）、预设初始化 | 显式选择与自动路由的前提 |
| 可用性 | `ModelCapability.enabled`（派生，`/models`） | 当前是否实际可调用（含连接/供应商/凭据） | 派生，只读 | 后端调度口径，V02-02 已冻结公式，本设计**不修改** |
| **展示偏好** | **`AIModel.display_enabled`（持久，本设计新增）** | 是否出现在创作界面选择器 | 设置页显隐开关 / 批量条（本契约端点） | 仅 UI 过滤；**不影响**可调用性、可用性、自动路由、审计、重放 |

V02-11 §9.2 问题 7（「不可用但仍设置为展示」的模型如何表现）本设计统一如下：

- **创作界面**：过滤条件是 `enabled && display_enabled`。`enabled=false` 的模型即使 `display_enabled=true` 也不出现在选择器（不可调用则展示无意义）。
- **设置页管理视图**：该模型行仍出现（管理员需要看到并测试），标注「未就绪」；「仅显示已隐藏」只按 `display_enabled` 过滤，与可用性正交。

---

## 3. 契约内容

### 3.1 持久位置

**结论：在 `ai_models` 表新增单列 `display_enabled`（Boolean, nullable=False, server_default true）。** 不做独立偏好表。

理由：

- 偏好是**全局、每模型一条、与调用能力正交**的字段；与 `AIModel` 是一对一关系，内嵌列即可表达，无需 JOIN。
- `AIModel` 生命周期已由 `connection_id` CASCADE 管理（`models.py:959-961`），列内嵌则偏好随模型删除自动清理，无孤儿偏好。
- 当前需求无「按项目/按角色覆盖展示偏好」的多键场景；若未来出现，再引入独立偏好表（键为 `model_id + scope`）作为演进路径，不预建。

关键保护（**必须写入实现 Issue 的禁止改动条款**）：

- V02-02 P4 规定 `_ensure_vertex_models` 只对已存在行重写 definition 内 9 个字段（provider_model_id/display_name/model_type/输入输出模态/operations/api_surfaces/capabilities），**不触碰** `enabled`/`confidence`/`priority`/`source`（`v02-provider-neutrality-audit.md:141`）。`display_enabled` 加入同一「不可覆盖」集合：预设初始化重写必须跳过该列，用户展示偏好不被启动同步回退。
- `update_model`（`provider_catalog.py:376-400`）对 PATCH 载荷的 `changes` 用 `setattr` 循环写入——`display_enabled` 必须走**独立更新分支**（见 §3.3），避免误触发能力校验或 source/confidence 重置。

### 3.2 管理列表读模型

**结论：新增只读端点 `GET /providers/connections/{connection_id}/models`，返回 `list[ProviderModelRead]`（含新增 `display_enabled` 字段）。**

理由：

- V02-11 §4 明确设置页模型区「以连接下的管理视图为准」，且需要同时读来源、`last_verified_at`、持久 `enabled`、展示偏好——这些只在 `ProviderModelRead`（`provider_schemas.py:129-153`）上，`ModelCapabilityRead`（`schemas.py:146-165`）缺 source/last_verified_at/持久 enabled。
- 与既有同资源组织对称：`POST /providers/connections/{id}/models`（创建，`providers.py:223`）、`POST /providers/connections/{id}/discover`（发现）已挂在连接下。
- 不扩展 `GET /models` 承载管理字段（那是创作目录，字段已足够、语义应保持），也不把 `models` 数组内嵌进 `GET /providers`（避免使 `ProviderConnectionRead` 与 Dashboard 响应变重）。

路由注意：当前无 `GET /providers/{provider_id}`（`providers.py` 仅 PATCH/DELETE），`GET /providers/connections/{connection_id}/models` 无字面路径冲突；实现时仍保持静态段先于参数段注册的惯例。

前端管理 query：`queryKey: ["connection-models", connectionId]`，按连接展开时请求；与现有 `["providers"]`、`["models"]` 相互独立。

### 3.3 单条/批量 API

**单条**：扩展既有 `PATCH /providers/models/{id}`。

- `ProviderModelUpdate`（`provider_schemas.py:169-182`）新增 `display_enabled: bool | None = None`。`version` 乐观锁沿用。
- **实现注意（L3 陷阱）**：`update_model` 当前无条件执行 `model.source = "MANUAL"; model.confidence = "MANUAL"`（`provider_catalog.py:395-396`）。若 PATCH 仅含 `display_enabled`，会把已验证模型的 `confidence` 打回 `MANUAL`、退出自动路由——这是不可接受的副作用。设计规定：`update_model` 在能力变更处理前弹出 `display_enabled`；若 `display_enabled` 是**唯一**变更字段，则只 `setattr` + `version += 1` + commit，**不重置** source/confidence、不执行 `_validate_protocol_capabilities`；若载荷同时携带能力字段与 `display_enabled`，按既有逻辑处理（重置 MANUAL）。
- `_validate_protocol_capabilities`（`provider_catalog.py:403`）不接收也不校验 `display_enabled`。

**批量**：新增 `PATCH /providers/models/visibility`。

| 项 | 内容 |
| --- | --- |
| 请求 | `ModelVisibilityBatchUpdate: { items: list[{ model_id: str, expected_version: int }]（1..100）, display_enabled: bool }` |
| 响应 | HTTP 200，`ModelVisibilityBatchResult: { updated: list[{ model_id, version }], failed: list[{ model_id, error_code, message, current_version? }] }` |
| 语义 | 幂等：把指定模型的展示偏好统一设为 `display_enabled`；每个模型独立 savepoint 更新，失败不回滚成功项 |
| 乐观锁 | 每项必须携带 `expected_version`。版本不匹配返回该项 `VERSION_CONFLICT` 与 `current_version`，不得覆盖并发修改；若目标值已经相同，仍按幂等成功返回当前版本且不额外递增 |
| 上限 | `model_ids` 1–100；超限 422 |
| 禁止 | 不得接受/写入 `enabled` 或任何能力字段，不得重置 source/confidence |

**部分失败与重试语义（V02-11 §9.2 问题 3 的定案）**：

- 服务端对每个 model_id 独立 `begin_nested()`；失败原因归类：
  - `MODEL_NOT_FOUND`（404）：模型不存在或已被删除。
  - `CONNECTION_MISSING`：模型所属连接已被删除（理论罕见，防御性兜底）。
  - `VERSION_CONFLICT`：`expected_version` 已过期；返回当前版本，前端刷新该行后由用户决定是否重试。
- 单个失败不影响其他项；成功的写入各自独立提交。
- 前端批量条（V02-11 §2.2 E3）：部分失败时保留勾选、逐行展示失败原因、提供「重试」；成功项从勾选移除。不得把部分失败升级为整体失败或静默吞掉。

### 3.4 部分失败

同 §3.3 批量语义。补充契约：**任何批量调用都不做整体事务**——若整体回滚，则「部分失败保留成功项」无法成立，也与 V02-11 §2.2 E3「部分失败保留选择并列出失败行」矛盾。成功项的写入必须是持久的、可独立审计的（version 各自递增）。

### 3.5 默认值

- **列默认**：`display_enabled` 默认 `True`（`models.py` ORM `default=True` + 迁移 `server_default=true`）。
- **新建模型**：discover、手工创建（`create_model`，`provider_catalog.py:340`）、预设种子全部默认显示。
- **既有模型**：迁移回填 `true`（§3.6）。
- **隐藏不改变**（红线 §1.2）：可调用性（`AIModel.enabled`）、派生可用性（`/models.enabled`）、自动路由资格（`confidence==VERIFIED && health_state==HEALTHY`）、任务创建、历史审计与重放全部不受影响。`model_router.py`、`model_availability.py`、`worker_handlers/*` 一行不改。

### 3.6 迁移/回滚

迁移命名：`20260830_20_model_display_preference`（`down_revision = "20260830_19"`，当前 head）。

```python
def upgrade() -> None:
    # 防御：列已存在（如人工修复）则跳过
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns("ai_models")}
    if "display_enabled" not in columns:
        op.add_column(
            "ai_models",
            sa.Column("display_enabled", sa.Boolean(), nullable=False,
                      server_default=sa.true()),
        )

def downgrade() -> None:
    hidden_count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM ai_models WHERE display_enabled = false")
    ).scalar_one()
    if hidden_count:
        raise RuntimeError(
            "refusing downgrade: reset hidden model preferences before removing the column"
        )
    op.drop_column("ai_models", "display_enabled")
```

要点：

- **零数据重写**：仅新增一列 + `server_default=true` 回填，不触碰任何既有列值、不读取凭据、不 import `Settings`（对齐 V02-02 的 M7 禁止条款）。
- SQLite 兼容：新增 `NOT NULL + server_default` 列是 SQLite 直接支持的 `ADD COLUMN` 形式（无需 batch_alter 重建表）；`sa.true()` 与项目既有 `sa.false()` 用法（`20260714_01_revised_mvp_workflow.py:43`）一致。布尔列 `server_default=sa.true()` 对 PostgreSQL 与 SQLite 3.23+ 均有效。
- **回滚语义**：down 前必须确认所有行均为默认 `display_enabled=true`；存在任何隐藏偏好时以明确错误拒绝降级，要求先导出或显式恢复为显示。这样不会把用户配置静默丢弃。该列为 `NOT NULL`，迁移测试同时验证不存在 NULL；不得通过临时填值绕过保护。
- 迁移通过前不编辑（本任务只给设计）；真实 PostgreSQL 升降级验收独立记录 `NOT RUN` 边界（沿用项目既有 PG 边界）。
- 新列**不加索引**：设置页按 `connection_id`（已有 index，`models.py:960`）过滤模型，展示偏好数据量小且不参与筛选谓词。

### 3.7 生成台 / 工作流 / 项目设置消费规则

统一下游消费规则（V02-12C 实现）：

**规则 A（选择器过滤）**：创作界面任一模型选择器（生成台 `ImageModelPicker`、工作流节点模型下拉、项目设置默认模型下拉）的候选集 = `ModelCapability.enabled == true && ModelCapability.display_enabled == true`。过滤只发生在前端消费点；**后端 `GET /models` 始终返回完整目录（含隐藏模型与 `display_enabled` 字段），不预过滤**——否则设置页「显示已隐藏」与「仅已验证」将失去数据源。

**规则 B（当前已选豁免）**：当前已选模型即使 `display_enabled=false` 也**保持显示**（可加「已隐藏」标注），防止「当前值从下拉消失、用户无法改选」：

- 项目设置 `default_text_model_id` / `text_model_alias` 指向的模型；
- 生成台当前页面候选/批次实际使用的模型；
- 工作流节点已绑定的 `model_alias` / `catalog_model_id`。

**规则 C（隐藏不改调度）**：过滤只发生在 UI 选择器；任务创建、自动路由、历史记录读到的真实模型 ID 不因隐藏改变（§3.5）。

**Schema 面**：

- `ModelCapabilityRead`（`schemas.py:146-165`）新增 `display_enabled: bool = True`；`routes/models.py` `list_models` 映射新增 `"display_enabled": model.display_enabled`（`models.py:958-982` 直接读列，无 JOIN）。
- `ProviderModelRead`（`provider_schemas.py:129-153`）新增 `display_enabled: bool`；`ProviderModelUpdate` 新增 `display_enabled: bool | None = None`。
- 前端 `apps/web/lib/api.ts`：`ModelCapability` 与 `ProviderModel` 类型各加 `display_enabled`；新增 `connectionModels(connectionId)`、`updateModelVisibility(modelIds, displayEnabled)` 两个 API wrapper。

**设置页（V02-11 模型行显隐控件，本契约落地后启用）**：

- 行内开关写单条 `PATCH /providers/models/{id}`（`display_enabled` 唯一字段）。
- 批量条写 `PATCH /providers/models/visibility`，范围默认当前连接（V02-11 §4.3 已定「不提供全选全部供应商跨卡批量」）。
- 「显示已隐藏」过滤只按 `display_enabled`；「仅已验证」只按 `confidence`；二者与可用性正交（V02-11 §4.3/§4.4）。
- 三态分离断言（V02-11 §8.4 V4）：分别构造「展示隐藏」「`AIModel.enabled=false`」「派生 `/models.enabled=false`」，断言 UI 与写入目标不混淆。

---

## 4. 变更清单（供 V02-12B/C 引用，本任务不执行）

**后端（V02-12B）**

| 文件 | 变更 |
| --- | --- |
| `apps/api/app/models.py` | `AIModel` 新增 `display_enabled: Mapped[bool] = mapped_column(Boolean, default=True)`；注明预设初始化禁止覆盖 |
| `apps/api/app/provider_schemas.py` | `ProviderModelRead`、`ProviderModelUpdate` 新增 `display_enabled`；新增 `ModelVisibilityBatchUpdate`、`ModelVisibilityBatchResult` |
| `apps/api/app/schemas.py` | `ModelCapabilityRead` 新增 `display_enabled: bool = True` |
| `apps/api/app/api/routes/models.py` | `list_models` 映射新增 `display_enabled` |
| `apps/api/app/api/routes/providers.py` | 新增 `GET /providers/connections/{connection_id}/models`、`PATCH /providers/models/visibility` |
| `apps/api/app/services/provider_catalog.py` | 新增 `list_models_for_connection`、`set_model_visibility_bulk`；`update_model` 增加 display_enabled 独立分支（不重置 source/confidence） |
| `apps/api/app/services/provider_presets.py` | `_ensure_vertex_models` 覆盖白名单明确排除 `display_enabled`（实现时核对 P4 范围） |
| `apps/api/migrations/versions/20260830_20_model_display_preference.py` | 新增迁移（§3.6） |

**前端（V02-12C）**

| 文件 | 变更 |
| --- | --- |
| `apps/web/lib/api.ts` | 类型加 `display_enabled`；新增 `connectionModels`、`updateModelVisibility` |
| `apps/web/components/provider-management.tsx` | 启用模型行显隐开关与批量条；「显示已隐藏」「未就绪」文案 |
| `apps/web/components/project-workspace/generate-section.tsx` | 候选过滤加 `display_enabled`（规则 A）+ 当前已选豁免（规则 B） |
| `apps/web/components/workflow-studio.tsx` | `textModels`/`imageModels` 过滤加 `display_enabled`（规则 A） |
| `apps/web/app/projects/[id]/settings/page.tsx` | 文本路由下拉过滤加 `display_enabled` + 当前已选豁免（规则 A/B） |

**文档**

- V02-12B 合并时同步 `docs/data-model.md`（§5 供应商能力示例、§3 实体说明）与 `docs/provider-platform.md`；本设计文档在实现与验收后由 lead 收口归档。

---

## 5. 与 V02-02 / V02-11 的衔接

- **V02-02（`5ac0383`）已冻结且本设计不触碰**：可用性公式（`model_availability.py:28-32`）、自动路由资格（`model_router.py:223-226`）、`credential_source` 派生、`auto_enable_pending` 状态机、Phase C 迁移（仅可空性）。`display_enabled` 不进入上述任一判定。
- **V02-11（`2ab048f`）待决项收口**：本设计回答了 §9.2 问题 1（持久位置 = `AIModel.display_enabled`）、问题 2（管理读端点 = `GET /providers/connections/{id}/models`）、问题 3（批量端点 + 部分失败响应 = `PATCH /providers/models/visibility`）、问题 7（不可用但展示的模型 = 创作界面不出现、设置页显示「未就绪」）。
- V02-11B 将「接入 V02-12 展示偏好」——其依赖 V02-12B 本契约落地；实现顺序为 V02-12B → V02-12C，V02-11B 等待二者。

路由注册红线：静态 `PATCH /providers/models/visibility` 必须注册在现有动态 `PATCH /providers/models/{model_id}` 之前，否则 FastAPI 会把 `visibility` 当作模型 ID。M5 必须通过真实 ASGI 路由调用覆盖该顺序，不能只测 service。

---

## 6. 边界与非目标

1. **本任务不实现代码/迁移**；设计通过前不编辑迁移文件。
2. **不改后端调度**：`model_router.py`、`model_availability.py`、`worker_handlers/*`、`page_readiness.py` 零改动；`display_enabled` 不进入任何后端判定。
3. **不做按项目/角色的展示偏好**（未来演进为独立偏好表）。
4. **不做隐藏模型的调用禁用**：隐藏 ≠ 不可用 ≠ 不可调用。
5. **不触碰 V02-02 的 Phase A–E 范围**（credential_source、健康统一、默认值平权、Vertex 入口删除），这些由 V02-10A–D 独立推进。
6. **真实 PostgreSQL / 供应商 / 浏览器 E2E 不作为本设计任务验收**；V02-12B/C 各自按 AGENTS.md 记录 `RUN`/`NOT RUN`/`BLOCKED` 边界。

---

## 7. 测试矩阵（供 V02-12B/C 验收引用）

| 编号 | 层 | 场景 | 类型 | 覆盖 |
| --- | --- | --- | --- | --- |
| M1 | 迁移 | 空库与含历史行库 `upgrade → downgrade → upgrade` 往返；`ai_models` 既有行回填 `display_enabled=true`，其他列逐字段不变；`model_call_attempts`/`generation_jobs` 等既有表结构不动 | 新增 pytest（迁移往返，沿用 `20260827_17` 风格） | B |
| M2 | 迁移 | 全部为 true 时 downgrade 后列消失、upgrade 后恢复且默认 true；存在任一 false 时 downgrade 明确拒绝且数据/列保持完整 | 新增 pytest | B |
| M3 | 后端 | 隐藏模型仍可调用：`display_enabled=false` 的模型在 `resolve_model` 显式选择、`/models` 可用性、自动路由候选、任务创建中与隐藏前行为一致；`GenerationRecord`/`ModelCallAttempt` 真实 provider/model ID 不改写 | 新增 pytest（扩展 `test_multi_provider_platform`/`test_model_call_audit`） | B |
| M4 | 后端 | 单条 PATCH 仅含 `display_enabled`：不重置 source/confidence，`VERIFIED` 保持自动路由资格；version 递增；与能力字段混合时按既有逻辑重置 MANUAL | 新增 pytest | B |
| M5 | 后端 | 批量端点：全成功；部分失败（含 `MODEL_NOT_FOUND`、`CONNECTION_MISSING`、`VERSION_CONFLICT`）返回 `updated`/`failed` 明细且成功项已提交；items 空或 >100 返回 422；相同目标值重试不递增 version；过期 version 不覆盖；载荷含 `enabled`/能力字段被拒绝（extra forbid）；ASGI 测试确认静态 visibility 路由未被 `{model_id}` 截获 | 新增 pytest | B |
| M6 | 后端 | 独立端点 `GET /providers/connections/{id}/models`：返回来源、confidence、last_verified_at、enabled、display_enabled；connection 不存在 404 | 新增 pytest | B |
| M7 | 前端 | 生成台/工作流/项目设置过滤：`enabled && display_enabled` 才出现；当前已选隐藏模型豁免显示并可改选 | 修改 `generate-section.test.tsx`、`workflow-studio.test.tsx`、项目设置测试 | C |
| M8 | 前端 | 三态分离（V02-11 V4）：分别构造展示隐藏、`AIModel.enabled=false`、派生 `/models.enabled=false`，断言 UI 与写入目标不混淆 | 修改 `provider-management.test.tsx` | C |
| M9 | 前端 | 批量显隐：勾选当前可见行 → 批量端点；部分失败保留勾选并列出失败行；「显示已隐藏」只按 `display_enabled` | 新增组件测试 | C |
| M10 | 门禁 | `npm run check` 全绿（V02-12B/C 各自独立）；`git diff --check` 通过 | 回归 | B/C |

---

## 8. 实现切片建议（供 lead 派工，本任务不执行）

1. **V02-12B（L3 后端，GLM 优先）**：迁移 + ORM/多 schema 字段 + 管理读端点 + 单条/批量 API + `update_model` 独立分支 + M1–M6 回归。独立 PR，不夹带其他重构。
2. **V02-12C（L2 前端）**：设置页显隐开关与批量条启用 + 三个消费点过滤 + 「显示已隐藏」/「未就绪」文案 + M7–M9 回归。依赖 V02-12B。
3. 每片独立审阅；真实 PostgreSQL 升降级（M1 的 PG 变体）按项目既有边界记录 `NOT RUN`，SQLite 覆盖不替代。

---

## 9. 开放问题（需 lead 复审时裁定）

1. **隐藏模型与自动路由**：本设计默认「隐藏不影响自动路由候选」（隐藏是纯 UI 偏好）。若产品希望「隐藏的模型也从自动路由候选排除」，需要额外决策并在 `model_router.py` 增加过滤——这将改变 V02-02 冻结的自动路由口径，需 lead 明确；默认维持「不影响」。
2. **批量端点命名与形态**：本设计选 `PATCH /providers/models/visibility`（200 + `updated`/`failed`）。若 lead 偏好 207 Multi-Status 或 `POST .../bulk`，仅属端点形态调整，不改变持久位置与语义。
3. **设置页「显示已隐藏」的入口深度**：V02-11 设计为连接内开关；若 lead 认为应为平台级开关，属前端布局决策，不改变契约。
