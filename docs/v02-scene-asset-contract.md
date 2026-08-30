# V02-20A 场景资产（一级资产）数据契约设计

- 任务：Issue #44 / `[0.2.0][V02-20A] Design the first-class scene asset data contract`
- 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（`origin/master`）
- 工作分支：`codex/v02-20a-scene-asset-contract`（worktree `D:\自媒体\漫画工作流-deepseek-v02-20a`）
- 任务性质：L3 数据契约设计草案（DeepSeek 起草，不拥有最终批准权；最终方案由 lead 集中复审确认）
- 约束：本文件只冻结设计契约，**不实现代码、迁移、测试；不编辑 `docs/roadmap.md` / `docs/development-progress.md` / `plan.md`**
- 修订记录：（由 lead 接管收口时填写）

---

## 1. 执行摘要

`Scene.location` 目前是 `String(200)` 纯文本（`models.py:222`），由剧情解析写入（`worker_handlers/story_parse.py:284`），在分镜生成时被直接复制为 `Panel.background`（`services/content_workflow.py:661,725`）。它没有参考图、没有版本、没有确认状态、没有变体，也无法结构化表达地点层级与空间关系——画面一致性只能依赖每次手写的文字。

本设计把场景升级为一级资产，沿用项目既有的「实体资产 + 文件资产」二分模式：**`SceneAsset`（实体表，含结构化字段、变体、状态、版本）+ `SceneAssetReference`（引用 `Asset` 文件池）**，与 `Character`/`CharacterReference`、`Outfit`、`StyleProfile` 完全同构。`Scene` 新增 `scene_asset_id` / `scene_asset_variant_id` 引用，`location` 保留为迁移兼容的**描述兜底**；消费规则为「结构化字段优先 → 资产描述兜底 → 历史 location 兜底」。

**红线：迁移零数据重写**。既有 `scenes.location` 文本原样保留、`scene_asset_id` 置 NULL、不回填虚构资产；历史行为不变。升级到资产是 V02-21 UI 的显式用户动作（「从 location 创建资产」），不是自动迁移副作用。

**路由选择策略不变**：`model_router.py` 与 `model_availability.py` 不改。任务创建、`JobAssetReference` 租约、生成消费函数、候选快照与 `GenerationRecord.input_versions` 必须扩展以纳入场景引用，因此不能笼统称为“后端调度零改动”。

---

## 2. 当前 Scene.location、页面、剧本、资产和候选引用的真实结构

### 2.1 剧本层

`Scene`（`models.py:214-231`）：

```text
id, chapter_id (FK CASCADE), ordinal
location: String(200) default ""        # 唯一的地点表达，纯文本
time_label: String(120) default ""      # 松散时间标签，如 "放学后"
weather: String(120) default ""         # 松散天气标签
purpose / emotional_arc: Text
source_range: JSON                      # 原文区间
outfit_assignments: JSON                # 角色→服装
locked_fields: JSON
version（Timestamped 乐观锁）
```

`Beat`（`models.py:234-250`）：挂在 `Scene` 下（`scene_id` FK CASCADE），含动作/对白/旁白/潜台词/情绪/原文区间。

### 2.2 页面/格子层

`MangaPage`（`models.py:253-280`）：

- `scene_ids` / `beat_ids`：JSON 数组，**存文本 id**，不建立外键。页面通过场景 id 引用多个场景。
- `selected_candidate_id`：当前采用候选。
- `storyboard_version` / `selected_candidate_ack_version`：分镜版本与采用确认版本。

`Panel`（`models.py:283-309`）：

- `bounds`：JSON（当前为 0–1 坐标，V02-30 契约另行处理）。
- `background`：Text —— **分镜生成时从 `page_scenes[0].location` 复制文本快照**（`content_workflow.py:725`），无资产引用。

### 2.3 资产/候选层

`Asset`（`models.py:187-211`）：全局文件池。`kind ∈ {CHARACTER_REFERENCE, OUTFIT_REFERENCE, STYLE_REFERENCE}`（`uploads.py:37-44` 的 `ASSET_KINDS`）；`sha256` 项目内唯一；`source ∈ {USER_UPLOAD, AI_GENERATED, ...}`；`status`（`AssetStatus`：UPLOADED/ANALYZED/GENERATED/NEEDS_CONFIRMATION/CANONICAL/ARCHIVED）；`deleted_at` 软删除；`storage_key`/缩略图键。

