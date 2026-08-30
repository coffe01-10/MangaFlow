# V02-42A 局部重抽卡现状与管线审计

- 基线提交：`2613d978f64a2faee0282c6250501a453c13df0f`（origin/master）
- 工作分支：`codex/v02-42a-local-redraw-pipeline-audit`
- 任务性质：审计/设计文档，不实现代码、迁移或测试
- 依赖与待决问题（**非已批准契约**）：`docs/v02-director-command-lineage-contract.md`（Issue #47，命令 envelope 与血缘表草案）、`docs/v02-usage-ledger-contract.md`（Issue #49，账本计量列草案）、`docs/v02-provider-neutrality-audit.md`（Issue #39）均未经组长审阅批准，本文仅在"依赖与待决"章节引用，不作为实现依据
- 所有行号以基线提交为准

## 1. 现状调用链（带证据）

### 1.1 任务类型与图片生成链

| job_type | 创建入口 | Worker handler | 产物 |
| --- | --- | --- | --- |
| `PAGE_GENERATE` | `workflow/generation.py:69-98`（`create_candidate`，图片禁 `auto` :79-83）→ `create_page_candidate` + `enqueue_job` | `worker_handlers/page_generate.py:257-444` | `PageCandidate` + `Asset(kind="page_candidate")` + `GenerationRecord` |
| `PAGE_REPAIR` | `workflow/inspection.py:85-176`（检查面板"修复"按钮 → `apps/web/components/project-workspace/inspection-panel.tsx:34`、`apps/web/lib/api.ts:1040-1050`，repair_type `BUBBLE_REGION/PANEL/PAGE`） | 同上 `page_generate.py:317-343` 分支 | 新 batch（`generation_kind="REPAIR"`，`inspection.py:111`）+ 新候选 + 整页图 |
| `PAGE_UPSCALE` | `workflow/inspection.py:179-244`（候选卡"升至 2K/4K"，`generate-section.tsx:142`） | `page_generate.py:344-349` 分支 | 新 batch（`UPSCALE`）+ 新候选 + 高清整页图 |
| `ASSET_GENERATE` | 资产面板（人物/服装/风格，`asset_generation.py` 各路由） | `worker_handlers/asset_generate.py:105-347` | `AssetCandidate` + `Asset(kind=batch.generation_kind.lower())` |

页面生成核心顺序（`page_generate.py`）：候选取回与分镜版本前置检查 :258-265 → 提示词编译与参考绑定 :273-299 → 状态推进 `GENERATING/UPLOADING_REFERENCES` :300-304 → 参考图租约与重读核对 :306-386（`_lease_reference_assets` 于 `worker_handlers/provider.py:296-315`）→ 付费调用 :389-401 → 调用后取消检查与分镜版本重读 :402-405 → 资产落盘（sha256 去重 :193-201、失败清理 :250-253）→ `GenerationRecord` 落账 :407-437 → 候选置 `READY`、`page.version += 1` :440-444。

### 1.2 记账链（Job/Record/Attempt）

- 每次**真实派发**一行 `ModelCallAttempt`：付费调用前独立事务 `begin`（`worker_handlers/model_call_audit.py:38-74`，唯一键 `(job_id, job_attempt, dispatch_no)` `models.py:451-457`），调用后独立事务 `finalize`（:77-114）；失败/换 Key 的替代派发同样逐行记录（`worker_handlers/provider.py:186-235` 重试与 :248-276 `route_switched`）。
- `GenerationRecord` 仅在成功产物落库时创建（`page_generate.py:407-437`、`asset_generate.py:312-337`），携带 `provider/model_id/catalog_model_id/prompt_checksum/input_versions/usage/output_asset_ids`（`models.py:420-437`）。
- 重试预算：`GenerationJob.attempt_count/max_attempts`（`models.py:362-363`，默认 3/3）、`job_service.py:116` 剩余重试计算、:294 耗尽判定。
- 取消：`cancel_job`（`job_service.py:467-485`）置 `CANCELLED` 并同步候选/节点状态（:428-462）；执行侧检查点 `_ensure_job_not_cancelled`（`page_generate.py:402` 调用点）。
- 手动重试：`reset_for_retry`（`job_service.py:485+`）。

