# V02-03A 导演指令与候选血缘契约（设计草案）

- 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（origin/master）
- 工作分支：`codex/v02-03a-director-command-lineage`
- 任务性质：**L3 设计草案**，不实现代码/迁移/测试，不拥有最终批准权；实现批准权在组长
- 前置：`docs/v02-provider-neutrality-audit.md`（供应商契约与账本边界）；本设计的账本与供应商字段沿用该文档 §4
- 定位：把"自然语言导演指令"约束为**可校验、可预览、可逐条接受的结构化编辑命令**，并把页面候选的派生关系（修复/升清/局部重抽卡）从隐式约定升级为**显式血缘**。所有"现状"结论均带基线文件/行号

## 1. 现状盘点

### 1.1 数据结构现状

| 对象 | 基线位置 | 关键字段 | 与本设计的关系 |
| --- | --- | --- | --- |
| `MangaPage` | `apps/api/app/models.py:255-280` | `revision_no`:262、`panel_count`:264、`resolution`:266、`status`:268、`scene_ids`:269、`beat_ids`:270、`locked_fields`:271、`estimated_text_chars`:272、`estimated_bubbles`:273、`source_coverage`:274、`selected_candidate_id`:275、`storyboard_version`:276、`selected_candidate_ack_version`:277、`continuity_status`:278 | 页级目标与失效计数器载体 |
| `Panel` | `models.py:283-309` | `reading_order`:291、`bounds`:292、`shot_type`:293、`camera_angle`:294、`camera_height`:295、`characters`:296、`character_presence`:297、`props`:298、`outfits`:299、`actions`:300、`expressions`:301、`background`:302、`bubble_regions`:303、`sound_effects`:304、`bleed`:305、`borderless`:306、`locked_fields`:307；继承 `Timestamped.version` | 格级命令的主要目标；字段级锁 |
| `Dialogue` | `models.py:312-323` | `speaker_character_id`:318、`target_text`:319、`reading_order`:320（panel 内唯一约束 :314）、`text_direction`:321、`region`:322、`rewrite_forbidden`:323 | 气泡即 Dialogue 行；`bubble_regions`（Panel:303）是其视觉区域缓存 |
| `Scene` | `models.py:214-231` | `location`:222、`time_label`:223、`weather`:224 | 场景/时间/天气命令的落点 |
| `Asset` | `models.py:187-215` | `kind`:198、`source`:209（`USER_UPLOAD`/`AI_GENERATED`/`VERTEX_GENERATED`）、`status`:210、`sha256` | 参考图与输出图统一载体 |
| `GenerationBatch` | `models.py:683-698` | `ordinal`:695（项目内唯一）、`generation_kind`:696（`PAGE`/`REPAIR`/`UPSCALE`）、`status`:697 | 一轮抽卡会话 |
| `PageCandidate` | `models.py:701-735` | `ordinal`:715（batch 内唯一 :704）、`model_alias`:716、`catalog_model_id`:717、`resolution`:720、`status`:721、`asset_id`:722、`job_id`:725、`generation_record_id`:728、`based_on_storyboard_version`:731、`is_favorite`:732、`is_selected`:733、`prompt_snapshot`:734、`deleted_at`:735 | 血缘主体（现状无显式父指针） |
| `AssetCandidate` | `models.py:738-766` | `variant`:752、`instruction`:753 | 人物/服装/风格资产候选，暂不纳入导演命令范围 |
| `GenerationJob` | `models.py:338-383` | `target_type/target_id`:355-356、`job_type`:357、`attempt_count/max_attempts`:362-363、`request_parameters`:368、`idempotency_key`:370（唯一）、`lease_owner/lease_expires_at`:374-377 | 派生任务的执行载体 |
| `JobAssetReference` | `models.py:399-410` | job↔asset 租约（batch 内唯一 :401） | 参考资产锁定 |
| `RepairPlan` | `models.py:601-618` | `inspection_result_id`、`repair_type`、`target_regions`:609、`target_fields`:610、`lock_conflicts`:611、`automatic_attempts/max_automatic_attempts`:612-613 | 修复范围与自动重试上限 |