`CharacterReference`（`models.py:662-673`）：`character_id + asset_id`（asset FK `RESTRICT`），`angle`、`is_canonical` —— **实体↔文件绑定的既有范式**。

`JobAssetReference`（`models.py:399-410`）：`job_id + asset_id`（asset FK `RESTRICT`），**资产租约**。`uploads.py:57-71` 的 `_ensure_asset_not_in_active_job` 阻止删除被活动任务引用的素材。

`_detach_reference_asset`（`uploads.py:74-102`）：删除 Asset 时从 `CharacterReference`、`Outfit.reference_asset_ids`、`StyleProfile.profile.reference_asset_ids` 解绑并重置状态——**既有解绑模式**。

`AssetCandidate`（`models.py:738-766`）：非页面批次的生成候选（`variant`、`instruction`、`asset_id`）。`PageCandidate`（`models.py:701-735`）：页面候选，`prompt_snapshot` 记录生成时快照、`based_on_storyboard_version` 锁定分镜版本、`asset_id`（FK `RESTRICT`）指向输出图。

### 2.4 location 消费路径

- 写入：`story_parse.py:284`（解析时 `location=scene_draft.location`）；`sources.py:358-388`（`update_scene` PATCH，改后触发 `mark_pages_for_review(chapter_id, reference_id=scene.id, reference_kind="scene")`）。
- 读取：`content_workflow.py:661`（补充镜头环境说明）、`:725`（`Panel.background = page_scenes[0].location`）。

---

## 3. 为什么 location 描述不足以成为一级资产

1. **不可结构化**：地点、子区域、室内外、时间/天气/光照/季节/色彩、固定物件、空间关系混在一段文字里，无法筛选、匹配、对比或按维度复用；`time_label`/`weather` 与 `location` 各自独立无关联，同一场景无法表达「清晨的教室 vs 黄昏的教室」。
2. **无参考图锚点**：地点只能靠文字想象，每次生成画面无法保证同一地点的一致性——这正是 `CharacterReference` 为角色解决、而地点缺失的问题。
3. **无生命周期**：没有版本、确认状态、软删除/恢复；编辑 `location` 文本即不可追溯地覆盖，无法回退到上一版，也无法锁定「生成这张页面时用的场景」。
4. **无复用**：场景是 `Scene` 的属性，跨章节/跨项目无法把同一地点资产化复用；用户每次都要重写文本。
5. **与既有资产体系断裂**：项目已有 `Character/Outfit/StyleProfile` 实体资产 + `Asset` 文件池的成熟范式，地点却停留在「面板字段」——页面/任务/候选既无法引用它，也无法进入资产租约与生成审计。

---

## 4. 选定方案：SceneAsset + SceneAssetReference + SceneAssetVariant

沿用项目「实体资产表 + `Asset` 文件池 + 引用绑定表」范式。**不做**在 `Asset` 表上加 `kind="SCENE"` 的扁平方案，理由：场景资产需要结构化字段、变体、版本化与确认状态，这些超出文件表职责；且 `Character/Outfit/StyleProfile` 均非文件表，保持同构便于 UI 与测试复用。

### 4.1 表结构

```text
scene_assets  (实体资产)
  id: str(36) PK
  project_id: FK projects.id CASCADE, index
  name: String(120)                     # 资产名，如「花凛的高三（2）班教室」
  description: Text default ""           # 兜底描述（结构化缺失时用）
  location_hint: String(200) default ""  # 迁移兼容：来源 location 文本，只读展示用
  structured: JSON default {}           # 结构化字段（4.2）
  status: AssetStatus 枚举 default UPLOADED
  locked_fields: JSON default []
  deleted_at: datetime nullable          # 软删除
  version: int (Timestamped)

  Index ix_scene_assets_project_deleted_created (project_id, deleted_at, created_at)
  活跃名称唯一 (project_id, normalized_name) WHERE deleted_at IS NULL

scene_asset_references  (参考图绑定)
  id: str(36) PK
  scene_asset_id: FK scene_assets.id CASCADE, index
  asset_id: FK assets.id RESTRICT, index
  role: String(32) default "main"        # main / subarea_<key> / overview / interior / skyline ...
  is_canonical: bool default False
  created_at

  UniqueConstraint (scene_asset_id, asset_id, role)

scene_asset_variants  (场景变体：时间/天气/光照)
  id: str(36) PK
  scene_asset_id: FK scene_assets.id CASCADE, index
  name: String(120)                      # 如「清晨教室」「雨夜街道」
  structured_overrides: JSON default {}  # 覆盖 4.2 中 time_of_day/weather/lighting/palette 子集
  # 变体级参考图通过下方关系表表达，禁止 JSON 裸 ID
  is_canonical: bool default False       # 默认变体
  deleted_at: datetime nullable
  version: int

  Index ix_scene_asset_variants_asset_canonical (scene_asset_id, is_canonical)

scene_asset_variant_references
  id: str(36) PK
  variant_id: FK scene_asset_variants.id CASCADE, index
  asset_id: FK assets.id RESTRICT, index
  role: String(32), sort_order: int, created_at
  UniqueConstraint (variant_id, asset_id, role)
```

