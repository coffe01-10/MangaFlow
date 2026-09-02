# V02-22A 角色模型包（Character Model Package）数据与生成契约

- 任务：Issue #78 / `[0.2.0][V02-22A] Freeze the character model package data contract`
- 基线提交：`7c10d27`（PR #77 / V02-21B 合并提交，`origin/master`）
- 工作分支：`glm/v02-22a-character-package-contract`（worktree `D:\自媒体\漫画工作流-glm-v02-22a`）
- 任务性质：L3 数据契约设计草案（GLM 起草，不拥有最终批准权；最终方案由组长集中复审确认）
- 约束：本文件只冻结设计契约，**不实现代码、迁移、API、Worker、前端；不编辑 `docs/roadmap.md` / `docs/development-progress.md`**；不调用真实供应商。
- 上游输入：`docs/roadmap.md`（M2：V02-22 / V02-23）、`docs/v02-character-model-package-ui-audit.md`（V02-23A，仅作 UI 侧输入，其建议字段一律重新核对，不视为已批准 schema）、`docs/v02-scene-asset-contract.md`（版本/快照/租约/迁移写法参照）。
- 修订记录：（由组长接管收口时填写）

---

## 1. 执行摘要

本契约把"角色参考资产"升级为**角色模型包**：在既有 `Character` 之上叠加一个一对一的 `CharacterModelPackage`，包内通过 `CharacterModelPackageVersion` 表达不可变版本。每个版本冻结三份规格（身份锚点 `identity_spec`、视觉规格 `visual_spec`、负面约束 `negative_constraints`）与两类关系（版本参考图矩阵、版本服装集）。`Character.id` 继续作为分镜、对白、服装、生成任务与历史候选的**唯一事实锚点**；模型包不替换、不重编号任何既有实体。

核心红线与 V02-20A 场景资产契约一致：

1. **升级零破坏**。迁移为每个既有 Character 建立兼容包与 V1 **草稿**快照，`published_version_id` 置 NULL；所有既有生成路径（`validate_candidate_reference_selections` → `prompt_snapshot` → Worker）在未发布版本时行为逐字段不变。启用模型包（发布版本）是显式用户动作，不是迁移副作用。
2. **生成版本锁定发生在排队边界**。候选排队时把所选版本的 ID、版本号、规格冻结摘要与实际参考图/服装 Asset ID 写入 `PageCandidate.prompt_snapshot`；Worker 只消费排队快照，不读取"最新版本"。发布新版本不改变历史候选、重试或重放。
3. **完整度是建议指标**。分数由服务端在读取路径确定性计算、不落库、不进入 `ensure_page_ready` 生产门禁；历史 Character 自动 grandfather。
4. **参考图与服装关系用关系表，不用 JSON 数组**。理由与比较见 §4.3；这是与既有 `Outfit.reference_asset_ids`（JSON）和 UI 审计建议（`multi_view_assets`/`assigned_outfit_ids` JSON 字典）的明确分叉。

---

## 2. 兼容锚点（冻结）

| # | 冻结决策 | 依据（`7c10d27` 当前代码） |
| --- | --- | --- |
| A1 | `Character.id` 继续是所有既有 API、Scene/Panel 出镜、`Outfit.character_id`、`CharacterReference.character_id`、生成任务与历史候选的事实锚点。模型包所有公开 API 以 `character_id` 寻址，包自身 `id` 不进入任何既有 URL 或分镜结构。 | `models.py:139-153`；`Panel.characters`/`character_presence`（`models.py:422-423`）；`Dialogue.speaker_character_id`（`models.py:444`） |
| A2 | `CharacterModelPackage` 与 Character 一对一（`character_id` 数据库级唯一），不替换、不重编号 Character。 | 新增约束，见 §4.1 |
| A3 | 既有 `CharacterReference`、`Outfit`、`StyleProfile`、`Asset` 行及其 ID **不改写、不复制实体**。版本关系表是新关系层，引用同一批 Asset/Outfit ID（与 `SceneAssetReference` 引用既有 `Asset` 同范式）；"不复制"指不新建影子 Asset/Outfit 记录、不改写任何既有行，而非禁止新建指向既有 ID 的关系行（§4.3 与 §6.2 给出论证）。 | `models.py:967-978`、`models.py:156-171`、`migrations/versions/20260901_24_scene_assets.py` |
| A4 | `StyleProfile` 保持项目级资产。模型包**不建立**与 StyleProfile 的任何持久关联（§4.5 冻结为"否决"）；StyleProfile 只能在生成快照中被记录事实，不能被角色包私有化。 | `models.py:174-185`；`prompt_compiler.py:136-145` |
| A5 | **旧客户端兼容行为**：所有包端点为新增路由，既有路由的请求/响应结构不变（`CharacterRead`/`OutfitRead`/`PageCandidateRead` 等不因本契约改变既有字段语义；`CandidateCreate.reference_selections` 只新增可选键）。旧前端完全不感知包存在时：不调用任何包端点 → 无包或包未发布 → 生成校验、快照、门禁与今天完全一致。新建 Character **不**自动建包（§6.2）。 | `schemas.py:419-497,711-715`；`ordinal_allocator.py:243-303` |
| A6 | `Character` 现无软删除（`models.py:139-153` 无 `deleted_at`，`characters.py` 无 DELETE 路由）；包随 Character 级联（FK `CASCADE`），不引入新的删除路径。 | `characters.py:49-197` |

---

## 3. 现状核对：当前代码事实与 UI 审计输入

以下事实在 `7c10d27` 上逐条核实，作为后续冻结的基础，并标注 UI 审计（V02-23A）建议与后端事实的差异。

### 3.1 生成输入与排队快照（当前真实路径）

1. 候选创建：`create_page_candidate`（`ordinal_allocator.py:306-428`）要求 `payload.storyboard_version == page.storyboard_version`，逐出镜角色校验 `reference_selections`：`character_asset_id` 必须存在 `CharacterReference(character_id, asset_id)` 行；服装取 `selection.outfit_id` 或分镜 `panel.outfits[char_id]`（两者冲突 409）；服装必须属于该角色与项目、且 `outfit.reference_asset_ids` 非空、`outfit_asset_id` 必须在其中有值（`ordinal_allocator.py:243-303`）。
2. 排队快照：`prompt_snapshot = {"reference_selections": ..., "storyboard_version": ..., "scene_asset": ...}`；`create_job` 同事务写入 `JobAssetReference`（`job_service.py:88-92`），租约集合 = 全部选中人物参考图 + 服装参考图 + 场景参考图（`ordinal_allocator.py:399-410`）。幂等键 `candidate:{candidate.id}`。
3. Worker 消费：`_run_page_generate`（`page_generate.py:275-486`）按排队快照加载参考图，校验"分镜版本未变、人物参考绑定仍在、服装参考仍在 `outfit.reference_asset_ids`"，付费调用前 `_lease_reference_assets` 并重读全部租约行；`GenerationRecord.input_versions = {"page", "page_revision", "storyboard", "scene_asset"}`（`page_generate.py:461-467`）。
4. **当前缺口（本契约要补的事实）**：提示词的人物档案（character bible）在 Worker 执行时从**当前** `Character` 行现场编译（`prompt_compiler.py:26-45,106-146`），不含任何版本概念；排队与执行之间编辑角色描述会漂移。风格参考图也在执行时读取（`page_generate.py:177-191`），未参与排队租约——本契约不改这一既有行为（§12 待批准问题 #2）。

### 3.2 生产门禁（必须 grandfather 的既有条件）

`build_page_readiness`（`page_readiness.py:187-360`）对每个出镜角色要求：≥1 张未软删的人物参考图（`MISSING_CHARACTER_REFERENCE`）、已指定本页服装（`MISSING_OUTFIT_ASSIGNMENT`，来自 `panel.outfits`）、该服装 ≥1 张未软删参考图（`MISSING_OUTFIT_REFERENCE`）；另有风格与模型/Worker 条件。`ensure_page_ready` 以 409 阻断候选创建。**完整度分数不进入该函数**（§7）。

### 3.3 与 UI 审计（V02-23A）建议的核对结论