### 1.2 结构化编辑面现状（无自然语言通道）

现有分镜编辑全部是**人工 API 调用**，规则集中在 `apps/api/app/api/routes/workflow/storyboard.py` 与 `apps/api/app/services/editor.py`：

- 格更新走 `panel.version` 乐观并发（`storyboard.py:94-95`），字段锁经 `ensure_unlocked`（`storyboard.py:98`，实现于 `domain/states.py`），校验后逐字段 `setattr` 并 `panel.version += 1`（`storyboard.py:166-168`）。
- 每次格/气泡变更：`mark_storyboard_changed`（`editor.py:8-10`）把 `page.storyboard_version += 1` 且 `selected_candidate_ack_version = None`；随后 `mark_pages_for_review`（`editor.py:64-90`）把本页及之后的页 `continuity_status = "NEEDS_REVIEW"`。
- 气泡（Dialogue）增改删都会 `refresh_page_text_metrics`（`editor.py:93-107`），执行每页 180 字硬上限（:102-103）与 8 气泡硬上限（:104-105）。
- **当前不存在任何自然语言模型写入数据库或文件的通道**；结构化输出由人工接口 schema 校验承担。

### 1.3 生成/修复/升清血缘现状

- 修复：新建 `generation_kind="REPAIR"` 的 batch 与 ordinal=1 的候选（`workflow/inspection.py:111-121`），父候选只存在于 `request_parameters["original_candidate_id"]`（`inspection.py:159`），Worker 侧读取（`worker_handlers/page_generate.py:318`）。**没有一等血缘列**。
- 修复范围约束是**提示词文本**（`page_generate.py:338-343`，含 `target_regions`:336），没有 mask 数据结构；升清约束同为提示词（`page_generate.py:344-349`，`preserve_structure`:235）。
- 自动修复上限与升级序：`RepairPlan.automatic_attempts/max_automatic_attempts`（`inspection.py:129-130`）、修复范围只能保持或扩大（`inspection.py:100-105`）、全局 `max_auto_repairs`（`config.py:43`）。
- 原候选/原资产从不被改写：产物写入新候选、新资产（`page_generate.py:406-441`；资产按 sha256 去重 :193-201，失败清理 :250-253）。采用状态只在 `MangaPage.selected_candidate_id`（:275）+ `PageCandidate.is_selected`（:733）。
- 过期防护：调用前 `based_on_storyboard_version != page.storyboard_version` 抛 `StaleStoryboardVersionError`（`page_generate.py:262-265`）；调用后 `db.refresh(page)` 再核对，分镜在途变化时**保留结果但暴露为过期候选**（`page_generate.py:402-405`），并由 `STORYBOARD_VERSION_UNCONFIRMED` 阻断生产通过（`page_completion.py:90-98`）。
- 租约与取消：`lease_owner/lease_expires_at`（`models.py:374-377`）由执行外壳统一收敛（`docs/architecture.md:67,83-87`）；参考资产先租约再重读核对（`worker_handlers/provider.py:296-315`、`page_generate.py:374-386`）。
- 幂等：`GenerationJob.idempotency_key` 全局唯一（`models.py:370`）；修复 `repair:{repair_plan_id}`、升清 `upscale:{batch_id}:{resolution}`（`inspection.py:166,241`）；候选序号冲突显式 409（`workflow/generation.py:90`）。

## 2. 总原则：自然语言模型只产出受约束命令

1. 自然语言模型（经 `structured_text` 能力调用，走 V02-10A 之后的统一账本）**只能输出**符合本契约 envelope 的编辑命令 JSON；它不获得任何数据库连接、文件路径或执行权。
2. 每条命令必须通过**确定性校验器**（复用 §1.2 现有校验语义）才会进入预览；校验器只允许 envelope 声明的字段。
3. 命令的**落库执行器是现有结构化编辑服务**（`storyboard.py`/`editor.py` 的服务层等价物），不新增第二条写路径。
4. 模型原始输出原文不落库为可执行数据，只作为审计快照保存在命令 envelope 的 `source` 段（脱敏、限长，见 §9）。