### 1.3 采用、回滚与版本状态

- 暂选：`POST /pages/{page_id}/select-candidate`（`workflow/generation.py:210-319`）——人工文字确认门槛 :237-244、过期候选显式接受门槛 :245-255、严重检查结果阻断 :256-276；同页旧暂选全部置 `is_selected=False` :286-288 后置新候选 :289。
- **换选即回滚语义**：`changed = page.selected_candidate_id != candidate.id` :291 → 同章后续已采用页 `NEEDS_RECHECK` :307-316（数据模型约定同 `docs/data-model.md:110`"换选已采用版本时，后续页面只标记 NEEDS_REVIEW，不删除历史候选"）。
- 沿用旧版：`POST /pages/{page_id}/selected-candidate/keep`（`generation.py:322-355`；前端横幅 `generate-section.tsx:103` STALE/LEGACY_UNKNOWN 二选一）；取消暂选 `DELETE /pages/{page_id}/selected-candidate`（:355-366）。
- 历史候选永不删除物理图：候选软删除 `deleted_at`（`generation.py:205-206`；前端删除按钮 `generate-section.tsx:142` 仅软删），资产无删除路径挂接到候选删除。

## 2. 缺口图："点击图片" → 局部重抽卡

前端候选图渲染于 `generate-section.tsx:142` 的 `CandidateArtwork ... onOpen={(url, label) => openPreview(url, label)}`——**点击图片唯一行为是打开预览灯箱**。逐环节缺口：

| 环节 | 现状 | 缺口 |
| --- | --- | --- |
| 点击图片 → 局部选择 | 仅 `openPreview` 全图预览 | 无画布/选区/mask 笔刷 UI（全库无 region-selection 组件） |
| 创建编辑请求 | 仅检查面板 `repairCandidate`（`inspection-panel.tsx:34`，`api.ts:1040-1050`）以 `repair_type` 粗粒度触发 | 无法从图上框选区域；`target_regions` 来自检查结果的 `regions`（`inspection.py:126`），非用户绘制 |
| 生成候选 | `PAGE_REPAIR` 走整页输出（`page_generate.py:338-343`"修复后仍输出完整页面"） | 无区域级输出/合成管线；无 mask 数据结构（全库无 mask 列/文件） |
| 比较 | 候选网格并排 + 收藏（`generate-section.tsx:142`） | 无父子候选对照视图、无区域 diff |
| 采用 | `select-candidate` 全页采用（`generation.py:210-319`） | 无"仅采用局部结果"概念——区域结果必须是完整新页候选 |

结论：**"局部重抽卡"当前不存在**；最接近的是文本约束的整页修复（§4 第三类）。

## 3. 四种操作口径区分与影响矩阵

| 操作 | 现状 | 页面/格子数据 | 候选与历史 | 存储对象 |
| --- | --- | --- | --- | --- |
| 整图重生（重新抽卡） | **有**：新 batch 新候选（`generation.py:69-98`；STALE 横幅"按当前 V 重新生成" `generate-section.tsx:103`） | 只读分镜；`based_on_storyboard_version`（`models.py:731`）锚定 | 历史候选保留、软删可选；采用需人工门槛 | 每候选独立 `Asset`（sha 去重共享，`page_generate.py:193-201`） |
| 单格重生 | **无** | Panel 无独立图像产物（图是整页一张） | 无 | 无 |
| 局部区域编辑 | **近似**：`PAGE_REPAIR` 带 `RepairPlan.target_regions`（`models.py:609`）但输出整页、约束纯提示词（`page_generate.py:338-343`） | 不改分镜；修复期间分镜变化 → 保留结果但过期（`page_generate.py:402-405`） | 新 batch（`inspection.py:111`）+ `original_candidate_id` 仅存于 `request_parameters`（:159）——无一等血缘列 | 新整页 Asset；无 mask 文件 |
| 仅提示词修改 | **无独立操作**：提示词由模板+分镜数据编译（`prompt_compiler.compile_page_prompt`，`page_generate.py:274`），人工改分镜字段间接改变提示词；候选 `prompt_snapshot.checksum`（:355）记录指纹 | 间接（分镜编辑路径 `storyboard.py:87-173`） | snapshot 逐候选快照 | 无 |

