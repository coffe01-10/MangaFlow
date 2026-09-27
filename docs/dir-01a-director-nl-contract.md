# DIR-01A 自然语言指令 → 导演命令契约（冻结）

- 基线提交：`758b342e`（origin/master）
- 任务性质：**L3 设计冻结**——冻结后再实现；实现归 DIR-01B/DIR-01C
- 前置契约：`docs/v02-director-command-lineage-contract.md`（命令 envelope、journal、
  propose/preview/accept/reject/undo/redo 状态机已全部实现于
  `apps/api/app/{domain,services}/director_commands.py` 与 `api/routes/director.py`）；
  `docs/v02-provider-neutrality-audit.md` §4（统一模型调用账本）
- 定位：定义"一段中文指令"如何变成既有命令层可预览、可逐条确认的
  `DirectorCommandEnvelope[]`。本契约只新增**一条受控解析通道**，
  不新增第二条写业务表的路径，不放宽既有命令层的任何校验。

## 1. 总原则

1. 模型只产出 envelope 候选 JSON；它永远没有数据库连接、文件路径、shell。
   模型输出原文不落业务表，只以 `raw_output_id`（ModelCallAttempt id）与
   `source.user_prompt`（截断）形式进入审计。
2. 解析产物必须经**两道确定性校验**才进入 journal：
   schema 校验（`domain/director_commands.py` 的 envelope 模型，未知字段拒绝）
   → 业务校验（目标存在、项目归属、operation 白名单、payload 白名单，
   即现有 `propose_command_group` 服务路径原样复用）。
3. 解析本身是**付费文本调用**，必须先落库为 `GenerationJob` 再异步执行；
   同步 HTTP 路径不做任何模型调用。
4. 解析阶段与预览阶段**不创建任何图片生成任务**。`regenerate_region`
   不出现在 NL 解析白名单（见 §5），局部重绘在首版继续走既有检查/修复流程。
5. 歧义不猜：无法唯一锚定目标时返回澄清项而不是默认目标。

## 2. API 面

```text
POST /api/v1/projects/{project_id}/director/utterances
  请求：{
    utterance: str,           // 必填，1..2000 字符，超出 422
    page_id: uuid,            // 必填；首版 NL 指令只作用于单页
    storyboard_version: int,  // 必填；客户端发起时刻的分镜版本锚点
    selection?: { kind: "panel"|"dialogue"|"character",
                  panel_id?, dialogue_id?, character_id? },  // 可选预锚定
    client_request_id: uuid,  // 必填幂等键
    retry_of_group_id?: uuid  // 重试上一解析会话时携带
  }
  响应：202 { job_id, command_group_id }
```

- 同事务写入：`DirectorCommandGroup(status="PARSING", page_id)` +
  `GenerationJob(job_type="DIRECTOR_PARSE", target_type="director_group",
  target_id=group.id, idempotency_key="director-parse:{client_request_id}")`；
  事务提交后 enqueue（沿用 architecture.md:140 事务所有权约定）。
- 重复提交（同 `client_request_id`）经幂等键冲突返回既有 `{job_id,
  command_group_id}`，HTTP 200 + `idempotent_replay: true`（与命令组
  重复提交语义一致）。
- 同页并发：若该 `page_id` 存在非终态 `DIRECTOR_PARSE` 任务 → 409
  `PARSE_IN_FLIGHT`（解析读的是版本快照，串行化即可预测）。
- 结果读取：复用 `GET /director/command-groups/{id}`；job 状态走既有
  jobs API 轮询，不新增第二种进度通道。
- `GET /projects/{id}/director/command-groups` 已支持 `page_id` 过滤，
  解析会话组按 `page_id` 与手工命令组同列展示，以 `first_result.kind`
  区分来源。

## 3. 版本锚定