## 3. 命令 envelope

```json
{
  "schema_version": 1,
  "command_id": "uuid（客户端生成，服务端唯一键）",
  "command_group_id": "uuid（一次导演会话产出的一组命令）",
  "created_at": "RFC3339 UTC",
  "target": {
    "project_id": "必填",
    "page_id": "页级命令必填",
    "panel_id": "格/气泡命令必填",
    "dialogue_id": "气泡命令必填",
    "scene_id": "场景上下文命令必填",
    "asset_id": "仅局部重抽卡引用 mask/参考时出现"
  },
  "expected_version": {"scope": "panel|page|storyboard|scene", "value": 12},
  "retry_of_command_id": "仅重试失败命令时出现",
  "operation": "update_panel_shot|update_panel_cast|update_panel_layout|update_scene_context|update_dialogue|move_dialogue|regenerate_region|update_page_layout",
  "payload": {"仅声明于 §4 操作白名单内的字段"},
  "source": {
    "user_prompt": "用户原话（截断上限 4000 字符）",
    "reference_asset_ids": ["引用的参考图"],
    "model": {"provider": "...", "catalog_model_id": "...", "model_id": "..."},
    "raw_output_id": "模型原始输出审计 id（不内嵌原文）"
  }
}
```

规则：

- `command_id` 是幂等键：同一 `command_id` 重复提交返回**首次结果**（成功或校验错误），不产生第二条命令——与 `GenerationJob.idempotency_key`（`models.py:370`）同语义。
- `expected_version` 按 `scope` 映射：`panel` → `Panel.version`；`page` → `MangaPage.version`；`storyboard` → `MangaPage.storyboard_version`；`scene` → `Scene.version`。不匹配返回 409 并附当前值（沿用 `storyboard.py:94-95` 行为）。
- `target` 的所有 id 必须属于 `project_id`（复用 `validate_character_ids` 同款项目归属校验，`editor.py:36-54`）；跨项目/不存在 → 422。
- 一个命令只允许一个非空目标层级；批量变更多目标时拆成多条命令放入同一 `command_group_id`，逐条独立接受/拒绝。
- `payload` 大小上限 16KB；未知字段一律拒绝（模型输出不可信，见 §9）。

## 4. 操作分类与 payload 白名单

| 分类 | operation | payload 白名单 | 落点与校验（现状语义复用） |
| --- | --- | --- | --- |
| 页面与格子布局 | `update_page_layout` | `panel_count`, `layout_mode` | `update_page_layout`（`storyboard.py:65-84`）；格数变化触发 storyboard_version 失效链 |
| | `update_panel_layout` | `bounds`, `reading_order`, `bleed`, `borderless` | `Panel` 字段（`models.py:291-306`）；`reading_order` 冲突 → 409 |
| 镜头 | `update_panel_shot` | `shot_type`, `camera_angle`, `camera_height`, `background`, `sound_effects` | 枚举/长度白名单；`Panel:293-295,302,304` |
| 角色姿态/表情 | `update_panel_cast` | `characters`, `character_presence`, `outfits`, `expressions`, `actions` | 必须复用 `storyboard.py:101-165` 的全部校验：项目内角色（:103）、服装归属角色与项目（:136-142）、表情归属（:143-146）、`actions.source_text` 保留（:160-165）；字段锁经 `ensure_unlocked` |
| 场景/时间/天气 | `update_scene_context` | `location`, `time_label`, `weather`, `background` | target 必须携带 `scene_id` 且 expected scope=`scene`；Scene 属于项目并被目标页引用；写入后从引用该 Scene 的最早页面开始失效，不得只处理当前页 |
| 气泡位置/大小/文字 | `update_dialogue` | `target_text`, `text_direction`, `region`, `speaker_character_id`, `rewrite_forbidden` | 说话人规范化/歧义 409（`editor.py:17-33,42-49`）；空文字 422（`storyboard.py:230-231`）；`rewrite_forbidden` 缺省 true（`models.py:323`） |
| | `move_dialogue` | `reading_order`, `region` | panel 内唯一约束（`models.py:314`） |
| 局部重抽卡 | `regenerate_region` | `instruction`, `target_regions`, `mask`（见 §7）, `model_alias`, `resolution` | 产生派生候选任务（§7）；指令进入提示词，mask 由服务端生成存储 |