`scenes` 表变更（**只加列，不删不改 location**）：

```text
scenes ADD
  scene_asset_id: FK scene_assets.id SET NULL, nullable, index
  scene_asset_variant_id: FK scene_asset_variants.id SET NULL, nullable
```

语义：`Scene.scene_asset_id` 表示「当前采用的场景资产」，跟随资产最新版本（不锁版本）；**版本锁定发生在生成边界**（§6），不是场景引用层。`scene_asset_variant_id` 表示当前生效变体。

服务端绑定不变量：Scene、SceneAsset 必须属于同一 project；variant 非空时必须属于所给 SceneAsset；软删资产/变体不得绑定。违反任一条件返回 422，不能依赖前端过滤。

### 4.2 structured 结构化字段契约

```json
{
  "place": "校园·教学楼",                  // 地点/场所（可含层级）
  "subareas": ["高三（2）班教室", "走廊", "天台"],
  "interior": true,                       // 室内/室外
  "time_of_day": "day",                   // dawn | day | dusk | night | ""(未指定)
  "weather": "clear",                     // 自由文本或枚举，保留自由文本以兼容解析
  "season": "spring",                     // 自由文本或枚举
  "lighting": "soft_diffuse",             // 自由文本或枚举
  "palette": { "dominant": ["#f2efe9"], "mood": "bright" },   // 色彩基调，mood 为语义词
  "fixed_props": ["讲台", "黑板", "窗"],  // 固定物件
  "spatial_relations": [                  // 空间关系
    { "from": "讲台", "to": "黑板", "relation": "in_front_of" }
  ]
}
```

契约约束：

- `structured` 是**规范化输入**，用于筛选（V02-21）、匹配与提示词编译；`description` 是**兜底描述**。
- 键集合固定（本契约枚举）；未知键由服务端拒绝（`extra="forbid"` 的 Pydantic schema）。值多为自由文本以兼容 AI 解析，枚举字段（interior/time_of_day）服务端校验合法值。
- **消费优先级（高→低）**：`structured` 结构化字段编译 → `description` 资产兜底 → `Scene.location` 历史文本（未绑定资产时）。（§5）

### 4.3 ASSET_KINDS 扩展

`uploads.py:37-44` 的 `ASSET_KINDS` 增加：`"scene": "SCENE_REFERENCE"`（以及 `"SCENE_REFERENCE": "SCENE_REFERENCE"` 别名，与既有大小写别名惯例一致）。场景参考图走既有 `/uploads` 通道，**不新开上传端点**；文件归属、sha256 去重、像素/解压炸弹/20MB 限制、缩略图全部复用 `Asset` 既有安全面（P1-15/P2-4 已收紧）。

---

## 5. 描述兜底与结构化字段的优先级

定义单一消费函数 `resolve_scene_background(db, scene) -> SceneBackgroundSpec`（V02-20 实现，供 `content_workflow.py:661,725` 替换直接读 `scene.location`）：

```text
1. scene.scene_asset_id 有值 且 资产存在且未软删：
     → 优先读 structured 结构化字段 → 编译为规范地点描述（place + interior + time_of_day + weather + lighting + fixed_props 摘要）
     → structured 为空/缺键时读 description
     → 变体（scene_asset_variant_id 有值）用 structured_overrides 覆盖对应键后参与编译
2. 否则：返回 scene.location 原文（历史行为完全不变）
```