| UI 审计建议 | 核对结论 |
| --- | --- |
| `PackageVersion.multi_view_assets` / `expression_assets`（JSON 字典）、`assigned_outfit_ids`（JSON 数组） | **否决**，改为关系表（§4.3 比较）。 |
| `PackageVersion.completeness_score` 落库 | **否决**落库，改为读取路径确定性计算（§7.4）。 |
| 包含 `name`、`aliases` 字段 | **否决**。`Character.primary_name`/`aliases` 已存在且被别名冲突逻辑维护（`characters.py:19-40`）；包上复制会产生同步漂移，UI 直接读 Character。 |
| `is_locked` 布尔 | **否决**。锁定语义由版本状态机（`status` ≥ READY 即不可变）表达，布尔位与状态冗余。 |
| 状态 `DRAFT/READY/IN_PRODUCTION/ARCHIVED` | **采纳**为版本状态名（§5.2），但语义按 §5 冻结（READY=已发布且冻结，与审计"发布后 READY"一致）。 |
| 完整度 20/40/20/20 | **采纳块结构、修订子项**（§7.3）：表情改为按带标签槽位计分，服装区分"已绑定"与"已设默认"。 |
| `GET .../characters/{pkg_id}/diff` | **修订寻址**：以 `character_id` 寻址（锚点 A1），不用独立 pkg_id（§9）。 |
| `generate-views` / `generate-expressions` | 只冻结请求/费用确认/模型能力/任务追踪契约（§9.4），本任务不实现、不调用。 |
| 审计假设"升级后历史 Character 自动有可用包" | **修订**：升级只建包 + V1 草稿，不发布；发布前生成路径不变（§6.2）。 |

### 3.4 既有枚举与命名对齐

- `AssetCandidateCreate.variant` 已支持 CHARACTER 目标 `FRONT|SIDE|BACK|EXPRESSION|SHEET`（`schemas.py:753-759`、`ordinal_allocator.py:476-482`）；本契约视图/表情角色（小写 `front/side/back/three_quarter/expression/pose`）与该枚举一一映射（大写↔小写），`generate-views` 复用既有 ASSET_GENERATE 通道。
- `CharacterReference.angle` 是自由 `String(32)`（默认 `"unspecified"`，`models.py:976`），数据库层无角色约束；本契约版本参考图关系引入受控角色枚举，并通过迁移映射历史 angle 值（§6.4）。
- `CharacterReference.asset_id` 全局唯一（`uq_character_reference_asset`，`models.py:969`）：一张 Asset 至多属于一个角色。版本参考图关系沿用"同一 Asset 至多服务一个角色"的隐含前提，但不复制该全局唯一约束（同一 Asset 可在同一角色的同一版本不同槽出现，如 front+cover；§4.3）。**换绑守卫**：既有 `bind_reference`（`characters.py:156-185`）在素材被换绑到另一角色时会删除旧 `CharacterReference` 行——若该素材同时被任何其他角色的包版本关系引用，跨角色换绑将造成"一张图服务两个角色"的身份混合；冻结的守卫见 §10.3。

---

## 4. 数据模型（冻结）

新增四张表，命名与字段如下。所有实体主键 `String(36)` UUID；`created_at/updated_at/version` 沿用 `Timestamped` mixin 语义（`models.py:53-58`，`version` 为整数乐观锁）；关系表对齐 `SceneAssetReference` 范式只带 `created_at`。SQLite 与 PostgreSQL 使用**相同 DDL**（JSON 列为不透明载荷，不参与筛选谓词；部分唯一索引同时声明 `postgresql_where` / `sqlite_where`，与 `20260901_24` 一致）。

### 4.1 `character_model_packages`（包本体）

| 字段 | 类型/约束 | 说明 |
| --- | --- | --- |
| `id` | String(36) PK | |
| `character_id` | String(36) FK `characters.id` ON DELETE **CASCADE**，**UNIQUE** | 一对一锚点（A2）。CASCADE 与 `CharacterReference`/`Outfit` 同风格（A6）。 |
| `project_id` | String(36) FK `projects.id` ON DELETE CASCADE，index | 刻意冗余：服务列表筛选索引 `ix_character_model_packages_project_status_created (project_id, status, created_at)` 与项目作用域校验；服务端不变式 `package.project_id == character.project_id`，违反 409。 |
| `identity_spec` | JSON NOT NULL default `{}` | **包级可编辑工作集**（仅存在 DRAFT 时可改，§5）。键集合固定：`age_appearance`(≤120)、`gender`(≤32)、`personality`(≤800)、`identity_notes`(≤2000)，全部可选字符串；未知键拒绝（Pydantic `extra="forbid"`）。不复制 `primary_name`/`aliases`（§3.3）。 |
| `visual_spec` | JSON NOT NULL default `{}` | 同上，键集合固定：`hair`、`hair_color`、`face`、`eyes`、`body`、`distinguishing_marks`，全部可选字符串（各 ≤400）。 |
| `negative_constraints` | JSON NOT NULL default `[]` | 字符串数组，≤20 项、每项 ≤120 字符。 |
| `published_version_id` | String(36) FK `character_model_package_versions.id` ON DELETE **SET NULL**，nullable | **唯一的"当前发布版本"指针**。SET NULL 与 `Scene.scene_asset_id` 同理由：目标消失时回退旧行为而非悬挂；正常路径版本不可物理删除（§10），FK 不会触发。 |
| `status` | String(16) CHECK IN (`ACTIVE`, `ARCHIVED`) default `ACTIVE` | 包生命周期。ARCHIVED 排除出默认继承（§8.1），不影响 Character。 |
| `created_at` / `updated_at` / `version` | Timestamped | `version` 乐观锁用于包级草稿规格编辑。 |

索引：UNIQUE(`character_id`)；`ix_character_model_packages_project_status_created (project_id, status, created_at)`。

**"当前/发布版本指针"的冻结解释**：任务输入要求"current/published version 指针"。本契约冻结为**单一指针** `published_version_id`——"当前版本"即该指针；草稿通过 `status='DRAFT'` 查询获得（每包至多一个，部分唯一索引保证，查询廉价）。第二个指针列被否决为冗余（§4.6）。

### 4.2 `character_model_package_versions`（不可变版本）

| 字段 | 类型/约束 | 说明 |
| --- | --- | --- |
| `id` | String(36) PK | |
| `package_id` | String(36) FK `character_model_packages.id` ON DELETE CASCADE，index | |
| `version_number` | Integer NOT NULL，**UNIQUE(`package_id`, `version_number`)** | 从 1 起单调递增；展示标签由服务端派生为 `V{version_number}`（如 V1、V2），**不存** "1.0" 式字符串（UI 审计的 `version_number: "1.0"` 否决：两字段漂移风险）。 |
| `status` | String(16) CHECK IN (`DRAFT`, `READY`, `IN_PRODUCTION`, `ARCHIVED`) default `DRAFT` | 语义见 §5.2。 |
| `spec_snapshot` | JSON NOT NULL default `{}` | **发布时冻结的规格副本**：`{"identity_spec": {...}, "visual_spec": {...}, "negative_constraints": [...], "frozen_from": "package" \| "derive" \| "migration"}`。DRAFT 期间保存派生来源副本（发布时被包工作集覆盖）；READY 后不可变。生成、diff、重放一律读本字段。 |
| `derived_from_version_id` | String(36) FK `character_model_package_versions.id` ON DELETE SET NULL，nullable | 派生来源；V1/迁移版本为 NULL。 |
| `published_at` | DateTime(timezone=True) nullable | 发布时间；READY/IN_PRODUCTION 必有值，DRAFT/ARCHIVED 保留历史值（归档不清除）。 |
| `created_at` / `updated_at` / `version` | Timestamped | `version` 乐观锁：DRAFT 的关系绑定操作要求携带该字段。 |

索引：UNIQUE(`package_id`, `version_number`)；`ix_character_model_package_versions_package_status (package_id, status)`；部分唯一索引 `uq_character_model_package_versions_one_draft` ON (`package_id`) `WHERE status = 'DRAFT'`（每包至多一个 DRAFT，两库同语法）。

### 4.3 版本参考图关系 `character_model_package_version_references`（关系表方案，冻结）

| 字段 | 类型/约束 | 说明 |
| --- | --- | --- |
| `id` | String(36) PK | |
| `version_id` | String(36) FK `character_model_package_versions.id` ON DELETE CASCADE，index | |
| `asset_id` | String(36) FK `assets.id` ON DELETE **RESTRICT**，index | 引用既有 Asset，不复制文件或记录（A3）。 |
| `role` | String(32) NOT NULL CHECK IN (`cover`, `front`, `side`, `back`, `three_quarter`, `expression`, `pose`, `extra`) | 核心五槽 + 表情/姿势/扩展。小写，对齐 `SceneAssetReference.role="main"` 风格。 |
| `label` | String(48) NOT NULL default `""` | `expression`/`pose`/`extra` 必填非空（如 `neutral`/`joy`/`anger`）；核心五槽必须为空串。 |
| `sort_order` | Integer NOT NULL default 0 | 表情/姿势展示顺序（0–1000）。 |
| `created_at` | datetime | 无 `version` 乐观锁：草稿期关系变更由版本级 `version` 字段守卫（§5.4）。无 `deleted_at`：解绑即物理删除（仅 DRAFT 允许，§10.1）。 |