结构化格/气泡命令执行后按现有编辑服务级联 `refresh_page_text_metrics`（气泡类）、对象 version、`mark_storyboard_changed` 与 `mark_pages_for_review`。`regenerate_region` 只创建派生候选，不修改分镜对象或 `storyboard_version`；`update_scene_context` 递增 `Scene.version`，并复用 scene 引用扫描找到所有引用页中的最早页。

## 5. 预览、diff、逐条接受、确认、撤销与重做

### 5.1 命令组状态机

```text
PROPOSED ──校验通过──> PREVIEWED ──逐条接受──> PARTIALLY_ACCEPTED ──全部终态──> COMMITTED / PARTIALLY_REJECTED
    │                      │                                              │
    └── 校验失败 → REJECTED └── 用户放弃 → DISCARDED                          └── 确认后入队派生任务（如有）
```

- `PROPOSED`：模型输出原始命令集（未落业务表，仅审计行）。
- `PREVIEWED`：服务端对每条命令跑确定性校验器 + 生成结构化 diff；失败的命令标注原因，不影响其他命令预览。
- 逐条接受/拒绝：接受的命令按 `expected_version` 串行执行（同一 target 内强制串行，跨 target 可并行）。
- 确认（二次确认）：触发付费派生任务（`regenerate_region`）的命令在接受后仍需显式确认，与既有图片测试付费确认模式一致（`provider-management.tsx:176` 的交互契约）。

### 5.2 结构化 diff

- 字段级 `{field: {before, after}}`，`before` 取自 `expected_version` 对应时刻的值；数组/字典给出逐元素增删改。
- 气泡文字 diff 附加字数变化与是否触碰 180 字/8 气泡余量（`editor.py:102-105`）。
- `regenerate_region` 的 diff 是"将生成新派生候选"的预告（父候选、区域、模型、分辨率），不是图像 diff。

### 5.3 撤销与重做

- 每条已执行命令生成一条**逆命令**（同 envelope，`operation` 为逆操作），或对不可逆操作（`update_page_layout` 减格）记录执行前页面快照段（panels+dialogues 的 JSON 导出，限长）。
- 撤销/重做本身也是命令：进入同一 journal、占用新的 `command_id`、遵守 `expected_version`；不使用"回滚数据库"语义，避免与乐观并发和检查失效链冲突。
- journal 只追加；`MangaPage.storyboard_version` 是撤销正确性的对账锚点（撤销失败时 journal 标记 `SUPERSEDED`，提示用户刷新）。

## 6. 幂等、乐观并发、过期版本、重复提交与事务边界