- 服务端在校验每条命令时**自行填充 `expected_version`**（panel→`Panel.version`，
  page→`MangaPage.version`，storyboard→`MangaPage.storyboard_version`，
  scene→`Scene.version`）；模型输出中的任何版本字段直接丢弃。
- `storyboard_version` 请求锚点只用于**过期检测**：Worker 起手重读页面，
  `page.storyboard_version != request.storyboard_version` → 任务正常完成，
  组状态置 `STALE`，`first_result={kind:"stale", current_version}`，
  不产生命令行；用户刷新后用新锚点重发（`retry_of_group_id` 可指回旧组）。
- 预览→接受之间分镜再变的安全网仍是命令层既有 `expected_version` 409。

## 4. 上下文装配（模型输入）

Worker 组装的上下文是**序列化后的最小投影**（JSON，≤32KB）：

- 目标页：`page_id, page_number, panel_count, storyboard_version, version`
- 每格：`panel_id, reading_order, shot_type, camera_angle, camera_height,
  bounds, version` + 气泡 `dialogue_id, reading_order, speaker(姓名),
  target_text(截断200字), version`
- 引用场景：`scene_id, location, time_label, weather, version`（仅
  `page.scene_ids` 内）
- 项目角色目录：`character_id, 姓名, 别名`（供说话人/角色名归一化）
- 可选 `selection` 预锚定与 `retry_of_group_id` 上次失败的命令摘要
- **禁止项**：任何文件路径、storage_key、图片字节、其他页数据、凭据。

系统提示词为固定模板（版本化字符串，入 `prompt_snapshot` 等价审计段），
声明：只输出 §3 envelope-lite JSON、禁止补全不存在的 id、目标歧义输出
clarification、无法映射输出 unsupported。

## 5. 模型输出 schema 与 NL 白名单

模型输出（外层，非 envelope）：

```json
{
  "commands": [ {"op_ref": "c1", "operation": "...", "target_hint": {...},
                 "payload": {...}} ],
  "clarifications": [ {"kind": "target|value", "question": "...", 
                       "options": [...] } ],
  "unsupported": [ {"reason": "...", "excerpt": "..."} ]
}
```

- 模型只给 `target_hint`（如 `{"panel_reading_order": 2}` 或 `{"speaker": "阿岚"}`）；
  **真实 id 由服务端归一化填充**——hint → 唯一实体则锚定，多候选进
  `clarifications`，零候选进该条命令的校验失败。
- NL v1 operation 白名单：`update_page_layout, update_panel_layout,
  update_panel_shot, update_panel_cast, update_scene_context,
  update_dialogue, move_dialogue`。
  `regenerate_region` **不在白名单**——模型请求局部重绘时归入
  `unsupported` 并附"请走修复流程"指引（§1.4）。
- 每次解析产出命令上限 **8 条**；超出截断并在 `first_result` 标注
  `truncated: true`。单条 payload 经 envelope 校验（≤16KB）。

## 6. 结果落库与组状态

`DirectorCommandGroup.status` 生命周期（新增值均为字符串，无迁移）：

```text
PARSING ─┬─ 全部就绪 ─> PREVIEWED        （命令行已写，逐条走既有接受流）
         ├─ 需澄清 ──> NEEDS_CLARIFICATION（无命令行）
         ├─ 版本过期 ─> STALE             （无命令行）
         └─ 解析失败 ─> PARSE_FAILED      （模型超时/输出非法/绑定失败）
```

- `first_result` 统一形态：
  `{kind: "ready"|"clarify"|"stale"|"error", reason?, clarify_options?,
    truncated?, model: {provider, catalog_model_id, model_id},
    model_call_attempt_id, duration_ms}`。
- `ready`：每条编译成功的 envelope 经 `propose_command_group` 同路径落为
  `DirectorCommand`（`source.user_prompt`=原指令截断、
  `source.model`=绑定三元组、`source.raw_output_id`=attempt id）。
  校验失败的条目同样落行（status 校验失败 + `error`），与其他条目互不影响。