范围升级约束：修复类型只能保持或扩大（`inspection.py:100-105`），自动修复上限 `RepairPlan.automatic_attempts/max_automatic_attempts`（:129-130）+ 全局 `max_auto_repairs`（`config.py:43`）。

## 4. 不可变原图、派生版本、采用状态与可追溯关系（需求）

1. **不可变原图**：任何局部编辑不得修改父候选的 `asset_id/prompt_snapshot/status`（现状已满足——修复/升清一律新建候选 `inspection.py:112-121,197-208`；资产文件按候选 id 落盘 `page_generate.py:202-210` 且 sha 去重只共享相同内容）。
2. **派生版本**：需要一等血缘（父指针 + 派生类型 + mask 引用），替代 `request_parameters["original_candidate_id"]` 的隐式字符串（`inspection.py:159`、读取 `page_generate.py:318`）；依赖与待决：Issue #47 §7 的 `CandidateLineage` 草案（未批准，仅作输入）。
3. **采用状态**：采用是页级、排他的（`generation.py:286-289`）；派生候选继承"同页候选"身份参与同一采用门槛，不得绕过人工文字确认与严重检查阻断（:237-276）。
4. **可追溯**：`GenerationRecord.input_versions`（storyboard/page/revision，`page_generate.py:426-430`）与候选 `based_on_storyboard_version`（`models.py:731`）已锚定"生成依据"；缺父子链与 mask 出处。

## 5. 风险清单（并发/重复提交/刷新/取消/超时/重试）

| 风险 | 现状机制 | 残余缺口 |
| --- | --- | --- |
| 并发编辑（分镜 vs 生成） | 调用后重读版本，保留产物并标记过期（`page_generate.py:402-405`）；`STALE_CANDIDATE_CONFIRMATION_REQUIRED`（`generation.py:245-255`） | 局部编辑引入后"区域内容 + 分镜结构"双版本锚点未定义（结构变则区域作废的粒度待契约） |
| 重复提交 | `idempotency_key` 唯一（`models.py:370`；`repair:{repair_plan_id}` `inspection.py:166`）；候选序号冲突 409（`generation.py:90`） | 前端无防抖幂等提示层（双击修复会创建两个 RepairPlan——repair id 不同则不拦截） |
| 刷新恢复 | 候选状态即真源（QUEUED/GENERATING/READY），前端轮询 workbench（`use-generation-workspace.ts:23` 注释列举全部动作） | mask 选区是纯前端瞬时状态，刷新即丢（未来功能需草稿持久化） |
| 取消 | `cancel_job`（`job_service.py:467-485`）+ 调用前后检查点 | 付费调用进行中的取消不退款、产物保留无归属（现状语义，局部编辑需继承并写明） |
| 超时 | `job_timeout_seconds`/租约（`models.py:374-377`，`config.py:41-42`） | 未决 attempt（outcome NULL）无补偿扫描（`model_call_audit.py` docstring 承认存在） |
| 失败重试 | `max_attempts` + `reset_for_retry`；Key 失败轮换（`provider.py:186-235`） | 重试整页重画，无"复用已成功区域"的增量语义 |

## 6. 红线：历史 provider/model/catalog_model_id 不改写