- 结构化字段**不存储**在 `Panel.background`（生成时仍落文本快照，保持候选不可变性）；它只在编译期提升生成输入质量。
- `location` 列保留、继续可编辑；在绑定资产前它仍是有效兜底。绑定资产后 `location` 编辑不再影响生成（优先级被资产覆盖），但保留供「解绑后回退」。

---

## 6. 页面/格子/任务/候选锁定实际场景版本

区分**当前采用**（跟随最新）与**生成锁定**（历史事实）：

| 层 | 锁定方式 | 语义 |
| --- | --- | --- |
| `Scene.scene_asset_id` | 外键引用 | 当前采用，跟随资产最新版本；资产修订时场景不自动改，但 `mark_pages_for_review` 将相关页面标记待复核 |
| 页面 `MangaPage.scene_ids` | 既有 JSON id | 只表达「本页涉及哪些场景」，不锁资产版本 |
| 格子 `Panel.background` | 既有文本快照 | 生成时的场景描述副本（既有无损快照模式） |
| 任务 `JobAssetReference` | 既有租约（RESTRICT） | 场景参考图在排队/执行期间被锁定，`_ensure_asset_not_in_active_job` 阻止删除（§8） |
| 候选 `PageCandidate.prompt_snapshot` | JSON 扩展 | 新增 `scene_asset_id` + `scene_asset_version` + `scene_asset_variant_id` + 变体 `structured_overrides` 快照（V02-20 实现）；`GenerationRecord.input_versions` 记录同快照 |

**版本锁定要点**：

- 候选的 `prompt_snapshot` 是**生成时快照**，即使场景资产随后被修订，候选引用的版本事实不变（对齐 `based_on_storyboard_version` 的既有模式）。
- 资产修订后 `mark_pages_for_review` 触发页面复核（沿用 `sources.py:377-382` 的既有机制，`reference_kind="scene_asset"`）；复核通过重新生成时新候选记录新版本快照。
- 不新增全局「场景资产版本号表」；`SceneAsset.version`（Timestamped 乐观锁）即版本事实，快照在候选侧。

---

## 7. 软删除、恢复、版本升级和引用完整性

### 7.1 软删除与恢复

- `SceneAsset` / `SceneAssetVariant` 软删除（`deleted_at`），对齐 `Asset` 语义；物理文件由 `Asset` 管理，删除场景资产**不删除** `Asset` 文件（场景资产只是引用方）。
- 恢复：`POST .../scene-assets/{id}/restore` 清 `deleted_at`；被软删资产不得进入生成消费（`resolve_scene_background` 与候选绑定均排除软删）。
- 引用方对软删资产保持引用有效（`Scene.scene_asset_id` 仍指向它），但消费函数返回兜底文本并标记 UI「资产已归档」，提示用户在资产工作区恢复或改绑。

### 7.2 版本升级

- `SceneAsset.version` / `SceneAssetVariant.version` 随 PATCH 递增（乐观锁，`update_scene` 同款 409 语义）。
- 升级只改变「当前采用」；已锁定版本快照的候选/记录不受影响（§6）。

### 7.3 引用完整性

- `Scene.scene_asset_id` → `SceneAsset`（FK `SET NULL`）：场景资产被软删/物理删除时场景回退到 `location` 文本兜底，**不产生孤儿外键**。
- `SceneAssetReference.asset_id` → `Asset`（FK `RESTRICT`）：参考图被活动任务租约占用时，删除资产被 409 拦截（复用 `_ensure_asset_not_in_active_job`）。
- `_detach_reference_asset` 扩展：删除被引用的 Asset 时，同时处理 `SceneAssetReference` 与 `SceneAssetVariantReference`；活动任务租约仍先返回 409。解绑后重算对应 `SceneAsset.status`，不得留下变体关系中的悬空 ID。
- 删除 `SceneAsset` 时其 `references` / `variants` 级联删除（CASCADE）。

---

## 8. 资产租约、任务执行中删除、旧 Worker 晚返回