约束与索引：
- CHECK 约束 `ck_character_model_package_version_reference_role_label`：`(role IN ('cover','front','side','back','three_quarter') AND label = '') OR (role IN ('expression','pose','extra') AND label <> '')`——核心五槽禁止标签，表情/姿势/扩展必须有标签。
- `uq_character_model_package_version_reference_slot UNIQUE(version_id, role, label)`——**逻辑槽唯一（不含 `asset_id`）**：同一逻辑槽（如 `expression/joy`）无论绑定哪张 Asset，至多一个活跃行；换绑（同槽换图）必须先解绑再绑定，保证基于 `(role, label)` 的矩阵寻址与 diff 契约无歧义。同一 Asset 占不同槽（front+cover）仍合法。
- 表情/姿势按 `(version_id, role, label)` 唯一去重；核心五槽因 label 恒为空串，由同一唯一约束自然保证每版每槽至多一行，**不再需要**额外的 `(version_id, role)` 部分唯一索引。

**关系表 vs JSON 数组/字典的正式比较**（否决 UI 审计的 `multi_view_assets`/`expression_assets`/`assigned_outfit_ids` JSON 方案）：

| 维度 | 关系表（选定） | JSON 数组/字典（否决） |
| --- | --- | --- |
| 外键完整性 | `asset_id`/`outfit_id` FK RESTRICT，数据库阻止悬挂引用；`_detach_reference_asset` 可按 FK 反查解绑（`uploads.py:80-139` 现按 JSON 全项目扫描 Outfit/Style） | 无 FK；Asset 软删后 ID 变成需要每读取方容错的裸字符串；物理删除直接产生悬挂 ID |
| 唯一性 | 核心槽唯一、默认服装唯一用部分唯一索引在**数据库层**封并发竞态（对齐 P2-8 教训） | 只能靠应用层检查，双写并发可产生两个 front 或两个默认服装 |
| 排序/寻址 | `role+label+sort_order` 结构化，矩阵槽位可枚举、diff 可逐槽对比 | 字典键自由增长，无形状契约，UI 与服务端各自解释 |
| 租约集合查询 | `SELECT asset_id FROM ..._references WHERE version_id=?` 一条索引查询 | 需读 JSON 并在 Python 展开，PG 无法用普通索引谓词（data-model.md 明确 JSON 不参与筛选约束） |
| 软删除/失效语义 | 消费方统一按 `Asset.deleted_at` 过滤，关系行保留冻结事实 | 相同过滤逻辑但要先解析 JSON，且无法区分"未填"与"解析失败" |
| 迁移成本 | 多两张表 + 回填 INSERT（§6） | 无新表，但把完整性、唯一性、排序全部推给应用层，后续每次修补都要改读取方 |
| 与仓库先例 | 与 `SceneAssetReference`/`CharacterReference` 同范式；data-model.md 建模原则"需要筛选、约束和追溯的关系使用表" | 仅 `Outfit.reference_asset_ids` 一个先例，且该 JSON 已在 `uploads.py:84-90`、`delete_candidate`（`generation.py:170-186`）造成三处全项目扫描式解绑，是被认证的痛点而非范式 |

### 4.4 版本服装关系 `character_model_package_version_outfits`（关系表方案，冻结）

| 字段 | 类型/约束 | 说明 |
| --- | --- | --- |
| `id` | String(36) PK | |
| `version_id` | String(36) FK `character_model_package_versions.id` ON DELETE CASCADE，index | |
| `outfit_id` | String(36) FK `outfits.id` ON DELETE **RESTRICT**，index | 关联既有 Outfit（A3）。Outfit 存在硬删除端点（`asset_generation.py:184-283`），RESTRICT 使被版本引用的服装不能无感删除；V02-22B 必须在 `delete_outfit` 前置校验并返回 409（§10.4）。 |
| `is_default` | Boolean NOT NULL default False | 部分唯一索引 `uq_character_model_package_version_outfit_default` ON (`version_id`) `WHERE is_default`——**每版至多一个默认服装**，数据库层封并发双设。 |
| `sort_order` | Integer NOT NULL default 0 | 服装展示/组合顺序。 |
| `created_at` | datetime | 同 §4.3，无独立乐观锁、无 `deleted_at`。 |

约束：`uq_character_model_package_version_outfit UNIQUE(version_id, outfit_id)`（同版同服装重复绑定 409）。

**服装参考图不按版本复制（冻结）**：版本关系冻结的是**Outfit ID 集合 + 默认 + 顺序**；`Outfit.reference_asset_ids` 保持服装级事实。理由：把服装参考图复制到每个版本会产生 N×M 关系行与解绑放大（`_detach_reference_asset` 需同时清理全部版本副本）；排队快照已冻结**具体** `outfit_asset_id`，候选不可变性不受服装后续编辑影响（§8.3）；服装自身的演进不属于角色包版本职责。已发布版本内某服装换图，只影响之后的候选——与场景资产"资产修订只影响新生成"的既定语义一致。

### 4.5 StyleProfile 关联（评估后否决，冻结）

**不建立**包或版本与 StyleProfile 的任何持久关联（无表、无列）。理由：`prompt_compiler` 只消费单一风格（`page.style_id` → `project.default_style_id`，`prompt_compiler.py:61-66,136-145`）；角色包若绑定风格会引入"第二个风格来源"，优先级冲突无解；且任务 A4 要求 StyleProfile 不得被角色包私有化。风格事实只在排队快照中以 `style_profile_id`/`style_profile_version` 记录（§8.2）。

### 4.6 版本选择与继承的落点（冻结）

**版本选择只发生在生成候选层**，不落在 Scene、Panel、Page 上：

| 层 | 是否持有包版本 | 说明 |
| --- | --- | --- |
| `Scene` / `Panel` / `MangaPage` | 否 | 分镜只表达 `character_id` 出镜与 `panel.outfits` 服装指派（既有 JSON，不改）。 |
| `CandidateCreate.reference_selections[char_id]` | 可选 `package_version_id`（显式覆盖） | 新增可选键，缺省走默认继承（§8.1）。 |
| `PageCandidate.prompt_snapshot["character_packages"]` | 是（冻结事实） | 排队时写入，之后不可变（§8.2）。 |
| `GenerationRecord.input_versions["character_packages"]` | 是（紧凑镜像） | 与快照同事务写入（§8.3）。 |

否决"Scene/Panel 级版本指针"的理由：会引入第三类可变指针，且发布新版本时必须决定是否 `mark_pages_for_review`——而本契约冻结"发布不触发 NEEDS_REVIEW"（§9.2），分镜层存版本将使两者矛盾。

### 4.7 禁止使用的冗余字段（冻结清单）

1. 包上不存 `name`/`aliases`（读 Character，§3.3）。
2. 包上不存第二个版本指针（草稿由状态查询，§4.1）。
3. 版本上不存 `completeness_score` 列（读取路径计算，§7.4）。
4. 版本上不存 `is_locked` 布尔（状态机表达，§3.3）。
5. 版本关系上不存 `deleted_at`（解绑即物理删除，仅 DRAFT；冻结事实不被抹除——Asset 软删时 DRAFT 关系行被物理清除（§10.3），READY+ 版本的关系行保留，消费方按 `Asset.deleted_at` 过滤）。
6. 服装关系不复制 `Outfit.reference_asset_ids`（§4.4）。
7. 版本号不存 "1.0" 式字符串（§4.2）。

---

## 5. 版本与事务状态机

### 5.1 事务与锁基础（沿用既有机制）

- 所有状态变更操作**单事务完成**，沿用 P1-5/P2-8 的 `ordinal_savepoint` + `lock_entity`（PG `with_for_update`，SQLite 显式写锁 + 保存点重试，`ordinal_allocator.py:77-125`）；重试耗尽统一 409，失败事务整体回滚，不留半成品。
- 版本序号分配：在包行锁内 `MAX(version_number)+1`，`UNIQUE(package_id, version_number)` 兜底；IntegrityError/锁冲突按 `ORDINAL_ALLOCATION_MAX_ATTEMPTS=5` 退避重试，耗尽 409（对齐 `create_page_candidate` 模式）。

### 5.2 版本状态语义（冻结）

| 状态 | 含义 | 允许的后续状态 | 可变性 |
| --- | --- | --- | --- |
| `DRAFT` | 编辑中（每包至多一个） | `READY`（发布）；物理删除（§10.1） | 规格（包工作集）与关系可编辑 |
| `READY` | 已发布、已冻结，尚未被生产引用 | `IN_PRODUCTION`（首个引用候选同事务置入）；`ARCHIVED` | 不可变（规格快照与关系均冻结） |
| `IN_PRODUCTION` | 已被至少一个 PageCandidate 快照引用 | `ARCHIVED` | 不可变 |
| `ARCHIVED` | 退役归档；仍可被显式选择与历史重放 | `READY`（恢复） | 不可变 |