1. **幂等**：`command_id` 与派生任务幂等键（`repair:{repair_plan_id}` 模式，`inspection.py:166`）两级去重；命令表唯一键 `(project_id, command_id)`。
2. **乐观并发**：见 §3 `expected_version`；命令执行失败 409 时命令组内该条回到 `PREVIEWED` 并携带当前版本，不自动重试。
3. **过期版本**：预览与执行之间分镜可能变化——执行前重读版本，不匹配即 409；`regenerate_region` 另受 `based_on_storyboard_version` 前置检查（`page_generate.py:262-265`）。
4. **重复提交与重试**：同 `command_id` 重复 POST 返回首次执行结果（HTTP 200 + `idempotent_replay: true` 标记）；对失败结果重试必须生成新 `command_id` 并携带 `retry_of_command_id`，或重试原已存在 Job，不得用同一命令 ID 假装重新执行；不同 command_id 对同一 target 的并发提交由 `expected_version` 串行化。
5. **事务边界**：单条命令的"校验+落库+失效联动+version 递增"在一个事务内（沿用 `storyboard.py:166-171` 模式）；派生候选+RepairPlan/血缘记录+任务在同一事务创建，`enqueue` 在事务提交后（沿用 `inspection.py:111-171` 模式，符合 `docs/architecture.md:140` 的事务所有权约定）。命令 journal 与业务变更同事务写入。

## 7. 候选血缘模型（设计，不实现）

新增一等血缘（替代 `request_parameters["original_candidate_id"]` 的隐式约定，`inspection.py:159`）：

```text
CandidateLineage
  id
  child_candidate_id  → page_candidates.id（唯一）
  parent_candidate_id → page_candidates.id（RESTRICT；候选采用软删除，不允许物理清理仍被引用的父候选）
  lineage_kind        = GENERATED | REPAIRED | UPSCALED | REGION_REGENERATED
  source_command_id   → command journal（可空）
  mask_asset_id       → assets.id（可空，仅 REGION_REGENERATED）
  model_alias / catalog_model_id / resolution   （冗余自候选，便于账本聚合）
  created_at
```

- **mask 载体**：`Asset(kind="region_mask", source="AI_GENERATED", mime_type="image/png")`，由服务端按 `target_regions`/前端笔刷栅格化生成；存储路径沿用 `storage/generated/...` 规则（`page_generate.py:202-210`），禁止模型或客户端提供路径。多边形 JSON 副本存于 `payload`（限 64 点/区域）。
- **参考资产**：沿用 `JobAssetReference` 租约（`models.py:399-410`）；父候选输出资产自动成为派生任务首张参考（现状 `inspection.py:165,240` 的行为一等化）。
- **模型参数**：`model_alias/catalog_model_id/resolution` 已在候选列（`models.py:716-720`）；`prompt_snapshot` 增补 `lineage` 段（`operation/parent/mask checksum`），保持现有 snapshot 键（`page_generate.py:351-356`）不变。
- **采用状态**：血缘不改变采用语义。采用、撤回与换选必须在同一事务维持双字段不变量：`MangaPage.selected_candidate_id` 指向的候选必须是同页唯一 `PageCandidate.is_selected=true`；页面无暂选时同页所有候选均为 false。派生候选继承 `is_favorite=false`，收藏互不影响。
- **局部编辑不变式**（红线）：
  1. 派生候选永远写入新 batch/新 ordinal（现状已满足：`inspection.py:111-121`），禁止覆盖父候选的 `asset_id/prompt_snapshot/status`。
  2. 禁止隐式整页重生：`REGION_REGENERATED` 任务的提示词必须包含 mask 区域约束（现修复文本约束 `page_generate.py:338-343` 的强化版），且任务参数必须携带 `mask_asset_id`；无 mask 的整页重生必须显式声明 `REPAIRED(FULL_PAGE)` 并重新走完整检查失效。
  3. 血缘链可追溯：`child → parent → …` 允许深链但账本与检查器只消费直接父子。

## 8. 旧 Worker、晚返回、取消、租约与资源所有权