- **租约**：场景参考图在任务排队/执行期间进入 `JobAssetReference`；租约集合必须同时包含资产级与当前变体关系表中的 asset_id。任务执行中删除任一参考图返回 409。
- **任务执行中删除**：软删除的 `Asset`/`SceneAsset` 仍可被在途任务读取（`storage_key` 有效），但不得被新任务绑定；任务完成前不物理删除文件（对齐既有 `Asset` 软删语义）。
- **旧 Worker 晚返回**：租约失效/取消后 Worker 不得写回候选或状态（既有 P1-7/P1-9 机制，本设计不重复实现）。场景版本锁定在候选 `prompt_snapshot`（生成时快照），旧 Worker 晚返回不改变候选已锁定的版本事实；若晚返回结果被采用，`resolve_scene_background` 只影响**新**生成，历史候选版本不变。
- **变体版本锁定**：同 §6，候选快照含 `scene_asset_variant_id` 与其 `structured_overrides` 副本，变体后续修改不影响历史候选。

---

## 9. 场景变体：时间、天气、光照

`SceneAssetVariant` 是 `SceneAsset` 的维度覆盖，只允许覆盖 `structured_overrides` 中的 `time_of_day / weather / lighting / palette`（以及可选的 `season`），**不允许**改 `place / subareas / interior / fixed_props / spatial_relations`（结构不变，只换时空光）。服务端校验：`structured_overrides` 键必须是上述允许子集。

- 默认变体：`is_canonical=true`（每资产至多一个）。
- 当前生效：`Scene.scene_asset_variant_id` 指向变体；为 NULL 时用资产默认变体/资产级结构化字段。
- 消费：`resolve_scene_background` 用变体覆盖键后编译（§5）。
- V02-21 UI 展示「当前页面/场景采用的时间、天气和光照变体」（roadmap V02-21 明确要求）。

---

## 10. API 读写边界

**管理端（V02-21 消费，全部项目作用域、校验项目存在与软删）**：

| 方法/路径 | 载荷/返回 | 语义 |
| --- | --- | --- |
| `POST /projects/{project_id}/scene-assets` | name, description?, structured?, location_hint? | 创建资产（含「从 location 创建」变体，见 §11） |
| `GET /projects/{project_id}/scene-assets` | `?status=&include_deleted=&place=&interior=&limit=&offset=` | 分页列表（§13），返回 `SceneAssetRead` |
| `GET /projects/{project_id}/scene-assets/{id}` | `SceneAssetRead`（含 references + variants） | 详情 |
| `PATCH /projects/{project_id}/scene-assets/{id}` | name/description/structured/status, version | 改字段或确认状态；乐观锁 409 |
| `POST .../scene-assets/{id}/restore` | `SceneAssetRead` | 恢复软删 |
| `DELETE .../scene-assets/{id}` | 204 | 软删除（被活动任务引用的 reference 资产 409） |
| `POST .../scene-assets/{id}/references` | `{ asset_id, role? }` | 绑定参考图 |
| `DELETE .../scene-assets/{id}/references/{asset_id}` | 204 | 解绑参考图（不删文件） |
| `POST .../scene-assets/{id}/variants` | name, structured_overrides, is_canonical? | 创建变体 |
| `PATCH .../scene-assets/{id}/variants/{variant_id}` | name/structured_overrides/is_canonical, version | 改变体/设默认 |
| `DELETE .../scene-assets/{id}/variants/{variant_id}` | 204 | 软删变体 |
| `PATCH /scenes/{scene_id}/bind-asset` | `{ scene_asset_id?, scene_asset_variant_id? }` | 场景绑定/解绑资产与变体；成功后 `mark_pages_for_review` |

**创作端（V02-20 生成消费）**：

- `SceneRead`（`schemas.py:264-279`）扩展 `scene_asset_id`、`scene_asset_variant_id`（只读）。
- `content_workflow.py:661,725` 改用 `resolve_scene_background`（§5）。
- 场景参考图绑定后进入 `JobAssetReference` 租约（§8）。

**边界**：上传继续走 `/uploads`（`kind=scene`），不新增上传端点；资产内容/缩略图继续走 `Asset` 既有端点；删除资产走 `Asset` 既有删除端点（含新扩展的解绑）。

---

## 11. 现有 location 文本的无损兼容迁移与 backfill

### 11.1 决策

**迁移不改写任何 `scenes.location` 文本、不自动回填 `SceneAsset` 行、`scene_asset_id` 置 NULL。** 历史项目升级后行为完全不变（消费函数走 location 兜底）。从文本升级为资产是 V02-21 UI 的显式用户动作：