- `clarify`：`clarify_options[]={kind:"page|panel|dialogue|character|value",
  id?, label, question}`，由前端渲染成可点选项；选择后用户重发指令
  （新 utterances 请求，可在 `selection` 携带选定项）。
- `PARSE_FAILED` 的组可 `discard`；任务行保留 `error_message` 供排障。

## 7. 模型绑定、账本与超时

- 绑定：`provider._text_model_reference(job, project)`
  （`project.default_text_model_id or project.text_model_alias or "auto"`）
  + `operation="structured_text"` 能力位；能力缺失 fail-closed，
  组状态 `PARSE_FAILED`（reason 注明未配置文字模型）。不自动换模型。
- 账本：一次解析 = 至多一次模型调用 = 一条 `ModelCallAttempt`
  （`task_kind="DIRECTOR_PARSE"`），沿用既有 attempt 生命周期与用量归集。
- 超时：适配器调用沿用项目模型超时配置；Worker 侧兜底
  `DIRECTOR_PARSE` 单任务上限 **120s**，超时按不可重试失败收尾
  （重试由用户显式重发，避免静默重复计费）。
- 重试语义：模型调用失败不产生自动重试；`retry_of_group_id` 仅作审计
  关联，新会话必须携带新 `client_request_id`。

## 8. 安全边界（沿用 §9 既有命令层，新增项加粗）

- envelope schema 严格校验：未知字段拒绝、类型白名单、长度上限。
- `target` 全部 id 必须属于 `project_id`；`scene_id ∈ page.scene_ids`；
  `panel_id/dialogue_id` 必须属于 `page_id`。
- payload/模型输出中禁止文件路径与 storage_key。
- **`page_id` 边界**：NL 解析不接受跨页目标 hint；hint 指向非本页实体
  视为零候选 → 该条失败。
- **`first_result.clarify_options` 的 id 同样经项目归属校验后才下发**，
  防止模型幻觉 id 原样反射给客户端。
- **utterance/上下文不进入 shell/SQL 模板**；原指令仅审计展示。

## 9. 验收矩阵（DIR-01B 测试对照）

| # | 场景 | 期望 |
| --- | --- | --- |
| A1 | "把第二格改成特写"（唯一锚定） | 组 PREVIEWED，1 条 `update_panel_shot`，expected_version=该格当前 version，diff 正确 |
| A2 | "把台词改成：……"无 selection 且页内多气泡 | `NEEDS_CLARIFICATION`，options 列全部候选气泡，零命令行 |
| A3 | 模型输出含幻觉 panel_id / 未知字段 / 9 条命令 | 幻觉 id 与未知字段条目校验失败落行；超 8 条截断；组仍 PREVIEWED |
| A4 | 请求 `storyboard_version` 过期 | 组 `STALE`，零命令行，job COMPLETED |
| A5 | 同 `client_request_id` 重复 POST | 200 回放同组，无第二个 job/组 |
| A6 | 同页并发解析 | 第二个 409 `PARSE_IN_FLIGHT` |
| A7 | 模型调用超时/非法 JSON/能力缺失 | 组 `PARSE_FAILED` + first_result.error；无付费 attempt 之外的写副作用 |
| A8 | 模型请求"重画这一格" | `unsupported` 条目 + 修复指引，零派生任务 |
| A9 | 解析产出的命令经既有 accept 路径执行 | 与手工命令行为完全一致（版本校验、级联失效、journal） |

## 10. 明确不做（首版边界）

- 跨页/跨章节指令、整章批量编辑
- 模型生成 mask 或 `regenerate_region` 派生任务
- 语音输入、多轮对话记忆（每会话独立，靠 `retry_of_group_id` 关联）
- 前端 `director-rules.ts` 规则桩保留为离线降级路径，标签继续标注
  "规则解析，非模型"；模型解析组在 UI 标注真实模型三元组