- **晚返回**：付费调用返回时重读 `storyboard_version`，变化则保留产物但候选暴露为过期（现状 `page_generate.py:402-405`）；血缘列的写入不受该分支影响（同事务收尾）。导演命令引入的 `command_id` 随候选持久化，过期候选仍可按血缘追溯到命令与父候选。
- **取消**：调用前取消不创建 attempt；调用期间取消且供应商已返回时，attempt 可为 SUCCEEDED，但取消检查发生在资产保存前，因此无 GenerationRecord、无持久输出，候选最终为 CANCELLED。血缘行作为已创建任务事实保留，不把未落盘结果描述成“无归属产物”。
- **租约失效**：租约到期由执行外壳重新调度或判死（`architecture.md:67`）；血缘行不参与租约，Worker 崩溃后重试在同一 `child_candidate_id` 上继续（幂等键不变）。
- **失败恢复**：资产写入失败时清理半成品文件与缩略图（现状 `page_generate.py:250-253`）；血缘行在候选达到终态（READY/FAILED）时才对查询可见（`visible` 标志或状态过滤），避免悬空引用。
- **资源所有权**：mask 与输出资产归属 project（`Asset.project_id`）；孤儿 mask 的清理复用素材清理策略，不在本契约内新造删除通道。

## 9. 安全边界

1. **模型输出不可信**：envelope JSON 必须过严格 schema（未知字段拒绝、类型白名单、字符串长度上限）；命令内容不进入 shell/SQL 模板；`user_prompt` 与 `raw_output_id` 只作审计展示。
2. **目标白名单**：target 必须存在、属于 `project_id`、且命令发起者是该项目成员（单用户产品下即项目归属校验）；`scene_id ∈ page.scene_ids`、角色/服装归属校验全部复用现有服务（§4）。
3. **路径禁止**：模型与命令 payload 中不出现任何文件路径/URL/storage_key；mask 与输出路径一律服务端生成（`page_generate.py:202-210` 规则）。
4. **字段大小限制**：`payload ≤ 16KB`；`user_prompt ≤ 4000` 字符；`target_text` 受页面 180 字总量硬上限约束（`editor.py:102-103`）；`target_regions ≤ 64` 点/多边形、每命令 ≤ 8 个区域。
5. **审计**：每次导演会话记录 provider/catalog_model_id/model_id，经统一模型调用账本（`docs/v02-provider-neutrality-audit.md` §4；实现细节归 V02-15A 账本契约）。

## 10. 检查失效矩阵

现状基线：任何格/气泡变更 → `storyboard_version += 1` + 本页起全部 `NEEDS_REVIEW`（`editor.py:8-10,64-90`）。本设计**默认保持该保守行为**，仅提出可收紧的分级（收紧需 V02-40 实测后由组长批准）：

| 命令分类 | storyboard_version | 本页视觉检查 | 本页连续性 | 后续页连续性 | 说明 |
| --- | --- | --- | --- | --- | --- |
| 气泡文字（不超余量） | ++ | 失效（文字在图内） | 保持已通过 | 失效（现状） | 收紧候选：后续页保持有效 |
| 气泡位置/大小 | ++ | 失效 | 失效 | 失效（现状） | 收紧候选：后续页保持有效 |
| 镜头/布局/角色/场景 | ++ | 失效 | 失效 | 失效（现状） | 视觉输入实质变化 |
| `regenerate_region` | 不变 | 新候选独立待检；当前采用候选保持 | 当前采用状态不变 | 当前采用状态不变 | 仅在用户采用派生候选时按既有规则使后续连续性待复查 |
| `update_scene_context` | 引用页各自 ++ | 引用该 Scene 的页面失效 | 失效 | 从最早引用页起失效 | Scene 是共享对象，不能声称目标页外零接触 |
| 其他无关页面 | 不变 | 不变 | 不变 | 不变 | 不引用共享目标且不在失效范围内的页面保持 |

"哪些无关页面保持有效"：目标页之前的所有页面、未被 `mark_pages_for_review` 波及的后续页（若收紧方案获批）以及非同章节页面——现状实现已保证目标页之前页面不动（`editor.py:87-89` 从 `page_number >= start` 才标记）。

## 11. 测试矩阵（可直接拆为 V02-40 / V02-42）

### V02-40 导演命令执行器