- **IN_PRODUCTION 是持久状态**（冻结）：在创建第一个引用该版本的 PageCandidate 的**同一事务**内，以条件更新 `UPDATE ... SET status='IN_PRODUCTION' WHERE id=? AND status='READY'` 置入（已置入则幂等跳过）。否决"按历史引用派生"：派生式需要扫描全部 `page_candidates.prompt_snapshot` JSON（无索引、两库行为不一致，data-model.md 明确 JSON 不参与筛选）；持久化的代价是服务端在候选创建事务内多做一次条件 UPDATE，可接受。
- 恢复（ARCHIVED→READY）后不自动回到 IN_PRODUCTION；下一个引用候选会再次触发置入（幂等）。

### 5.3 操作定义（十个场景逐一冻结）

1. **创建兼容包** `POST /characters/{character_id}/package`：单事务创建 `CharacterModelPackage(ACTIVE)` + `V1 DRAFT`（`spec_snapshot` 初始化为 `{"identity_spec": {...payload}, "visual_spec": {...}, "negative_constraints": [...], "frozen_from": "package"}` 的空/初始副本，`published_at=NULL`）。包已存在 → 409；项目或角色不存在/项目已软删 → 404。
2. **编辑草稿** `PATCH package`（规格）与关系 bind/unbind（§9）：规格编辑要求携带 `package.version`（乐观锁，不一致 409，对齐 `update_character` 的 `characters.py:105-106`）；无 DRAFT 时规格编辑 409（状态冲突沿用既有惯例，提示"请先派生新版本"）。**关系变更在同一事务内对 DRAFT 版本的 `version` 执行 compare-and-increment**：请求携带的令牌必须等于当前值，通过后校验、写入关系并在同事务递增 `version.version`（`Timestamped.version` 不会自动递增，必须显式 +1）——两个携带同一令牌的并发关系变更只有第一个成功，其余 409，不会静默覆盖。全部单事务。
3. **发布版本** `POST versions/{id}/publish`：单事务：锁包行 → 重读 DRAFT 及关系 → 校验（目标非 DRAFT → 409；活跃参考关系为 0 → 422）→ 把包工作集规格写入 `spec_snapshot`（`frozen_from: "package"`）→ `status='READY'`、`published_at=now` → `published_version_id=id`。**发布不要求完整度阈值**（完整度仅建议，§7.5）。并发发布同一草稿：后到者在行锁/条件更新处 409；发布与派生竞争：派生要求无 DRAFT，二者在包锁内串行。失败整体回滚，**不存在半发布状态**（指针与状态同事务落库）。
4. **派生新版本** `POST versions {base_version_id?}`：要求当前无 DRAFT（409）；`base` 缺省取 `published_version_id`，显式指定可为任何非 DRAFT 版本，但**必须属于当前包**（`base.package_id == package.id`，否则 409/404——外键不约束同包归属，服务端必须校验，防止把其他角色的身份与关系复制进来）；base 为 DRAFT 422。单事务：分配 V(n+1)；新 DRAFT `spec_snapshot = base.spec_snapshot`（`frozen_from: "derive"`）、`derived_from_version_id=base.id`；复制 base 全部关系行（同 asset/role/label/sort_order/is_default）；包工作集三字段重置为 `base.spec_snapshot` 对应内容（UI 从基线继续编辑）。
5. **生产使用后的冻结**：见 §5.2 IN_PRODUCTION 置入规则。置入失败（如版本被并发归档）不阻塞候选创建——条件更新影响 0 行即跳过，快照事实不受影响。
6. **归档与恢复**：版本 `ARCHIVED`：从 READY/IN_PRODUCTION 可达；`version == package.published_version_id` 时 409（"先切换发布版本"）。恢复 `POST restore` → READY（不自动恢复指针）。包 `POST .../package/archive`（ACTIVE→ARCHIVED）/`POST .../package/restore`（ARCHIVED→ACTIVE）：随时可做；归档后该角色退出默认继承（§8.1），Character 及既有页面不受影响；归档不清 `published_version_id`（历史事实）。
7. **删除或解绑参考图/服装**：关系仅 DRAFT 可变；非 DRAFT 409。解绑 = 物理删除关系行。Asset 软删路径见 §10.1。DRAFT 版本整体删除（§10.1）级联清关系。
8. **切换当前发布版本** `POST activate {version_id, expected_published_version_id}`：目标必须 READY 或 IN_PRODUCTION（ARCHIVED 需先恢复，否则 409）；`expected_published_version_id` 是**必填**的 CAS 令牌——单事务锁包行后校验 `package.published_version_id == expected_published_version_id`，不一致 409（后到者不得静默覆盖先到者的切换）；通过后 `published_version_id` 指向目标，目标状态不变。
9. **并发发布两个草稿**：每包至多一个 DRAFT（部分唯一索引）+ 派生守卫，使"两个不同草稿"不可构造；同一草稿的两个并发发布由包行锁 + 条件更新收敛为一个成功、一个 409。SQLite BUSY/LOCKED 回滚整个事务并按 §5.1 重试。
10. **失败回滚**：创建/派生/发布/切换/归档均为单事务；任何 IntegrityError/锁异常 → 回滚该操作全部写入（包括已分配的 version_number 与关系副本），调用方得到 409；不残留指针移动或状态跃迁。

### 5.4 错误码边界（冻结）

| 码 | 用途 | 示例 |
| --- | --- | --- |
| `404` | 目标不存在或项目已软删 | 包/版本/关系不存在；character 不属于该项目 |
| `409` | 状态冲突、乐观锁不一致、跨实体归属/资格冲突、锁重试耗尽 | 重复建包、非 DRAFT 编辑、跨角色换绑已进包素材（§10.3a）（沿用既有"资格类冲突用 409"惯例，如 `characters.py:146-155`、`validate_candidate_reference_selections`） |
| `422` | 载荷形状/键集合/取值域非法 | 未知 spec 键（`extra="forbid"`）、role/label 组合非法、`sort_order` 越界、发布零参考关系的版本、生成请求显式选择 DRAFT 版本 |

**乐观锁冲突后的客户端处理**（冻结）：409 响应体带 `detail` 文案（与既有 `characters.py:106` 同风格）；客户端必须重新 GET 最新 `version` 后由用户确认重放修改，服务端不做合并。

**编辑旧版本是否允许**（冻结）：不允许。READY/IN_PRODUCTION/ARCHIVED 的规格、关系、快照一律只读；修改只能通过"派生新版本"（任务一.2 与 UI 审计 §4.6 一致）。

---

## 6. 升级迁移设计（可回滚，冻结）

迁移编号沿用仓库惯例：`20260902_25_character_model_packages`，`down_revision = "20260901_24"`。写法对齐 `20260901_24_scene_assets.py`：schema 所有权校验（表已存在但列/索引不匹配 → 明确拒绝）、迁移内禁止 import `Settings`、禁止读凭据、禁止对既有表做任何 `UPDATE`/`DELETE`。

### 6.1 upgrade

1. **建表（含循环外键的两阶段创建，冻结）**：`character_model_packages.published_version_id` 与 `character_model_package_versions.package_id` 构成双向引用，任一表都无法先于对方带全量约束创建——
   - **PostgreSQL**：先建 `character_model_packages`（`published_version_id` 仅作普通可空列，无 FK）→ 建 `character_model_package_versions`（`package_id` FK CASCADE 内联）→ 最后 `ALTER TABLE character_model_packages ADD CONSTRAINT fk_character_model_packages_published_version FOREIGN KEY (published_version_id) REFERENCES character_model_package_versions(id) ON DELETE SET NULL`；
   - **SQLite**：`character_model_packages` 直接内联该 FK（SQLite 允许前向引用同事务内稍后创建的表，FK 在 DML 时才解析；回填 INSERT 前两张表均已存在），无需 ADD CONSTRAINT（SQLite 不支持）；
   - 两库最终逻辑 schema 一致；差异仅是约束创建路径（§6.4）。
2. 建全部索引（DDL = §4，含两库部分唯一索引）。
2. **回填（仅 INSERT 新表）**，对每个既有 `Character`（无其他过滤条件，包括 `alias_conflict` 角色与零参考图角色）：
   - 创建 `character_model_packages(ACTIVE, published_version_id=NULL, 空规格工作集)`；
   - 创建 `V1 DRAFT`：`spec_snapshot={"identity_spec":{}, "visual_spec":{}, "negative_constraints":[], "frozen_from":"migration"}`，`derived_from_version_id=NULL`；
   - 从该角色的 `CharacterReference` 行复制版本参考图关系（同一 `asset_id`）：仅复制 `Asset.deleted_at IS NULL` 的行；`angle → role` 映射冻结为——去空白小写后 ∈ {`front`,`side`,`back`,`three_quarter`,`cover`} → 同名 role；∈ {`expression`,`pose`} → 同名 role + `label="unspecified"`；其余（含 `unspecified`）→ `role="extra"` + 基础 `label=angle or "unspecified"`；`is_canonical` 不参与映射（不虚构 front 槽，默认继承靠显式设置，见下）；
   - **标签碰撞安全（冻结）**：`CharacterReference.angle` 无唯一约束且默认 `unspecified`（`models.py:967-976`），同槽可能撞名。全部降级行（核心槽重复降级为 extra 的行、同 role 多行、以及 extra 基础 label 已被占用者）按 `(created_at, id)` 序处理：该 `(version_id, role, label)` 槽位空闲则直占；已被占用则追加确定性序号后缀 `-{n}`（从 2 起，按处理序递增），保证回填永不违反 `(version_id, role, label)` 唯一约束、升级不因历史数据失败；
   - 复制该角色全部 `Outfit` 关系（`sort_order` 按 `outfits.created_at` 序），**全部 `is_default=False`**——迁移不虚构默认服装，默认值是升级后的显式用户动作；
   - `version_number=1`。