- `GenerationRecord`/`ModelCallAttempt` 为追加式账本，外键 `SET NULL` 不改写（`models.py:422-424,491-499`）；无任何 UPDATE 历史 ID 的代码路径（全库仅 `model_costs.py` 只读消费）。
- 候选层 `catalog_model_id` 允许在派发时**从 alias 解析回填**（`page_generate.py:367-368`），这是首次写入而非改写既有事实；血缘设计必须同样只允许创建时写入。
- `legacy_alias` 单值唯一解析通道（`models.py:964-966`、`model_router.py:46-50`）与 V02-10C 迁移红线（`docs/v02-provider-neutrality-audit.md` §5 Phase C，未批准稿）保持一致：任何局部编辑功能不得引入第二份别名映射。

## 7. 能力不足必须显式暴露（禁止静默退化）

- 现状：`PAGE_REPAIR`/`ASSET_GENERATE` 对一切支持 `image_edit` 操作的模型发同样的"首图为原图"请求（`page_generate.py:317-324`、`asset_generate.py:276-291`）；模型能力仅有 `resolutions/max_reference_images`（`provider_catalog.py:703-706`）与操作白名单（`provider_catalog.py:417-423`），**没有 mask/inpaint 能力位**。
- 后果：不支持原生 mask 的模型会静默按整图编辑处理——用户以为只改了区域，实际全图重绘，违反"不得静默退化"。
- 需求：能力矩阵（依赖 Issue #52 的 V02-44A 交付）必须把 `native_mask_edit` 声明为显式能力位；不支持时 API 返回确定性错误（复用 `UNSUPPORTED_CAPABILITY` 家族，`vertex.py:136-140` 先例），前端把"局部重抽卡"入口置灰并说明原因，而不是退化为整页重生。半修半整的降级方案只能作为用户显式选择的选项。

## 8. 测试矩阵（可拆契约/后端/前端/验收 Issue）

| 组 | # | 层 | 场景 | 环境 |
| --- | --- | --- | --- | --- |
| 契约 | C1 | 文档→实现前 | 局部重抽卡操作语义（四类口径 §3）获组长批准 | 评审 |
| 后端 | B1 | 单元 | 修复/升清/重生三条链血缘正确：新 batch、父指针、原候选零改动（对照 `inspection.py:111-121` 现状回归） | 隔离 SQLite |
| 后端 | B2 | 单元 | 无 mask 能力位的模型收到区域编辑请求 → `UNSUPPORTED_CAPABILITY`，无付费调用 | 隔离 SQLite + mock 适配器 |
| 后端 | B3 | 单元 | 重复提交：同 RepairPlan 幂等键拦截；不同 RepairPlan 双击的防重（待契约定夺） | 隔离 SQLite |
| 后端 | B4 | 服务 | 取消/超时/未决 attempt 的候选终态与产物归属 | 隔离 SQLite |
| 后端 | B5 | 并发 | 修复期间分镜变化：产物保留 + 过期 + keep/重生二选一（回归 `generation.py:245-255`） | 隔离 SQLite；真实 PG NOT RUN |
| 前端 | F1 | 组件 | 候选图点击仅预览（现状回归）；局部选择入口在无 mask 能力时禁用并提示 | Vitest |
| 前端 | F2 | 组件 | 换选后后续页 NEEDS_RECHECK 横幅与 keep 流程（回归 `generate-section.tsx:103,142`） | Vitest |
| 验收 | A1 | E2E | 生成→检查→修复→新候选→比较→采用→下一页连续性全链 | 浏览器 E2E + 真实 Worker NOT RUN |
| 验收 | A2 | 验收 | mask 编辑在支持该能力的真实供应商上：区域外不变性人工评审 | 真实供应商 NOT RUN |

## 9. NOT RUN 与 UNKNOWN 边界

1. **未调用任何真实供应商**：mask/inpaint 能力、区域外不变性、修复质量均为 NOT RUN（A2）。
2. 真实 PostgreSQL/Redis/生产 Worker、浏览器 E2E 未运行（沿用项目既有边界；`architecture.md:131` 门禁要求本地服务）。
3. UNKNOWN：供应商是否存在可用的原生 mask/inpaint 端点及其计费单位——由 V02-44A 能力矩阵调研，本文不预设。
4. 本审计不修改任何前两批文档；其结论被引用处均标注"未批准草案"。