| # | 层 | 场景 | 环境 |
| --- | --- | --- | --- |
| E1 | 单元 | envelope schema：未知字段/超长/越权 target 全部 422；`command_id` 缺失 422 | 隔离 SQLite |
| E2 | 单元 | 六类 operation 的 payload 白名单逐字段校验（含枚举与长度） | 隔离 SQLite |
| E3 | 服务 | `update_panel_cast` 复用现有校验：跨项目角色 409、服装错配 409、字段锁 409 | 隔离 SQLite |
| E4 | 服务 | `expected_version` 过期 → 409 + 当前版本；同 `command_id` 重复提交返回首次结果 | 隔离 SQLite |
| E5 | 服务 | 命令组逐条接受：部分拒绝后 COMMITTED/PARTIALLY_REJECTED 状态正确 | 隔离 SQLite |
| E6 | 服务 | 执行器级联：metrics 上限 422、`panel.version`/`storyboard_version`/`ack_version`/`continuity_status` 联动与现状一致 | 隔离 SQLite |
| E7 | API | 逆命令撤销后重做；撤销期间并发编辑 → `SUPERSEDED` | 隔离 SQLite |
| E8 | 并发 | 同一 panel 两条命令（不同 command_id）版本竞争只有一个成功 | 真实 PostgreSQL（NOT RUN 边界） |
| E9 | Worker | `regenerate_region` 派生任务：mask 缺失/父资产删除 → 调用前失败，无付费调用 | 隔离 SQLite + mock 适配器 |
| E10 | Worker | 晚返回与取消：分镜在途变化后产物保留且候选过期（对齐 `page_generate.py:402-405`） | 隔离 SQLite + mock；真实 Worker NOT RUN |

### V02-42 血缘与失效

| # | 层 | 场景 | 环境 |
| --- | --- | --- | --- |
| L1 | 数据 | 血缘唯一性：一个 child 一条 lineage；REPAIR/UPSCALE/REGION_REGENERATED 全部落血缘 | 隔离 SQLite |
| L2 | 数据 | 局部编辑不变式：父候选 asset/snapshot/status 零改动；新 batch/ordinal | 隔离 SQLite |
| L3 | 数据 | 无 mask 的 REGION_REGENERATED 被拒绝；mask 资产 kind/source 正确 | 隔离 SQLite |
| L4 | 迁移 | 历史修复/升清任务回填血缘（`request_parameters.original_candidate_id` → 行），缺失父候选可空处理 | Alembic + 真实 PostgreSQL 升降级（NOT RUN 边界） |
| L5 | 查询 | 血缘链查询（child→parent 深链、按页聚合）与采用状态解耦 | 隔离 SQLite |
| L6 | 失效 | §10 矩阵逐行断言；收紧方案在未获批前不生效 | 隔离 SQLite |
| L7 | Worker | 租约失效后重试不产生第二条血缘；取消保留血缘 | 隔离 SQLite；真实 Worker NOT RUN |
| L8 | 端到端 | 命令 → 派生候选 → 检查 → 采用 → 下一页连续性输入，全链 | 浏览器 E2E + 真实 Worker（NOT RUN 边界） |

## 12. 未验证边界（NOT RUN / 待批准）

1. 本契约**未获组长最终批准**，不构成实现授权；V02-40/V02-42 拆分前需逐节确认。
2. 真实 PostgreSQL 下的乐观并发与唯一键竞争（E8/L1）未运行；隔离 SQLite 结果不替代。
3. 真实 Worker 的晚返回/崩溃恢复/租约失效（E10/L7/L8）未运行。
4. 自然语言模型输出的实际合规率、`payload` 16KB 与字段限长的可用性未经真实模型验证（本轮未调用任何供应商）。
5. mask 栅格化精度与"区域外不变"的验收标准（图像相似度阈值）未定义，需 V02-40 阶段补充实验。
6. §10 失效分级收紧为候选方案，默认实现必须保持现状保守行为。
7. 浏览器 E2E 与性能门禁未运行（`architecture.md:131` 门禁要求本地 Web/API）。