3. **不做**：不复制图片文件或 Asset 记录；不修改 `Character`/`CharacterReference`/`Outfit`/`StyleProfile`/`Asset` 任何行；不修改任何 `PageCandidate.prompt_snapshot`；不修改任何 `GenerationRecord.input_versions`；不创建/修改任何候选、批次或任务；**不发布任何版本**（`published_version_id` 全 NULL）——升级后旧项目的生成、重放、门禁逐字段不变（ grandfather，§7.5），启用包是显式用户动作（与 V02-20A"从 location 创建资产"同哲学）。
4. 脏数据处理（全部确定性、写入本迁移文档字符串）：无参考图角色 → 空矩阵包；指向已软删 Asset 的 `CharacterReference` → 跳过该关系（消费方门禁本就按未删资产判定，`page_readiness.py:105-115`）；无服装角色 → 空服装集；**名称冲突无影响**——包不持有名称（§4.7-1）；`angle` 自由文本 → 统一落入映射规则，不丢数据（extra/label 保留原文）。

### 6.2 新建 Character 不自动建包（冻结）

迁移只覆盖存量。升级后通过既有 `POST /characters` 新建的角色**没有**包；`POST .../package`（"创建兼容包"）是显式动作。理由：与"升级零行为变化"同构——若自动建包，"有包未发布"状态会静默出现在所有新角色上，而包存在与否是 V02-23B UI 的展示分支依据。无包角色与"有包未发布"角色在生成路径上等价（§8.1），因此不需要自动建包。

### 6.3 downgrade（冻结）

先决条件检查，任一不满足即 `RuntimeError` 拒绝（数据原样保留，不做任何清理）：

1. 存在 `status != 'DRAFT'` 的版本（即发生过发布或生产引用）→ 拒绝；
2. 存在 `published_version_id IS NOT NULL` 的包 → 拒绝；
3. 存在多于一个版本的包（发生过派生）→ 拒绝；
4. 存在 `status='ARCHIVED'` 的包 → 拒绝。

全部通过（即所有包仍处于"迁移-created V1 草稿"形态，用户至多编辑过草稿内容）时：DROP 四张表。**允许无损降级的论证**：Character/CharacterReference/Outfit/StyleProfile/Asset/候选/记录从未被迁移触碰，删除包行至多丢弃"未发布的草稿编辑"（规格文字、槽位调整），事实源全部保留。V1 关系行的删除不影响既有绑定。存在新版本或发布指针时拒绝降级且数据保持原样（不做级联删除兜底）。

### 6.4 PostgreSQL 与 SQLite 差异（冻结）

| 面 | 处理 |
| --- | --- |
| 建表 | 新表；**`published_version_id` 循环 FK 两阶段创建**：PostgreSQL 先建表后 `ADD CONSTRAINT`，SQLite 内联前向引用（同事务建齐两表）| 部分唯一索引 | 同时声明 `postgresql_where` / `sqlite_where`（对齐 `20260901_24`；两处 WHERE 表达式相同） |
| 外键 | SQLite 依赖项目已启用的 `PRAGMA foreign_keys=ON`；`CASCADE`/`RESTRICT`/`SET NULL` 两库语义一致 |
| 回填 | 迁移内 Python 循环 + 参数化 INSERT（服务器端游标），在同一迁移事务内；两库行为一致 |
| 并发 | 迁移串行执行，不涉及 §5 锁机制 |
| 检查 | 迁移后外键检查为零（对齐 `test_migrations.py` 既有往返模式） |

**禁止**把 SQLite 迁移测试描述为 PostgreSQL 验收；真实 PostgreSQL 升降级为独立环境验收（PKG-S14，`NOT RUN`）。

---

## 7. 完整度规则（冻结）

### 7.1 性质

完整度是**建议指标**：不落库、不进入 `ensure_page_ready`/`build_page_production_readiness`、不阻塞生成、导出或发布；只在包读取模型与 UI 中展示。历史 Character grandfather 规则：**无包、包已归档、或包无 `published_version_id`** 的角色，生产门禁与今天逐字段一致（§3.2 的三个 blocker 原样）。

### 7.2 计算输入（对一个给定版本）

规格事实来源按版本状态二分（冻结）：**READY+ 版本读 `spec_snapshot`**（发布时冻结的副本）；**DRAFT 读包工作集**（`identity_spec`/`visual_spec`/`negative_constraints` 的当前编辑值，保证发布前的完整度引导不陈旧——草稿期 `spec_snapshot` 仍是派生/迁移来源副本，不反映未发布编辑）。其余输入一律为该版本关系行的存活状态：参考关系各槽（`role`/`label`）是否存在且其 `Asset.deleted_at IS NULL`（Asset 软删后 DRAFT 关系行已被物理清除、READY+ 行保留但失效，两者都表现为该槽缺失）；服装关系是否有成员且该 `Outfit.reference_asset_ids` 存在未删资产；是否设置 `is_default`。**同样的行状态永远得到同样的分数**（参考图/服装失效后重算结果确定且可解释——失效槽位从"已得"变"缺失"，出现在缺失项列表）。

### 7.3 权重（冻结；对 UI 审计 20/40/20/20 的确认与修订）

| 块 | 分值 | 子项 |
| --- | --- | --- |
| 身份与规格 | 20 | `identity_spec` 4 键各 5 分（非空即得） |
| 多视图矩阵 | 40 | `front` 15、`side` 10、`back` 10、`three_quarter` 5（各槽存在未删资产即得） |
| 核心表情集 | 20 | 每个**带不同 label** 的 expression 槽 5 分，封顶 4 个（修订：审计按固定 4 种情绪命名，本契约按槽位计数，label 自由命名，推荐 `neutral/joy/anger/sorrow`） |
| 服装绑定 | 20 | ≥1 条服装关系且其服装有未删参考图 → 15；该版已设 `is_default` → +5（修订：区分"已绑定"与"已设默认"） |

总分 100。缺失项列表输出 `{code, field, message, suggestion}`，例如 `{"code": "MISSING_VIEW", "field": "side", "message": "缺少侧面参考", "suggestion": "上传或生成侧面图后重新发布版本"}`——逐项可解释。

### 7.4 计算位置与不可提交性

分数在**服务端读取路径**由服务函数计算（V02-22B 提供，供包详情/列表/快照摘要复用）；无任何写入端点接受分数；`spec_snapshot` 与关系是唯一事实。前端展示的百分比必须来自 API，不得本地另算。

### 7.5 与发布、门禁的关系

发布不设分数阈值（空分也能发布，只要有 ≥1 条参考关系，§5.3-3）；完整度不足只提示补全，不阻断任何既有生产流（任务五与 roadmap V02-23A 结论一致）。

---

## 8. 生成版本锁定（冻结）

### 8.1 版本解析与默认继承

对每个出镜角色，按序解析"本次生成使用的包版本"：

1. 请求显式指定 `reference_selections[char_id].package_version_id` → 必须先解析归属：该版本的 `package_id` 对应包的 `character_id` 必须等于当前出镜角色且 `project_id` 等于当前项目（否则 409，沿用资格类冲突惯例），再检查状态——目标不存在 404、为 DRAFT 422、ARCHIVED 允许（显式选择旧行为）；**禁止把其他角色（或其他项目）的包版本冻结到当前出镜角色名下**；
2. 否则角色存在 `status='ACTIVE'` 的包且 `published_version_id` 非空且目标版本 ∈ {READY, IN_PRODUCTION} → 用该版本；
3. 否则（无包/包归档/未发布）→ **legacy 路径**，校验与今天逐字段一致（`validate_candidate_reference_selections` 原逻辑）。

命中包版本时，排队校验替换为：`character_asset_id` 必须是该版本中 `role` 任意、`Asset` 未删的参考关系行（缺省时按默认继承自动选择：`front` 槽 → `cover` 槽 → 任一 `sort_order` 最小者；全部为空 409 提示选择参考图）；`outfit_id` 解析顺序 = 显式选择 > 分镜 `panel.outfits` > 版本默认服装（`is_default`）；命中包版本的服装必须是该版本的活跃服装关系成员（409 同既有文案风格）；`outfit_asset_id` 必须 ∈ `outfit.reference_asset_ids` 且未删。**门禁联动（需组长批准，§12 #1）**：`MISSING_OUTFIT_ASSIGNMENT` 增加一条替代满足路径——分镜未指派但解析出的包版本存在带有效参考图的默认服装时不再阻断，排队自动选默认服装；其余两个角色类 blocker 与全部风格/模型/Worker 条件不变。无包角色不受任何影响（grandfather 完整）。