- V02-21 提供「从 location 创建资产」：以 `name=location`（截断 120）、`location_hint=location`、`description=location` 创建 `SceneAsset` 并可选绑定到场景——**应用层显式动作，不是迁移副作用**。这避免了为无参考图、无结构化字段的历史场景批量伪造「资产」造成噪音。

### 11.2 upgrade / backfill / downgrade

迁移 `20260830_20_scene_assets.py`（`down_revision = "20260830_19"`，当前 head）：

```text
upgrade:
  1. create_table scene_assets（§4.1）
  2. create_table scene_asset_references（§4.1）
  3. create_table scene_asset_variants（§4.1）
  4. create_table scene_asset_variant_references（§4.1）
  5. add_column scenes.scene_asset_id   nullable=True（不填值）
  6. add_column scenes.scene_asset_variant_id  nullable=True（不填值）
  7. add_index 上述外键与复合索引
  （backfill：无数据操作。location 文本零触碰。）

downgrade:
  1. 预检 scenes 两个新 FK 全为 NULL，且四张新表均为空；任一条件不满足即明确拒绝 downgrade
  2. drop scenes.scene_asset_variant_id / scenes.scene_asset_id
  3. drop_table scene_asset_variant_references / scene_asset_variants / scene_asset_references / scene_assets
  4. 禁止为通过 down 临时删除、回填或改写用户场景资产；location 原样
```

- 迁移内禁止 import `Settings`、禁止读凭据、禁止任何对既有行的 `UPDATE`（对齐 V02-02 M7 禁止条款）。
- 三张新表各自带 schema 所有权校验（对齐 `20260829_18` 迁移风格），防止人工建表冲突。

### 11.3 SQLite 与 PostgreSQL 差异

| 面 | SQLite | PostgreSQL | 契约 |
| --- | --- | --- | --- |
| 建表/加列 | 直接 DDL；`ADD COLUMN nullable` 简单 | 同 DDL | 无 batch_alter 需要（仅 nullable 加列） |
| 外键 | 需 `PRAGMA foreign_keys=ON`（项目已启用）；`SET NULL`/`RESTRICT` 支持 | 原生严格 | `scenes.scene_asset_id` 用 `SET NULL`，`references.asset_id` 用 `RESTRICT` 两库一致 |
| JSON 筛选 | 无 jsonb，JSON 列不参与索引/查询谓词 | jsonb 可用但**不使用** | 保持 JSON 为不透明载荷；筛选一律走关系列（project_id/status/deleted_at/name），保证两库行为一致 |
| 唯一约束 `(project_id, name)` | 支持 | 支持 | 两库一致 |

真实 PostgreSQL 升降级验收：**NOT RUN**（沿用项目既有真实 PG 边界；SQLite 往返不替代）。

---

## 12. 安全、上传、文件归属

- **上传**：`kind=scene → SCENE_REFERENCE` 走既有 `/uploads`，全部安全面复用：sha256 去重（同项目同文件返回既有 asset）、REFERENCE_IMAGE_TYPES 白名单（png/jpeg/webp）、像素限制/解压炸弹异常（P2-4）、请求总量限制（P1-15）、20MB 上限。
- **文件归属**：`SceneAssetReference.asset_id` 引用 `Asset`；物理文件归 `Asset`（`storage_key` 相对 `upload_root`），场景资产只存引用。缩略图复用 `Asset` 缩略图端点。
- **服务端路径不外泄**：API 返回 `asset_id` 而非绝对路径；内容访问走既有 `FileResponse` 端点（`uploads.py:325-337`）。
- **项目作用域**：所有 scene-asset 路由校验 `project_id` 存在且未软删（对齐 `list_assets`）。

---

## 13. 索引、查询和分页

- `scene_assets`：`ix_scene_assets_project_deleted_created (project_id, deleted_at, created_at)`（对齐 `ix_assets_project_deleted_created`）；`(project_id, name)` 唯一约束兼查询索引。
- `scene_asset_references`：`scene_asset_id`、`asset_id` 各一索引。
- `scene_asset_variants`：`(scene_asset_id, is_canonical)` 复合索引。
- `scenes.scene_asset_id`：单列索引（按资产反查场景）。
- **分页**：`GET .../scene-assets` 用 `limit/offset` + `(deleted_at IS NULL)` 默认过滤；`include_deleted=true` 时含软删（`ORDER BY deleted_at NULLS LAST` 对 PostgreSQL，SQLite 用 `coalesce(deleted_at, created_at)` 兜底——由实现 Issue 按两库行为固化）。
- **筛选**：`status`/`interior`/`place` 前缀搜索走关系列或 Python 层过滤（场景资产单项目量级通常 <100，Python 过滤可接受；PostgreSQL jsonb 优化列为 NOT RUN 的演进项，不阻塞）。