### 8.2 排队快照（`prompt_snapshot["character_packages"]`，冻结）

命中包版本时，按角色写入（与既有 `reference_selections`、`scene_asset` 并列的新键）：

```json
{
  "character_packages": {
    "<character_id>": {
      "package_id": "…",
      "package_version_id": "…",
      "version_number": 2,
      "spec_fingerprint": "sha256:<canonical-json(spec_snapshot)>",
      "primary_name": "林澈",
      "aliases": ["…"],
      "identity_spec": {"…": "…"},
      "visual_spec": {"…": "…"},
      "negative_constraints": ["…"],
      "character_asset_id": "…",
      "reference_role": "front",
      "reference_label": "",
      "outfit_id": "…" ,
      "outfit_asset_id": "…",
      "style_profile_id": "… or null",
      "style_profile_version": 7
    }
  }
}
```

- 携带**规格全文冻结副本**而非仅指纹：重放/编译无需回查版本行，版本归档后快照自足；`spec_fingerprint` 供审计与去重（canonical JSON 的 sha256）。`primary_name`/`aliases` 一并冻结：Character 改名不影响历史候选重放。
- `style_profile_id`/`style_profile_version` 仅记录排队时解析到的事实（`page.style_id` → `project.default_style_id`）；风格参考图加载仍走既有执行时路径（§3.1-4，不改）。

### 8.3 `GenerationRecord.input_versions`（冻结）

同事务写入紧凑镜像：`input_versions["character_packages"] = {<character_id>: {"package_id", "package_version_id", "version_number", "spec_fingerprint"}}`。完整规格以 `prompt_snapshot` 为准（input_versions 保持既有"紧凑版本事实"定位，`page_generate.py:461-467`）。

### 8.4 JobAssetReference 租约（冻结）

排队事务的租约集合在既有"人物 + 服装 + 场景"之上，**人物与服装的 Asset ID 一律取自本次解析结果**（命中包版本时即版本关系中的实际 Asset）：`character_asset_id` + `outfit_asset_id`（每角色）+ 场景参考图。按 `(job_id, asset_id)` 唯一去重（同一 Asset 服务多角色/多槽只写一行）。执行期保护沿用既有双层机制：`_lease_reference_assets` + 付费前重读全部租约行（`page_generate.py:409-421`）；`_ensure_asset_not_in_active_job` 使排队/执行期间删除任一被租约 Asset 返回 409（`uploads.py:63-77`，`WAITING` 已含于 `ACTIVE_REFERENCE_STATUSES`）。

### 8.5 Worker 消费规则（冻结）

Worker 对命中 `character_packages` 的候选：只读取排队快照中的规格与 Asset ID；校验仅限——(a) 每个引用 Asset 存在且未软删（缺失即 RuntimeError 停止，对齐 `page_generate.py:82-87` 既有文案风格），(b) `package_version_id` 对应版本行存在（由 §10.2 的"被引用版本不可物理删除"保证），(c) 分镜版本守卫（既有）。**不**重读最新版本、不重校验版本关系绑定（排队后解绑草稿关系与在途任务无关；且 READY+ 版本关系本就冻结）。提示词编译：该角色的 bible 条目（`prompt_compiler.py:35-45` 形状）以快照冻结规格 + 冻结名称替换现场 Character 行读取；未命中的角色与 legacy 候选维持现场读取。

### 8.6 历史、重试与重放（冻结）

1. 发布新版本不改变任何历史候选、不触发 `mark_pages_for_review`、不改变 `NEEDS_REVIEW` 状态（与场景绑定不同——绑定改变的是"当前采用"，发布只是新增不可变版本，§9.2）。
2. 页面重新生成（新候选）默认继承 §8.1；分镜已变更的页面因 `storyboard_version` 守卫自然重走新候选。
3. 局部编辑（`PAGE_REPAIR`）与升清（`PAGE_UPSCALE`）沿用 `original_candidate_id` 继承**原候选的完整快照**（含 character_packages），不重新解析版本。
4. 失败重试：同一候选/任务重试沿用其排队快照（既有执行外壳语义，不变）。
5. 旧候选没有 `character_packages` 键 → 永久合法状态，重放走 legacy 编译路径，不做任何回填；`version_state` 既有语义（`LEGACY_UNKNOWN` 等，`helpers.py:18-36`）不变。
6. 组合顺序与去重：参考图序列 = 各角色 `character_asset_id`（角色按分镜首次出场顺序）→ 同角色 `outfit_asset_id` → 场景参考图 → 风格参考图 → 上一页采用图（延续既有 `page_generate.py` 追加顺序）；按 Asset ID 去重**保留首次出现位置**（对齐现有 `dict` 去重语义）；上限沿用模型能力校验 `_validate_reference_capacity`。

---

## 9. API 契约（为 V02-23B 冻结最小集）

统一约定：路径挂载在既有 `/api/v1` 前缀下；全部项目作用域（项目不存在/已软删 404，对齐 `list_assets`）；包以 `character_id` 寻址（锚点 A1）；响应模型字段——`PackageRead`/`VersionRead` 含 `created_at/updated_at/version`（Timestamped），**关系读取模型 `ReferenceRead`/`VersionOutfitRead` 只含各自列与 `created_at`、无独立 `version`**（关系变更的并发守卫是父 DRAFT 版本的 compare-and-increment，§5.3-2）；分页用 `limit`（默认 50，≤200）/`offset`。除注明外，**任何包端点都不调用 `mark_pages_for_review`，不产生页面 NEEDS_REVIEW**（§9.2）。

### 9.1 端点表

| Method/Path | 请求 → 响应 | 语义与错误 |
| --- | --- | --- |
| `GET /projects/{project_id}/character-packages` | `?status=&limit=&offset=` → `list[PackageSummaryRead]`（含 character 摘要、发布版本号、发布版完整度） | 列表；`status` ∈ {ACTIVE,ARCHIVED} 可选筛选 |
| `POST /projects/{project_id}/characters/{character_id}/package` | `{identity_spec?, visual_spec?, negative_constraints?}` → `PackageRead`（含 V1 DRAFT） | 创建兼容包；409 已存在；404 角色/项目；幂等性：无（重复提交 409） |
| `GET /projects/{project_id}/characters/{character_id}/package` | `?include_versions=true` → `PackageRead`（详情，含版本数组与关系） | 只读 |
| `PATCH /projects/{project_id}/characters/{character_id}/package` | 规格三字段 + `version` → `PackageRead` | 编辑草稿工作集；409 乐观锁不一致或无 DRAFT（状态冲突沿用既有 409 惯例） |
| `POST .../package/versions` | `{base_version_id?}` → `VersionRead`（新 DRAFT） | 派生；409 已有 DRAFT |
| `POST .../package/versions/{version_id}/publish` | `{}` → `VersionRead` | 发布（§5.3-3）；409 非 DRAFT/锁耗尽/已是指针；422 零参考关系 |
| `POST .../package/archive` | `{}` → `PackageRead` | 包 ACTIVE→ARCHIVED（§5.3-6）：退出默认继承，不影响 Character 与既有候选；无前置条件 |
| `POST .../package/restore` | `{}` → `PackageRead` | 包 ARCHIVED→ACTIVE；恢复默认继承资格（发布指针不变） |
| `POST .../package/versions/{version_id}/archive` | `{}` → `VersionRead` | 409 目标为发布指针 |
| `POST .../package/versions/{version_id}/restore` | `{}` → `VersionRead` | ARCHIVED→READY |
| `POST .../package/activate` | `{version_id, expected_published_version_id}` → `PackageRead` | 切换发布指针（§5.3-8，CAS 令牌必填）；404/409（指针已被并发切换） |
| `DELETE .../package/versions/{version_id}` | → 204 | 仅 DRAFT 可物理删除；409 其他状态 |
| `POST .../package/versions/{version_id}/references` | `{asset_id, role, label?, sort_order?, version}` → `ReferenceRead` | 绑定（DRAFT only；`version` 为 DRAFT 令牌，同事务校验并递增父版本，§5.3-2）；404 Asset 不存在或已软删；409 非 DRAFT/重复槽/Asset 跨项目/资格不符（沿用 `bind_reference` 惯例，`characters.py:141-155`） |
| `PUT .../package/versions/{version_id}/cover` | `{asset_id, version}` → `ReferenceRead` | 设置封面：`role=cover` 槽的绑定/替换语义（已存在封面时同一事务先解绑旧行再绑新行）；DRAFT only，令牌校验并递增；等价于 references 端点的受限形式，单列成端点以匹配 UI 动作 |
| `DELETE .../package/versions/{version_id}/references/{reference_id}` | body `{version}` → 204 | 解绑（DRAFT only，令牌校验并递增，对齐 `DialogueDelete` 的 DELETE-body 惯例） |
| `POST .../package/versions/{version_id}/outfits` | `{outfit_id, is_default?, sort_order?, version}` → `VersionOutfitRead` | 绑定服装（DRAFT only，令牌校验并递增）；409 跨角色/重复/非 DRAFT |
| `PATCH .../package/versions/{version_id}/outfits/{outfit_id}` | `{is_default, version}` → `VersionOutfitRead` | 设默认（部分唯一索引封并发双默认；置默认不自动清旧——由索引与应用层在同事务互斥更新保证）；令牌校验并递增 |
| `DELETE .../package/versions/{version_id}/outfits/{outfit_id}` | body `{version}` → 204 | 解绑（DRAFT only，令牌校验并递增） |
| `GET .../package/diff` | `?base_version_id=&target_version_id=` → `PackageDiffRead` | 逐槽结构化差异：规格三块字段级、参考图按 `(role,label)` 对比 Asset 与状态、服装集合与默认位变化 |
| `GET .../package/versions/{version_id}/completeness` | → `{score, missing[]}` | §7；同时内嵌于版本读取模型 |

**生成侧（无新端点，冻结）**："选择角色包版本用于生产"通过 `CandidateCreate.reference_selections[char_id]` 新增**可选**键 `package_version_id` 实现（§8.1）；无独立"绑定版本到页面"端点。

### 9.2 NEEDS_REVIEW 与锁定版本操作（冻结）

- 发布、归档、恢复、切换指针**均不触发**页面 NEEDS_REVIEW。理由：分镜与页面不持有版本指针（§4.6），发布不改变任何既有绑定的事实；需要换版本的页面通过新生成自然获得。
- 允许作用于已锁定（READY+）版本的操作仅：读取、diff、completeness、archive、restore、activate、显式选用于生成；其余变更一律 409。

### 9.3 `generate-views` / `generate-expressions`（只冻结契约，不实现）

若 V02-23B 保留（本任务不实现、不调用真实模型）：

- `POST .../package/versions/{version_id}/generate-views`：请求 `{views: ["FRONT"|"SIDE"|"BACK"|"THREE_QUARTER"], model_alias, resolution}`，逐 view 复用既有 ASSET_GENERATE 通道（`target_type=CHARACTER`、`variant` 枚举 `ordinal_allocator.py:476-482`；`THREE_QUARTER` 需先扩枚举，属实现切片）。响应 `202 + {batch_id, jobs[]}`，任务追踪走既有 jobs API。
- `POST .../package/versions/{version_id}/generate-expressions`：请求 `{labels: string[≤6], model_alias, resolution}`，每 label 一个 EXPRESSION 候选任务。
- 费用确认契约：沿用既有 estimated_cost 字段语义（估算≠账单）；每个 view/label 是一次独立付费派发，进入 `ModelCallAttempt` 账本。
- 模型能力：沿用目录 operations/resolutions 校验；不支持 `image_edit`（有参考）或分辨率不足 → 422。
- 产物采用：生成结果先入素材库（AssetCandidate），采纳进版本槽位必须走 §9.1 的 references 绑定端点（DRAFT only）——**生成不直接写版本关系**。

### 9.4 分页与筛选汇总

列表端点固定 `limit/offset` + `(status)` 筛选 + 默认按 `created_at` 升序（包）/`version_number` 降序（版本）；无 keyset 需求（单项目角色量级小，对齐场景资产 §13 的量级判断）。

---

## 10. 软删除与资源所有权（冻结）

1. **删除/恢复语义**：Package——无删除端点，仅 `status` ACTIVE↔ARCHIVED；Version——DRAFT 可物理删除（级联关系行），READY/IN_PRODUCTION/ARCHIVED 只能归档/恢复，**永不物理删除**；reference/outfit 关系——仅 DRAFT 可解绑（物理删除行）；Outfit 关系成员的 Outfit 本体删除见第 4 条。
2. **被历史候选引用的版本**：禁止物理删除（上面已冻结"非 DRAFT 不可删"）。该不变式使 Worker 的 `package_version_id` 存在性校验（§8.5）无需处理悬空。
3. **被活动 JobAssetReference 租约的 Asset**：删除被 409（既有 `_ensure_asset_not_in_active_job`，不改）；资产软删对**未租约**场景的影响按版本状态二选一（冻结，消除二义性）：**DRAFT 版本关系行随资产删除被物理清除**（V02-22B 扩展 `_detach_reference_asset`，与该函数对既有 `CharacterReference` 行的物理删除语义一致，草稿槽位回到"空缺"，用户可重新绑定）；**READY+ 版本关系行原样保留**（冻结事实，消费方按 `Asset.deleted_at` 过滤，槽位显示"已失效"，完整度重算确定性降分，§7.2）。旧 Worker 晚返回/租约失效语义沿用既有 P1-7/P1-9 机制，本契约不重复实现。
3a. **跨角色换绑守卫（冻结，V02-22B 必须实现）**：`bind_reference`（`characters.py:129-188`）把素材换绑到角色 B（删除角色 A 的 `CharacterReference` 并为 B 重建）之前，必须校验该 Asset 未被**任何其他角色的**包版本参考关系（DRAFT 或 READY+，未解绑行）引用；命中即 409（"该素材已被角色模型包版本引用，请先在对应版本中解绑或放弃换绑"）。理由：包版本关系直接指向 Asset（§4.3），若不设守卫，A 的已发布版本与 B 的角色绑定会同时指向同一张图，生成时造成身份混合，且 READY+ 关系不可变、无法自动解除。素材属于同一角色的包关系时不拦截（换绑前后角色一致，无身份混合）。此守卫与既有"asset 全局唯一"约束共同保证"一张人物参考图至多服务一个角色"在包时代继续成立。
4. **Outfit 删除**：`DELETE /outfits/{outfit_id}`（`asset_generation.py:184-283`）现可硬删。冻结：Outfit 被任何包版本关系引用时，V02-22B 在删除前校验并 409（"被角色模型包版本引用，请先解绑"）；FK RESTRICT 是数据库兜底。**Outfit 归档/失效后历史版本展示**：若以 `AssetStatus.ARCHIVED` 停用（非删除），版本关系保留，UI 按 Outfit 当前状态展示"服装已归档"，历史候选不受影响（快照已冻结具体 `outfit_asset_id`）。
5. **Package 归档是否影响 Character**：不影响。Character、参考图、服装、分镜、既有候选全部不变；仅默认继承退出（§8.1-2）。
6. **资源清理所有权**：只清理由当前操作证明拥有的资源（对齐仓库"只清理已证明归属"规则）：解绑删除该关系行本身；版本删除只级联自身关系行；不触碰 Asset 文件、Outfit、CharacterReference、其他版本或项目内共享资源。

---

## 11. 测试与验收矩阵（可直接用于 V02-22B）