---

## 14. 测试矩阵（拆 V02-20 实现 / V02-21 UI）

### 14.1 V02-20（后端实现，L3）

| 编号 | 场景 | 类型 |
| --- | --- | --- |
| S1 | 迁移：空库与含历史场景库 `upgrade→downgrade→upgrade` 往返；`scenes.location` 逐字段不变、`scene_asset_id` 为 NULL；三新表结构校验 | 新增 pytest（对齐 `20260829_18` 所有权校验） |
| S2 | `resolve_scene_background` 优先级：structured 完整 → structured 缺键走 description → 未绑定走 location 原文；变体 overrides 覆盖生效 | 新增 pytest |
| S3 | 绑定/解绑：`POST references`/`DELETE references` 不删文件；`(scene_asset_id, asset_id, role)` 唯一约束；重复绑定 409 | 新增 pytest |
| S4 | 软删/恢复：软删后不进入消费与候选绑定；恢复后恢复消费；被活动任务租约引用的 reference 资产删除 409 | 新增 pytest |
| S5 | 场景绑定：`PATCH /scenes/{id}/bind-asset` 后 `mark_pages_for_review` 触发页面复核；解绑回退 location 兜底 | 新增 pytest |
| S6 | 候选版本锁定：生成时 `prompt_snapshot` 含 scene_asset_id/version/variant 快照；资产后续修订不改变历史候选快照 | 新增 pytest（扩展 `test_*_candidate`） |
| S7 | 租约：引用场景资产的生成任务创建时 `JobAssetReference` 写入参考图；活动任务期间删除该资产 409 | 新增 pytest |
| S8 | 变体：`structured_overrides` 只允许 time/weather/lighting/palette/season；越界键 422；is_canonical 每资产至多一个 | 新增 pytest |
| S9 | API 边界：分页/筛选/include_deleted；`extra="forbid"` 拒绝未知 structured 键；乐观锁 409；项目软删 404 | 新增 pytest |
| S10 | `ASSET_KINDS` 扩展：`kind=scene` 上传成功；同 sha256 去重；安全面回归（像素/解压炸弹/总量）不退化 | 扩展既有 `test_uploads` |
| S11 | `_detach_reference_asset` 扩展：删除被场景引用的 Asset 时解绑 SceneAssetReference 并重置资产状态 | 扩展既有删除测试 |

### 14.2 V02-21（UI 工作区，L2）

| 编号 | 场景 | 类型 |
| --- | --- | --- |
| U1 | 工作区列表：创建/上传参考图/生成图/确认状态流转（UPLOADED→CANONICAL）；筛选与分页 | 组件测试 |
| U2 | 「从 location 创建资产」：历史场景一键升级，location 文本写入 name/location_hint/description | 组件测试 |
| U3 | 变体管理：创建/设默认/软删；页面/场景展示「当前采用的时间/天气/光照变体」 | 组件测试 |
| U4 | 场景绑定：剧本/分镜/生成页绑定资产与变体；解绑回退提示 | 组件测试 |
| U5 | 引用图：上传/绑定/解绑/采纳生成图为参考图（adopt-reference 模式） | 组件测试 |
| U6 | 失败/空/加载态与键盘可达性；删除确认与 409 原文展示 | 组件测试 |
| U7 | `npm run check` 全绿；`git diff --check` 通过 | 门禁 |

---

## 15. 未验证边界（NOT RUN / NOT VERIFIED）

1. **真实 PostgreSQL 升降级未运行**：S1 的 PG 变体需独立环境，SQLite 往返不替代（沿用项目既有真实 PG 边界）。
2. **真实供应商场景图生成未验证**：场景资产生成图走既有生成通道，本任务不调用真实供应商。
3. **性能门禁**：V02-21 的浏览器/Lighthouse/FPS 门禁需独立性能窗口（对齐 `architecture.md:131` 的窗口语义），本任务不运行。
4. **PostgreSQL jsonb 场景筛选优化**：列为演进项，不进入本契约实现范围。