| 编号 | 场景 | 类型 / 环境 | 关键断言 |
| --- | --- | --- | --- |
| PKG-S1 | 既有 Character 无损升级 | SQLite 迁移测试（扩展 `test_migrations.py` 往返模式） | 迁移后每个 Character 有包 + V1 DRAFT；`published_version_id` 全 NULL；Character/Reference/Outfit/Style/Asset 行逐字段不变；`prompt_snapshot`/`input_versions` 逐字节不变 |
| PKG-S2 | ID 不变 | SQLite 单元 | 升级前后 `Character.id`/`CharacterReference.asset_id`/`Outfit.id`/`Asset.id` 集合相等；V1 关系引用同一批 Asset ID；不新建 Asset/Outfit 行 |
| PKG-S3 | 草稿编辑与乐观锁 | SQLite 单元（新 `test_character_packages.py`） | 规格编辑 `version` 不匹配 409；未知 spec 键 422；无 DRAFT 编辑 409；两个携带同一 DRAFT 令牌的并发关系变更恰好一个成功、其余 409（compare-and-increment，§5.3-2）；关系绑定校验角色/项目归属 |
| PKG-S4 | 并发发布唯一性 | SQLite 双 Session（对齐 P2-8 模式） | 同一 DRAFT 并发发布：恰好一个成功，`published_version_id` 唯一且指向该版本；失败方 409；派生与发布竞争串行化 |
| PKG-S5 | 派生版本与旧版本不可变 | SQLite 单元 | 派生后 base 的 `spec_snapshot`/关系逐字段不变；新 DRAFT 复制关系；旧版本所有变更端点 409 |
| PKG-S6 | 完整度确定性与 grandfather | SQLite 单元 | 同一行状态两次计算分数一致；草稿规格编辑后 DRAFT 分数即时变化（读包工作集，§7.2）；失效参考图后分数下降且缺失项列出该槽；无包/未发布角色 `ensure_page_ready` 行为与升级前一致；分数不阻断生成 |
| PKG-S7 | 参考图角色及项目归属校验 | SQLite 单元 | 绑定跨项目 Asset 409；Asset 已软删 404（对齐 `bind_reference`）；`role/label` 组合非法 422；核心槽重复 409；同一逻辑槽换绑前旧行未解绑 409；被其他角色包版本引用的素材跨角色换绑 409（§10.3a） |
| PKG-S8 | 默认服装唯一性 | SQLite 单元 + 并发双设 | 部分唯一索引使并发双默认只成功一个；解绑默认后可再设 |
| PKG-S9 | 排队快照和 Worker 读取一致 | SQLite 单元 + 本地执行器 + 假供应商 | 排队后修改版本/关系/Character 行，Worker 仍按快照生成；显式 `package_version_id` 属于其他角色或其他项目时 409（§8.1-1）；`prompt_snapshot["character_packages"]` 与 `input_versions["character_packages"]` 的 package_version_id/version_number/fingerprint 一致；租约集合含版本解析出的实际 Asset |
| PKG-S10 | JobAssetReference 租约与删除保护 | SQLite 单元 | 排队/执行期间删除被租约 Asset 409；DRAFT 关系解绑不影响在途任务 |
| PKG-S11 | 历史候选和重放逐字段不变 | SQLite 单元 | 发布新版本后：旧候选 `prompt_snapshot` 逐字节不变；重试/修复/升清沿用原快照；无 `character_packages` 键的旧候选重放走 legacy 路径且结果不变 |
| PKG-S12 | 软删除、恢复和历史展示 | SQLite 单元 | 包归档/恢复后默认继承退出与恢复；DRAFT 删除级联关系；READY+ 版本不可删；Asset 软删后 DRAFT 关系行被物理清理、READY+ 槽位显示失效且完整度重算下降 |
| PKG-S13 | 迁移升降级和拒绝降级 | SQLite 迁移测试 | upgrade→downgrade→upgrade 往返（原始态）；存在发布指针/多版本/归档包时 downgrade 拒绝且数据原样；外键检查为零 |
| PKG-S14 | 真实 PostgreSQL 并发与约束验收 | **真实 PostgreSQL**（独立环境） | 升降级往返、部分唯一索引、`FOR UPDATE` 包锁下并发发布/派生/切换、RESTRICT 删除保护；无 PG 环境时如实标注 `NOT RUN` |

**环境分层声明（冻结）**：PKG-S1～S13 设计为 SQLite 单元/迁移测试 + 本地执行器 + 假供应商（离线，不调用真实供应商、不产生费用）；PKG-S14 必须真实 PostgreSQL。Redis/RQ 多 Worker 并发租约验证属独立集成验收（沿用 P1-11 边界），不在本契约测试矩阵内伪装；真实供应商 generate-views/expressions 调用为 `NOT RUN`。缺失环境一律如实标注，SQLite/fakeredis/mock 不替代。

---

## 12. 风险和未决问题

### 12.1 已冻结决策（汇总）

1. Character.id 一对一锚点；包 API 以 character_id 寻址；包不持有名称/别名。
2. 版本参考图与服装关系用关系表（§4.3 比较）；参考图逻辑槽唯一由 `(version_id, role, label)` 唯一约束 + role/label CHECK 保证（不含 asset_id），默认服装与每包单 DRAFT 用部分唯一索引封并发。
3. 单一 `published_version_id` 指针；每包至多一个 DRAFT；版本号整数单调。
4. 版本状态 DRAFT/READY/IN_PRODUCTION/ARCHIVED；IN_PRODUCTION 持久化（首个引用候选同事务置入）；发布=单事务冻结，无半发布状态。
5. 迁移建包 + V1 **草稿**、不发布、不改既有行、不回填候选；新建角色不自动建包；downgrade 拒绝条件四条。
6. 完整度服务端读取路径确定性计算、不落库、不进门禁、权重 20/40/20/20（子项修订）；READY+ 读 `spec_snapshot`，DRAFT 读包工作集（§7.2）。
7. 版本选择只在候选层（`reference_selections.package_version_id` + 默认继承链）；排队快照 `character_packages` 含规格全文冻结副本；Worker 只消费快照；发布不触发 NEEDS_REVIEW。
8. StyleProfile 不与包建立持久关联。
9. 服装参考图保持服装级、不按版本复制；候选不可变由排队快照保证。

### 12.2 被否决方案及原因

| 方案 | 否决原因 |
| --- | --- |
| JSON 数组/字典存版本参考图与服装（UI 审计原案） | §4.3 逐维比较：无 FK、并发唯一性、租约查询、排序寻址全面劣势 |
| 包上复制 name/aliases | 与 Character 字段漂移；别名冲突逻辑已存在 |
| 存储完整性分数列 | 失效后需失效/重算同步，必然漂移；读取路径计算零成本 |
| `is_locked` 布尔 | 与版本状态冗余 |
| IN_PRODUCTION 按引用派生 | 需全表扫描 JSON 快照，两库无一致索引方案 |
| 第二个"当前版本"指针列 | 与状态查询冗余 |
| Scene/Panel/Page 级版本指针 | 第三类可变指针；与"发布不触发 NEEDS_REVIEW"矛盾 |
| 版本复制服装参考图集合 | N×M 关系与解绑放大；快照已保证候选不可变 |
| 迁移直接发布 V1 | 迁移后生成路径立即改道版本关系，破坏"升级零行为变化" |
| 包级 StyleProfile 绑定 | 双风格来源冲突；StyleProfile 必须保持项目级 |

### 12.3 仍需组长批准的问题

1. **门禁扩展**：`MISSING_OUTFIT_ASSIGNMENT` 增加包默认服装替代满足路径（§8.1）。这是对既有生产门禁的唯一行为扩展；备选方案是保持门禁不动（默认服装仅在分镜已指派时无效），但那样默认服装近乎无用途。
2. **风格参考图租约时机**：现有 Worker 在执行期读取风格参考（未参与排队租约，§3.1-4）。本契约不改；若要一并冻结风格参考，应另立切片。
3. **快照体积**：`character_packages` 携带规格全文冻结副本会增加 `prompt_snapshot` 体积（每出镜角色约 1–2KB）。若组长认为不可接受，可降级为"指纹 + 版本行回查"（代价：重放依赖版本行永存——已由 §10.2 保证，但重放路径多一次 join）。
4. **V1 回填跳过软删参考图**（§6.1-2）：被跳过的绑定不出现在 V1 矩阵；备选是全量复制再由消费方过滤。
5. **V02-23B 是否在新建 Character 时引导创建包**（UI 动作，非迁移）：本契约冻结"不自动建包"，UI 引导策略留给 V02-23A/B。

### 12.4 实现拆分建议（V02-22B，供组长派工参考）

1. 切片一：迁移 + ORM + schema 所有权校验 + PKG-S1/S2/S13（纯数据层，PG 验收另列）。
2. 切片二：包/版本/关系服务与 API + 乐观锁/状态机 + PKG-S3～S8、S12。
3. 切片三：排队校验替换、`prompt_snapshot`/`input_versions` 扩展、Worker 快照消费、租约接入、`_detach_reference_asset`/`delete_outfit`/`bind_reference`（跨角色换绑守卫，§10.3a）扩展 + PKG-S9～S11。
4. 切片四：完整度服务 + diff + PKG-S6 收口；generate-views/expressions 为可选切片（需先扩 `THREE_QUARTER` 枚举）。
5. PKG-S14（真实 PostgreSQL）独立环境验收，不与切片 1–4 混入同一 PR。

### 12.5 数据损坏与历史兼容风险

- 迁移回填在大库上的事务时长（单事务全量 INSERT）；缓解：回填在迁移事务内分批提交需谨慎（部分失败会留半回填态），建议保持单事务并接受时长，或按组长指示分批 + 幂等续跑（两者择一，V02-22B 实现前定稿）。
- 角色槽语义与历史 `angle` 自由文本的映射损失（同槽多图降级 extra）——已确定性化（§6.1-2），但首轮真实数据分布未验证（`NOT RUN`）。
- 包默认服装满足门禁的扩展若被否决，§8.1 的继承链需回退一项（其余不受影响）。
- 快照体积增长对 `page_candidates` 行宽的影响（§12.3-3）。

### 12.6 NOT RUN 边界（汇总）

- `NOT RUN`：真实 PostgreSQL 升降级与并发（PKG-S14）；SQLite 往返不替代。
- `NOT RUN`：Redis/RQ 多 Worker 真实集成。
- `NOT RUN`：真实供应商调用（本任务未实现、未调用任何模型；generate-views/expressions 仅契约）。
- `NOT RUN`：浏览器 E2E / UI 验收（无前端改动；V02-23B 范围）。
- `NOT RUN`：本契约为设计文档，未运行 `npm run check`、未创建迁移或任何代码；验收以文档一致性核对与 `git diff --check` 为准。
