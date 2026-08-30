# V02-11 供应商设置工作流：只读审计与 UI 设计

- 任务：Issue #40 / `[0.2.0][V02-11]` Audit and redesign the provider settings workflow
- 基线：`1eb2ae1c1a6d1332e601d01e6cb5d98edb0037ef`（分支 `grok`）
- 性质：L2 UI 只读审计与信息架构设计，不实现功能，不改服务端 schema
- 入口：`/settings` 主栏中的 `ProviderManagement`；`apps/web/app/providers.tsx` 不是供应商页面，而是根级 React Query 包装器
- 契约复审输入：已合并的 V02-02 审计 `5ac0383:docs/v02-provider-neutrality-audit.md`；本文件的现状行号仍以 `1eb2ae1` 为准，目标设计按该已批准契约收口

本文只约束设置页供应商平台的信息架构、操作流、状态、文案、组件拆分与测试。不决定服务端字段，不覆盖全站视觉，不授权真实供应商调用。

---

## 1. 当前结构与摩擦点

### 1.1 页面与文件角色

| 表面 | 文件 | 实际职责 |
| --- | --- | --- |
| 系统设置页 | `apps/web/app/settings/page.tsx` | 唯一用户入口。顶部状态条、`ProviderManagement`、独立 Vertex 验证卡、运行设置、诊断与存储。 |
| 供应商平台组件 | `apps/web/components/provider-management.tsx` | 318 行单文件：列表、搜索、排序、创建、密钥、连接编辑、发现、手工模型、测试、余额。 |
| 对应测试 | `apps/web/components/provider-management.test.tsx` | 仅 3 个用例：健康错误可见、创建失败/刷新、凭据测试失败且不回显明文。 |
| Query 根 | `apps/web/app/providers.tsx:6-16` | `QueryClientProvider`（`staleTime: 15_000`, `retry: 1`），不是供应商路由。 |
| 前端类型与客户端 | `apps/web/lib/api.ts` | `ModelCapability`、`ProviderProfile`、`ProviderConnection`、`ProviderKeySummary`、`ProviderModel`、`ModelProbe` 及部分 API。 |
| 样式 | `apps/web/app/globals.css:832-898`、`:1150-1170`、`:1250-1255` | 工具栏、卡片、连接面板、模型行；窄窗口在 900px 叠设置栏、760px 叠供应商工具栏。 |
| 产品规则 | `docs/provider-platform.md` | 协议范围、密钥脱敏、同步模型、`INFERRED`/`VERIFIED`、四层测试、自动路由只走已验证模型。 |

设置页与平台组件都请求 `["providers"]`（`settings/page.tsx:47` 与 `provider-management.tsx:284`）。React Query 会去重，状态条的「N 健康」与平台列表共用缓存。平台组件另外请求 `["models"]`（`:285`）。

### 1.2 当前组件树（按渲染顺序）

```
SystemSettingsPage                         settings/page.tsx:43
├─ 状态条「AI 连接 / 执行器 / 数据库 / 存储 / 最近检查」  :71-77
├─ ProviderManagement                      provider-management.tsx:282
│  ├─ 工具栏：搜索 + 排序 + 协议说明         :310
│  ├─ details「添加自定义供应商」            :311-315
│  └─ 加载面板 或
│     ├─ ProviderGroup「已启用」            :261-280 / :316
│     │  └─ ProviderCard                   :233-259
│     │     └─ ConnectionPanel             :27-192
│     │        ├─ 密钥表单/列表             :151-157
│     │        ├─ 验证凭据 / 同步模型 / 余额 :159-163
│     │        ├─ details 高级连接          :164-171
│     │        ├─ 模型行                    :172-182
│     │        └─ 手工模型表单              :183-187
│     └─ ProviderGroup「已停用」
├─ VERTEX AI / PROVIDER 独立验证卡          settings/page.tsx:81-94
├─ WORKER / RUNTIME
└─ 侧栏：分层诊断、本地存储
```

`ProviderCard` 用自定义按钮 + `aria-expanded`（`:250`）。创建区与高级连接用原生 `<details>`（`:311`、`:164`），没有 `aria-controls`、没有把错误绑到摘要。折叠模式不统一。

### 1.3 前端已接 / 未接的 API 面

已接：`GET /providers`、`POST /providers`、`PATCH /providers/{id}`（仅 `enabled`）、`PATCH /connections/{id}`、`PUT .../keys`、`DELETE .../keys/{id}`、`POST .../discover`、`POST .../models`、`POST .../test`、`GET .../balance`。

类型已有、UI 未接：

- `api.updateProvider` 的 `name`（`api.ts:821`）——不能改显示名。
- `ProviderConnection.enabled`、`ConnectionUpdate.enabled`——不能停用单条连接。
- `ProviderModel` / `PATCH /providers/models/{id}`（后端 `providers.py:234-238`，载荷含 `enabled`、`display_name`、`operations`、`priority`、`version`）——前端没有 `updateProviderModel`。
- `DELETE /providers/{id}`（内置 409，自定义可删）——前端没有删除。
- `POST /providers/{id}/connections`——不能加备用连接。
- `api.providerProbes`（`api.ts:837`）——探测历史不展示。
- `GET /providers/presets`——创建自定义时不能从预设复制。
- `test_type: "BENCHMARK"`、`acknowledge_cost` 仅图片路径使用。

`/models` 返回的 `ModelCapability.enabled` **不是单独的展示偏好**，而是可用性派生值（`apps/api/app/api/routes/models.py:34-67`）：持久 `AIModel.enabled`、连接、供应商均启用，Key 型连接还要有可用凭据。`auto_eligible` 另要求 `confidence == "VERIFIED"` 且连接 `HEALTHY`。V02-02 已确认这套可用性公式保持不变；因此设置页不能把返回值直接当作「是否在创作界面展示」，也不能为了隐藏选项而停用实际可调用模型。展示偏好由 V02-12 与下游选模共同确定。

### 1.4 摩擦点（文件:行号）

**发现与导航**

1. 二十余个预设（含 Vertex、Gemini API、聚合网关）全部铺在同一列表。未配置项默认折叠（`:235-237`），但搜索只匹配 `name` 与 `preset_key`（`:301`），不匹配协议、分类、风险、模型 ID、描述。
2. 页头计数写成「N 个预设供应商」（`:309`），自定义供应商也被算进去。
3. `category` / `risk_label` 以英文枚举直出（`:252`）：`compatible`、`GATEWAY`、`THIRD_PARTY`、`OFFICIAL`。`documentation_url` 有数据无入口。
4. Vertex 出现两次：平台列表里的 `VERTEX_NATIVE` 连接（密钥区被跳过，`:151`）+ 下方独立 Vertex 验证卡（`settings/page.tsx:81-94`）。这是当前双入口，不是终态；目标见 §2.1 统一外壳 + capability renderer。
5. `apps/web/app/providers.tsx` 文件名像供应商页，实际是 Query 根。后续实现不要在这里加供应商 UI。

**供应商新增 / 编辑**

6. 创建区只有名称、协议、Base URL（`:311-313`）。`use_responses_api` 固定 `false`（`:292`），OpenAI 兼容连接创建后还要打开高级区才能改。
7. 没有编辑供应商名称、没有删除自定义供应商、没有新增备用连接。启用/停用只在卡片头（`:255`），与「模型显隐」不是同一层。
8. 高级连接把端点模板和请求头做成自由 JSON 文本（`:40-45`、`:168-170`）。`JSON.parse` 无防护（`:69-70`）；非法 JSON 会变成 `SyntaxError`，英文消息挂在面板底部，不标字段。
9. `ConnectionPanel` 的 React key 是 `` `${connection.id}:${connection.version}` ``（`:257`）。任意成功 mutation 都会 `invalidateQueries`（`:52-55`），version 一变就卸载面板，正在编辑的 Base URL / JSON / 密钥标签草稿丢失。
10. 所有连接级 mutation 共用一个 `pending`（`:136-137`），一次「测试文本」会禁用保存密钥、同步、余额、手工添加。

**连接测试 / 发现 / 手工模型**

11. 兼容连接并排「验证凭据」与「同步模型」（`:160-161`）。后端 `CREDENTIALS` 已经调用 `discover_models`（`providers.py:252-277`），再点「同步模型」会 **连续两次请求上游 `/models`**。文案还像纯鉴权，与 Vertex 卡「验证凭据」（只刷新 OAuth，`settings/page.tsx:87`）撞名。
12. 「同步模型」成功只写 `已同步 N 个模型…`（`:80`）。不区分新增/保留/推断；`INFERRED` 必须人工修正这一产品规则（`docs/provider-platform.md:23`）在 UI 上没有对应动作。目标方案把连接级发现并进单动作，不再保留第二颗发现按钮。
13. 模型行只显示 `display_name`、`model_type`、`model_id`、`confidence`、`operations`（`:174`）。不显示 `auto_eligible`、来源、上次验证、延迟、用户显隐。测试按钮在 `!model.enabled` 时禁用（`:175`）——这里的 `enabled` 是可用性，不是隐藏开关。
14. 文本测试走 `TEXT`，带 `multimodal_analysis` 才出现「测试视觉」（`:179`）。图片走 `window.confirm`（`:176-177`）。Anthropic 图片按钮仍渲染，文案为「协议不支持」且 disabled。没有探测历史，成功只覆盖一条 `notice`（`:98-100`）。
15. 手工模型把 ID 当显示名，并按类型写死模态、操作、API surface（`:111-125`）。创建后不能改能力，也不能标「待验证」。

**搜索、筛选、排序、显隐**

16. 无模型类型筛选、无能力筛选、无「仅显示已验证模型」。`confidence` 原文展示，且未覆盖 `MANUAL` / `DECLARED` / `INFERRED` / `PARTIAL` / `VERIFIED` 全表（见 §4.2）。
17. 排序只作用于供应商（`:219-230`），不作用于模型。「推荐顺序」是本地加权：已配置 ×4 + HEALTHY ×2 + 有模型 ×1，与路由评分无关，也未解释。
18. 无批量选择、无批量显隐。供应商停用是整卡开关；模型层没有开关。`ProviderKey.cooldown_until`、`last_error_code`、`enabled` 已在类型中（`api.ts:93-101`），列表只渲染 `label key_hint · health_state`（`:157`），与文档「标签、末四位、健康状态与冷却时间」（`provider-platform.md:19`）不符。

**折叠、焦点、错误关联**

19. 查询失败时 `isLoading` 为 false 且 `data` 为空（`:316`）。界面走进空分组「当前筛选条件下没有供应商」（`:278`），没有列表级错误。`models` 失败时连接会显示「尚无模型」（`:181`），空与错误不分。
20. 连接级错误是面板底部一条 `form-error`（`:190`），创建错误在 `<details>` 内（`:314`）。都没有 `id`、`role="alert"`、`aria-describedby`、`aria-invalid`。焦点不移到失败字段。
21. 搜索框、Base URL、手工模型 ID、创建表单输入缺少 `aria-label`（仅密钥标签、API Key、模型类型、排序有，`:153-154`、`:185`、`:310`）。
22. 分组/卡片用按钮折叠，创建/高级用 `<details>`。搜索非空时 `forceExpanded` 强制展开两组（`:273`、`:316`），没有「跳到结果」。目标：**输入/防抖不得抢焦点**；仅 Enter 或显式「跳到结果」才移焦。删除密钥无确认，按钮只有 `aria-label={`删除 ${key.label}`}`（`:157`）。

**密钥脱敏**

23. 密钥输入 `type="password"`、`autoComplete="new-password"`，成功后清空（`:59`、`:154`）。测试用例覆盖「失败后 DOM 不含输入明文」（`provider-management.test.tsx:115-137`）。
24. 服务端 hint 格式是 `••••` + 末四位（`credential_crypto.py:83-88`）。测试夹具写的是 `sk-****1234`（`provider-management.test.tsx:51`），断言的是夹具字符串，不是生产 hint。实现时不要把 `sk-` 前缀当真实回显格式。
25. `notice` 写「浏览器不会读取明文」（`:60`）——保存后 React 状态清空，但提交前明文仍在受控输入里。文案过满。

**窄桌面窗口**

26. `.settings-board` 在 900px 才单列（`globals.css:1159`）。900–1280px 仍是「供应商平台 + 粘滞侧栏」，主栏大约 560–760px，工具栏仍是三列（`minmax(210px,1fr) auto minmax(170px,.85fr)`，`:833`）。搜索、排序、说明挤在一行，说明 8px。
27. 供应商工具栏、创建表单、密钥表单到 760px 才单列（`:1166-1169`）。1024×768 一类窄桌面会同时出现：三列工具栏、四列创建表单、三列密钥表单、模型行「标题 + 按钮」挤占。
28. 900px 以下 `.provider-card-counts` 直接 `display: none`（`:1170`），折叠卡片上看不到连接数/模型数。
29. `.provider-list` 固定 `max-height: 920px; overflow: auto`（`:844`），设置页本身再滚动，形成套娃滚动。模型多的连接没有独立虚拟化或分页。
30. 字号大量 7–9px（卡片分类 7px、连接 URL 7px、密钥芯片 7px、模型副文 7px）。这是可读性问题，不是视觉风格任务；实现时应把设置页控件抬到 ≥12px 正文 / ≥11px 辅助，不改全站。

**测试缺口**

31. 现有测试不覆盖：搜索/排序/焦点、类型与能力筛选、已验证过滤与 confidence 映射、展开键盘、错误关联、JSON 形状与禁头、手工模型 `MANUAL`、单动作探测（禁止双请求）、图片确认、批量显隐、供应商重命名/删除、查询失败、窄布局、Vertex 统一外壳、可用性 `enabled` 与用户显隐混淆。

### 1.5 范围外记录（不改）

- 运行设置、诊断、存储的布局与文案。现有 Vertex 四按钮卡仅作迁移期兼容，终态见 §2.1 / §7.1，不作为永久双入口保留。
- 首页 `ai_overview`、项目设置默认文字模型、生成台选模（V02-12）。
- `/models.enabled` 派生语义与 `PATCH /providers/models/{id}` 的持久 `enabled` 不一致（V02-02）。
- 凭据主密钥文件、AES-GCM、真实供应商探测（禁止本任务触碰）。
- `plan.md` / `docs/roadmap.md` / `docs/development-progress.md` 未列 V02 编号（禁止本任务修改）。

---

## 2. 目标信息架构与完整操作流

### 2.1 目标分层

设置页只保留 **一个**「AI 供应商与模型」主卡，内部分四层。不新开 `/providers` 路由，不把 `app/providers.tsx` 改成页面。

```
L0  平台工具栏     搜索 · 类型 · 能力 · 已验证 · 排序 · 添加
L1  供应商          身份、风险、启用、连接摘要（统一外壳）
L2  连接            凭据区（按 credential_source 渲染）· 健康 · 连接探测 · 高级端点
L3  可展示模型      类型/能力/置信度/显隐/测试
```

**最终目标是统一供应商外壳**，不是 Vertex 专用卡 + 兼容列表两个入口。`ProviderCard` / `ConnectionPanel` 对所有协议复用同一骨架；差异只由 V02-02 确定的 `credential_source` 与能力声明驱动：

| 契约 | `CONNECTION_KEY` | `ENV_SERVICE_ACCOUNT` | 共同规则 |
| --- | --- | --- | --- |
| 凭据区 | 标签 + 口令框 + 末四位、健康与冷却芯片 | 「凭据由服务端环境管理」，只展示 readiness，不暴露路径 | 不按供应商名或协议字符串分支 |
| 连接启用 | 首个有效 Key 由显式保存动作启用 | 新连接遵守 `auto_enable_pending` 一次性状态机 | 人工启用/停用清除 pending；凭据暂时缺失不自动停用 |
| 模型发现 | 仅在连接能力声明支持时显示 | 同左；账号型并不天然等于“不支持发现” | 不支持发现时显示手工建模/预设种子，不渲染无效按钮 |
| 连接验证 | 统一 `CREDENTIALS` | 统一 `CREDENTIALS` | 同一健康端点与状态枚举；不触发文本/图片生成 |
| 模型验证 | 按目录模型 `operations` 渲染 `MODEL_SMOKE` | 同左 | 图片冒烟必须确认费用；无硬编码模型名或四按钮 |

基线后端中 Key 型 `CREDENTIALS` 已调用 `discover_models`（`providers.py:252-277`）。因此过渡实现只能保留一颗「测试连接并同步目录」组合动作，禁止随后再次调用 discover。V02-02 Phase B 落地统一验证契约后，UI 根据服务端能力显示「测试连接」，并仅在 `supports_model_discovery=true` 时显示「同步模型」；两动作若被服务端合并则只渲染组合按钮，若拆分则各调用一次对应端点，任何模式都不得重复请求模型列表。

例如 Gemini API 当前使用 Key，Vertex 当前使用环境服务账号，但 UI 只读取 `credential_source` 与能力字段，不出现 `if protocol === "VERTEX_NATIVE"` 一类产品分支。

**现有 `VERTEX AI / PROVIDER` 四按钮卡不是终态。** 它只是有退出条件的迁移期兼容（见 §7.1）。最终设置页主栏只留统一平台卡；账号型连接和 Key 型连接都进入同一个 `ProviderCard`，模型测试由目录能力生成。

### 2.2 对象与用户能做的事

**供应商（生命周期，首发必须交付，不许写「若做」）**

- 浏览预设与自定义；按名称/协议/模型 ID 搜索。
- 启用或停用整家供应商（已有 `PATCH /providers/{id}`，带 `version`）。
- 添加自定义：名称、协议、Base URL；OpenAI 协议可勾选 Responses API（创建时提交，不再写死 `false`）。400 字段错误留在表单内。
- 重命名：卡片内联或编辑面板调用已有 `api.updateProvider(id, { version, name })`。成功更新标题；409 乐观并发展示「供应商已在别处更新，请重新加载」。内置与自定义都走同一 PATCH；不另开 schema。
- 删除：仅 `built_in === false` 显示删除。前端必须补 `api.deleteProvider(id)` wrapper（今日缺失）。确认后 `DELETE /providers/{id}`。内置不渲染删除；若误调，展示 409「内置供应商只能停用，不能删除」。任务占用同样 409，原文展示。
- 不在本页发明第二套分类体系；`OFFICIAL` / `GATEWAY` / `THIRD_PARTY` 映射为中文标签，文档链接用 `documentation_url` 做「说明」外链。
- **现有连接启用/停用**纳入首发：使用已有 `ConnectionUpdate.enabled`；对 ENV_SERVICE_ACCOUNT 的人工切换必须按 V02-02 同时清除 `auto_enable_pending`，不能在前端凭 readiness 猜测或回填 `enabled`。
- **新增备用连接**需要单独的产品切片：后端已有 `POST /providers/{id}/connections`，并非契约阻塞；但本 Issue 不扩大到连接优先级、删除与故障切换，标记为 `DEFERRED`，不得与“编辑/停用现有连接”混为一项。

**连接**

- 查看健康、延迟、密钥数、模型数、最后成功。
- 保存密钥（可写时）、删除密钥（确认）、看冷却截止。
- `CONNECTION_KEY` 渲染密钥表单；`ENV_SERVICE_ACCOUNT` 渲染服务端环境 readiness；两者使用同一连接验证动作和健康状态。
- 模型发现按钮只由 `supports_model_discovery` 控制。基线组合契约下使用一颗「测试连接并同步目录」；统一端点拆分后使用「测试连接」+ 可选「同步模型」，且不得重复 discover。余额同样按能力声明显示。
- 模型级测试按 `operations`/capabilities 生成，使用目录模型 ID；不硬编码 Vertex、Nano Banana 或其他供应商名称。
- 展开高级：Base URL、Responses 开关（仅 OPENAI）、端点模板、额外请求头。失焦做语法 + 形状 + 禁头校验（§6.3），非法时保存禁用并在对应 textarea 下显示中文错误。

**模型（可展示目录）**

- 默认列出该连接下的模型。工具栏或连接内可筛：文字/图片、能力（结构化文本 / 视觉 / 生图 / 改图）、仅已验证。
- 单行：显示名、上游 ID、类型、能力芯片、置信度、显隐开关、测试。
- 手工添加：上游模型 ID、显示名（可与 ID 不同）、类型；能力用当前协议的安全默认值。添加后标为待验证，不进入自动路由（规则已在服务端，UI 只展示）。
- 批量：勾选当前可见行 →「隐藏所选 / 显示所选」。该动作写**创作界面展示偏好**，持久位置与批量 API 由 V02-12 的实现 Issue 确定；不得回退为逐条修改 `AIModel.enabled`，因为后者会改变调用与自动路由资格。
- 「隐藏」只表示不出现在生成台、工作流和项目默认模型选择器；设置页开启「显示已隐藏」后仍能管理和测试。它与 `AIModel.enabled`（可调用性）及 `ModelCapability.enabled`（派生可用性）是三种状态，测试必须分别构造。

### 2.3 完整操作流

**A. 配置一家预设供应商**

1. 进入 `/settings`，主卡加载供应商与模型。
2. 搜索名称、预设键或模型 ID。输入与防抖 **只过滤并展开匹配分组/卡片，不移动焦点**。用户按 Enter，或点击工具栏「跳到结果」，才把焦点移到第一张匹配卡标题。
3. 展开卡片 → 连接。Key 型输入标签与 API Key 后保存；账号型读取服务端 readiness，不显示密钥框或凭据路径。成功后只显示脱敏状态。
4. 点连接验证动作。基线组合模式显示「测试连接并同步目录」且只调用一次；统一拆分模式显示「测试连接」，并仅在能力支持时另有「同步模型」。进行中该连接操作区 busy，状态区使用 `role="status"`；任何路径都禁止重复 discover。
5. 对计划使用的文字模型「测试文本」（需要视觉再测视觉）；图片模型走明确确认层（不是仅 `window.confirm`，见 §3.3）。这些是 **模型级** 探测，不是连接级第二次 `/models`。
6. 需要时隐藏无关模型；打开「仅已验证」检查自动路由候选。

**B. 新增自定义供应商**

1. 「添加供应商」展开（键盘可进入，见 §6）。
2. 填名称、协议、Base URL；非法 URL 在字段下提示，不提交。
3. 创建成功：清空表单、关闭添加区、展开新卡片、焦点到密钥输入。
4. 之后同 A 的 3–6。高级端点仅在默认模板不够时打开。

**B2. 重命名 / 删除供应商**

1. 重命名：打开卡片编辑，改显示名，提交 `PATCH`（`name` + `version`）。成功后标题更新，version 随响应前进。
2. 409 冲突：不覆盖本地输入；提示重新加载；提供「放弃草稿并重新加载」。
3. 删除自定义：确认文案明确不可恢复；成功后卡片从列表消失。
4. 内置卡片无删除按钮，只提供停用。

**C. 编辑连接**

1. 打开「连接与端点」。
2. 改 Base URL / Responses / 模板 / 头。JSON 非法时「保存连接」禁用。
3. 保存成功给字段级成功状态；并发 version 冲突展示「连接已在别处更新，请重新加载」并刷新该卡，不丢尚未保存的本地草稿提示（冲突后以服务器为准，草稿可一键放弃）。

**D. 手工模型**

1. 发现结果没有所需 ID 时，用「添加模型」。
2. 提交后行出现在列表。服务端 `confidence` 为 `MANUAL` 或等价未验证值时，UI 显示「待验证」（映射见 §4.2）。测试按钮在连接已配置时可用。
3. 「仅已验证」开启时该行被过滤，直到服务端改为 `VERIFIED`。
4. 测试通过后置信度按服务端返回映射（通常为「已验证」）。

**E. 批量显隐**

1. 在当前筛选结果中勾选。
2. 出现批量条：已选 N · 隐藏 · 显示 · 取消选择。
3. 进行中禁用这些按钮；部分失败保留选择并列出失败行。
4. 「仅已验证」与「隐藏」正交：已隐藏的不出现在默认列表；打开「显示已隐藏」才能再显示。

**F. 停用供应商**

1. 卡片「停用」二次确认：停用后自动路由与显式选择都不可用（现有后端语义）。
2. 卡片移到「已停用」组，不删除密钥与模型。

### 2.4 非目标

- 不在设置页做路由权重编辑（`RoutingPolicy`）。
- 不在设置页做价格版本表单。
- 不把 Vertex 服务账号 JSON 做到浏览器。
- 不在本任务做全站控件视觉重构。

---

## 3. 桌面端文字线框与状态表

### 3.1 宽桌面（≥1280px）

设置页仍为「主栏 + 诊断侧栏」。主栏第一卡：

```
┌─ AI 供应商与模型                              22 家供应商 · 4 已配置 ─┐
│  ┌搜索供应商、协议或模型 ID──────┐ [文字][图片] [能力▾] [✓ 仅已验证] [排序▾] [跳到结果] [+ 添加] │
│                                                                          │
│  已配置 (4)                                              默认展开          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ v  官方 · 低风险            OpenAI                    1/1 连接  12 模型 │
│  │   已启用                          [重命名] [停用]                    │
│  │   ┌ default · OPENAI · https://api.openai.com/v1     健康 · 420ms ┐ │
│  │   │ 密钥  default · ••••9f3a · 健康                                 │ │
│  │   │ 标签 [default    ]  API Key [••••••••] [保存密钥]               │ │
│  │   │ [测试连接并同步目录] [查询余额]                                  │ │
│  │   │ ▸ 连接与端点（Base URL / 模板 / 请求头）                          │ │
│  │   │ 模型筛选：当前连接已应用平台筛选                                  │ │
│  │   │ ☐ gpt-4.1-mini   文字  结构化文本  已验证        [显示] [测试文本] │ │
│  │   │ ☐ dall-e-3       图片  生图        推断/待验证    [显示] [测试图片] │ │
│  │   │ 添加模型  [上游 ID        ] [显示名] [文字▾] [添加]               │ │
│  │   └──────────────────────────────────────────────────────────────┘ │
│  │ v  官方                    Vertex AI                 1/1 连接   3 模型 │
│  │   （同一外壳；OAuth renderer，无 API Key 表单）         [停用]         │
│  │   [验证凭据] [验证文本] [验证 NB2] [验证 Pro]                          │
│  └────────────────────────────────────────────────────────────────────┘  │
│  未配置 (N)                                              默认折叠          │
│  已停用 (M)                                              默认折叠          │
└──────────────────────────────────────────────────────────────────────────┘
```

宽桌面允许工具栏单行；搜索至少 240px；筛选以按钮/选择呈现，不和 8px 说明抢同一列。协议说明改为工具栏下方一句辅助文字，或移到添加面板。「跳到结果」在无匹配时禁用。Vertex 与 OpenAI 共用卡片外壳；迁移期内设置页下方四按钮卡仍可暂时存在，终态删除（§7.1）。

### 3.2 窄桌面（900–1279px，本设计的主适配面）

不要等 760px 才折行。建议断点：

| 宽度 | 行为 |
| --- | --- |
| ≥1280 | 工具栏单行；设置板双列 |
| 1100–1279 | 设置板仍可双列，但供应商工具栏改为「搜索独占一行，筛选第二行」 |
| 900–1099 | 设置板单列（可把现有 900px 提到 1100px，仅作用于 `.provider-platform`，不是全站） |
| <900 | 创建表单、密钥表单、模型行全部单列；卡片计数改为卡片标题下第二行，不 `display:none` |

窄桌面线框：

```
┌─ AI 供应商与模型 ──────────────┐
│ [搜索供应商、协议或模型 ID   ] │
│ [文字][图片] [能力▾]           │
│ [✓ 仅已验证] [排序▾] [跳到结果] [+] │
│                                │
│ 已配置 (4)                     │
│ ┌ OpenAI                    ┐  │
│ │ 官方 · 1 连接 · 12 模型    │  │
│ │           [重命名] [停用]  │  │
│ │ 密钥标签                   │  │
│ │ [default                 ] │  │
│ │ API Key                    │  │
│ │ [                        ] │  │
│ │ [保存密钥]                 │  │
│ │ [测试连接并同步目录]       │  │
│ │ gpt-4.1-mini               │  │
│ │ 文字 · 已验证              │  │
│ │ [显示] [测试文本]          │  │
│ └────────────────────────────┘  │
└────────────────────────────────┘
```

连接 URL 过长用省略 + `title` 全文；模型行操作按钮折到标题下一行，不横向溢出。列表取消固定 920px 套娃滚动，跟随页面滚动；分组头 `sticky` 保留。

### 3.3 图片费用确认（替换 `window.confirm`）

```
┌ 图片能力测试可能产生费用 ─────────┐
│ 将向当前连接发起一次 1K 图片调用。 │
│ 模型：dall-e-3                     │
│ 连接：OpenAI / default             │
│ 请确认已了解供应商计费规则。       │
│          [取消]  [确认测试]        │
└────────────────────────────────────┘
```

焦点陷阱在对话框内；取消回到触发按钮；确认后 `acknowledge_cost: true`。不要用浏览器原生 confirm 作为唯一确认。

### 3.4 平台级状态

| 状态 | 条件 | UI |
| --- | --- | --- |
| 平台加载 | `providers` 或 `models` 首屏 pending | 主卡骨架/「正在读取供应商与模型目录」，工具栏可用但结果区替换 |
| 平台错误 | 任一方 query error | 结果区错误块 + 重试；**禁止**伪装成「筛选无结果」 |
| 平台空（真无供应商） | 成功且数组空 | 「还没有供应商。先添加自定义连接，或检查服务端预设。」 |
| 筛选空 | 成功但过滤后为空 | 「没有符合当前搜索或筛选的供应商」+ 清除筛选 |
| 部分失败 | 供应商成功、模型失败 | 列表仍渲染；每条连接模型区显示模型目录错误，不写「尚无模型」 |

### 3.5 连接操作状态

| 状态 | UI |
| --- | --- |
| 未配置 | 健康「未配置」；Key 型说明「先保存密钥」，账号型说明「请在服务端配置凭据」；`credential_writable===false` 时 Key 输入禁用并说明「服务端未配置凭据主密钥」 |
| 就绪 | 所有凭据源的「测试连接」可用；模型发现动作是否出现只看 capability |
| 进行中 | 连接操作区 `aria-busy`；一条 status：「正在测试连接…」或过渡组合模式「正在测试连接并同步目录…」；不卸载整个面板 |
| 成功 | 纯验证显示「连接可用 · 420 ms」；组合/发现动作才显示「已发现 N 个模型」；密钥保存成功不说实现细节 |
| 失败 | 错误绑到触发区域（见 §6）；不把密钥明文或凭据路径写入错误；不自动改打 discover |

进行中 **不要** 因 invalidate + version key 卸载面板。刷新列表时保留该连接的本地草稿（Base URL、JSON、密钥输入）。

---

## 4. 可展示模型：加载 / 空 / 错误 / 显隐

模型目录有两份前端形状，设计必须分开读：

| 形状 | 来源 | 适合 |
| --- | --- | --- |
| `ModelCapability` | `GET /models` | 生成台可用性、`auto_eligible` |
| `ProviderModel` | discover/create/PATCH 返回 | 设置页管理：来源、`last_verified_at`、持久 `AIModel.enabled`（影响可调用性） |
| 展示偏好（V02-12 待落地） | 下游统一偏好契约 | 生成台、工作流和项目默认模型选择器的显示/隐藏 |

设置页模型行应以 **连接下的管理视图** 为准。V02-02 已确认 `/models.enabled` 是派生可用性、`AIModel.enabled` 是其输入之一，二者都不是纯展示偏好。过渡阶段可用 `GET /models` 按 `connection_id` 过滤作只读展示；在 V02-12 的展示偏好契约落地前，显隐控件必须呈禁用/说明状态，不能假装保存成功，也不能写回任一 `enabled`。本设计规定 UI 状态机与语义，但不在文档任务中决定服务端 schema。

### 4.1 模型区状态机

```
[连接展开]
    │
    ├─ models 加载中 ──────────── 「正在读取模型目录…」
    ├─ models 错误 ────────────── 「模型目录读取失败」+ 重试
    ├─ 连接探测进行中 ─────────── 列表可保留旧数据 + busy（不得重复 discover）
    ├─ 无模型且支持发现 ───────── 「还没有模型。同步目录，或手工添加上游 ID。」
    ├─ 无模型且不支持发现 ─────── 「还没有模型。手工添加上游 ID，或等待预设种子。」
    ├─ 有模型但筛选空 ─────────── 「没有符合筛选的模型」+ 清除
    └─ 有可见行 ───────────────── 行列表 ± 批量条
```

「尚无模型。先同步模型列表，或手动添加供应商模型 ID。」（`:181`）拆成发现型空态、不支持发现空态与筛选空态三类，见上表。按钮只按能力出现；不支持发现的连接不能被引导去点不存在的同步动作。

### 4.2 单行展示与 confidence 映射

默认行：

- 显示名（主）
- 上游模型 ID（辅）
- 类型：文字 / 图片
- 能力芯片：由 `operations` 映射，最多 3 个 +「更多」
- 置信度芯片（只显示下表中文，不显示英文枚举）
- 显隐开关（创作界面展示偏好，不是可用性；契约未落地时禁用并附说明）
- 主测试按钮；视觉为次按钮；图片为付费样式

`confidence` → 中文（`provider-copy.ts` 纯函数，未知才「未知」）：

| 服务端 `confidence` | UI 标签 | 「仅已验证」 |
| --- | --- | --- |
| `MANUAL` | 待验证 | 隐藏 |
| `DECLARED` | 待验证 | 隐藏 |
| `INFERRED` | 推断/待验证 | 隐藏 |
| `PARTIAL` | 部分验证 | 隐藏 |
| `VERIFIED` | 已验证 | 显示 |
| 其他 / 缺省 / 空串 | 未知 | 隐藏 |

手工添加成功后，断言行上为「待验证」（对应 `MANUAL`）。发现得到的推断模型断言为「推断/待验证」（`INFERRED`）。不得把 `INFERRED` 显示成单独的「待验证」而与 `MANUAL` 无法区分。

行级禁用：

- 连接未配置：模型测试禁用；展示偏好开关仅在 V02-12 写入契约可用时可操作。
- 协议不支持的操作：不渲染按钮，不用 disabled「协议不支持」占位。
- 用户已隐藏且未开「显示已隐藏」：行不出现。

### 4.3 显隐状态

| 展示偏好 | 设置页列表（默认） | 「显示已隐藏」 | 创作界面 |
| --- | --- | --- | --- |
| 显示 | 出现 | 出现 | 出现，但仍受可用性/验证约束 |
| 隐藏 | 不出现 | 出现（标明已隐藏） | 不出现在生成台、工作流和项目默认模型选项；不改变模型调用资格 |

批量：

- 勾选仅针对 **当前筛选后的可见行**（含「显示已隐藏」时的隐藏行）。
- 「隐藏所选」把选中项标隐藏；「显示所选」恢复。
- 不提供「全选全部供应商」跨卡批量，避免一次误伤二十家预设。范围默认 **当前连接**；可选「当前供应商的全部连接」。

### 4.4 「仅显示已验证模型」

过滤条件：**仅** `confidence === "VERIFIED"`（显示「已验证」）。

`MANUAL`、`DECLARED`、`INFERRED`、`PARTIAL` 与未知值一律隐藏。测试必须分别断言这四类 + 未知被过滤，而不是只测 `INFERRED`/`PARTIAL`。

不要用 `auto_eligible`：那还要求连接健康与可用性，会把「已验证但连接暂时降级」的模型藏掉，设置页反而无法去测试。

与类型、能力、搜索同时生效。开关对所有已展开连接一致（平台级），避免每条连接各有一份导致「为什么那家还有未验证模型」。

### 4.5 加载与探测的竞态

- 连接验证成功后只刷新连接健康；组合/发现动作成功后再 invalidate `["models"]` 与该 provider 详情。两者都不重置折叠、筛选、勾选，且同一用户动作不得重复调用 discover。
- 发现动作返回空模型列表：按 capability 进入对应空态，不是错误。
- 验证或发现失败：保留旧列表 + 错误；不自动改打另一个动作。
- 手工添加成功：清空 ID，焦点回 ID 框，新行进入视图，confidence 芯片为「待验证」。

---

## 5. 文案清单：保留 / 精炼 / 删除

中文用户可见字符串一律中文。英文枚举不进主界面。

### 5.1 保留（语义正确，可微调标点）

| 当前文案 | 位置 | 说明 |
| --- | --- | --- |
| 保存密钥 | `:155` | 主操作 |
| 验证凭据（Vertex 卡，迁移期） | `settings/page.tsx:87` | 仅记录当前 OAuth 入口；终态并入统一连接验证，不保留协议专属 renderer |
| 输入 API Key（不会回显） | `:154` | 可作 placeholder |
| 服务端未配置凭据主密钥 | `:154` | 保留为禁用说明 |
| 文本优先使用 Responses API | `:167` | 仅 OPENAI |
| 额外请求头（禁止 Authorization / x-api-key） | `:169` | 精炼：补上 Host、Content-Length，见 §5.2 / §6.3 |
| 图片能力测试会产生一次 1K 调用费用，是否继续？ | `:176` | 语义保留，载体改为对话框 |
| 添加自定义供应商 / 创建 | `:312-313` | 可改为「添加供应商」 |
| 已启用 / 已停用 | `:316` | 分组名保留 |
| 当前筛选条件下没有供应商 | `:278` | 仅筛选空；不要用于加载失败 |
| 正在访问供应商，请稍候… | `:188` | 改为更具体的 status |
| 运行设置页顶栏「系统设置与运行诊断」 | settings | 页面身份，不动 |

### 5.2 精炼

| 当前 | 建议 | 原因 |
| --- | --- | --- |
| `AI PROVIDERS / MODEL PLATFORM` | 中文主标题「AI 供应商与模型」 | 中文产品，英文小标可删 |
| `N 个预设供应商` | `N 家供应商 · M 已配置` | 含自定义 |
| 搜索 placeholder「搜索 OpenAI、Claude、DeepSeek、火山…」 | 「搜索供应商、协议或模型 ID」 | 与真实匹配范围一致；实现后应能搜模型 ID |
| 「OpenAI / Anthropic；Vertex 保留为原生连接。」 | 「可添加 OpenAI 或 Anthropic 兼容连接。Vertex 使用同一供应商卡片，凭据为服务账号而非 API Key。」 | 禁止再指向「本页下方另一张卡」作为终态 |
| 「同步模型」+「验证凭据」（基线 Key 型连接 `:160-161`） | 过渡期合并为「测试连接并同步目录」；统一契约落地后按 capability 显示「测试连接」与可选「同步模型」 | 当前 `CREDENTIALS` 已 discover；任何模式都禁止重复请求上游目录 |
| 「已同步 N 个模型；名称推断结果需验证后才参与自动路由」 | 「连接可用，已发现 N 个模型。推断/待验证的能力需测试通过后才会进入自动路由。」 | 单动作成功文案 |
| `${probe.probe_type}：${probe.status} · ${latency} ms` | 「连接可用，已发现 N 个模型 · 420 ms」/「文本测试失败：上游返回 401」 | 枚举不进 UI；连接级用单动作文案 |
| 额外请求头（禁止 Authorization / x-api-key） | 额外请求头（禁止 Authorization、x-api-key、Host、Content-Length） | 与网络安全规则一致 |
| `HEALTHY` / `DEGRADED` / … | 健康 / 降级 / 未知 / 未配置 / 离线 | 所有凭据来源共用词典，不再暗示独立卡 |
| `API Key 已加密保存；浏览器不会读取明文` | 「密钥已保存」 | 不谈加密实现 |
| 「手动模型已加入；请先测试能力再用于自动路由」 | 「已添加。测试通过前不会进入自动路由。」 | |
| 「手动模型 ID」placeholder | 「上游模型 ID」 | |
| 「添加」 | 「添加模型」 | 与「添加供应商」区分 |
| 分组「已启用」 | 建议拆成「已配置」「未配置」；停用仍独立一组 | 20+ 未配预设不应和已配混在「已启用」 |
| `compatible · LOW` | 「兼容协议 · 低风险」等中文映射 | 建立小词典，见下 |
| `MANUAL` / `DECLARED` / `INFERRED` / `PARTIAL` / `VERIFIED` | 待验证 / 待验证 / 推断/待验证 / 部分验证 / 已验证；其他为「未知」 | 见 §4.2；不得把 `INFERRED` 与 `MANUAL` 显示成同一标签 |
| `正在读取供应商与模型目录…` | 保留 | |
| 启用/停用 | 保留；停用加确认「停用后此供应商不可用于生成」 | |

风险/分类词典（只映射展示，不改字段）：

- `OFFICIAL` → 官方
- `GATEWAY` → 网关
- `THIRD_PARTY` → 第三方
- `LOW` / `OFFICIAL` 风险 → 低风险 / 官方
- `compatible` → 兼容协议（若后端仍发该 category）

### 5.3 删除或移出主路径

| 当前 | 处理 |
| --- | --- |
| 工具栏里的协议英文长说明占第三列 | 移出工具栏，避免窄桌面抢空间 |
| 模型行 disabled 按钮「协议不支持」 | 不渲染 |
| 高级区把端点 JSON 当默认可见能力 | 默认折叠；非开发用户不必看到 |
| 成功 notice 里的 `CREDENTIALS：PASSED` | 删除英文探测类型 |
| 基线 Key 型连接上并排且会重复请求的「同步模型」按钮 | 过渡期删除；统一契约拆分后仅按 capability 恢复独立同步动作 |
| 创建成功无文案、失败才出现 | 成功用 status，不要靠空白 |

### 5.4 需要新增的文案

- 搜索 `aria-label`：「筛选供应商」
- 「跳到结果」按钮；无匹配时禁用
- 筛选空模型：「没有符合筛选的模型」
- 列表错误：「供应商列表读取失败」
- JSON 语法非法：「端点模板不是合法 JSON」/「额外请求头不是合法 JSON」
- JSON 非对象：「必须是 JSON 对象，不能是数组或单值」
- JSON 值非字符串：「每个键和值都必须是字符串」
- 禁头：「不能设置 Authorization、x-api-key、Host 或 Content-Length」
- 删除密钥确认：「删除密钥「default」？已保存的密文将无法恢复。」
- 删除自定义供应商：「删除后连接与模型目录一并移除。进行中的任务会阻止删除。」
- 内置不可删：「内置供应商只能停用，不能删除」
- 乐观并发：「供应商已在别处更新，请重新加载」 / 「连接已在别处更新，请重新加载」
- 通用「测试连接」；过渡组合模式「测试连接并同步目录」；进行中文案与实际动作一致
- 批量条：「已选 N 个模型」
- 仅已验证开关标签：「仅已验证」；辅助：「自动路由只使用已验证模型」
- 类型：「文字」「图片」；能力：「结构化文本」「视觉理解」「图片生成」「图片编辑」
- 排序：「推荐」「名称」「健康」「模型数量」「延迟」——「推荐」旁辅助「已配置、健康、有模型的靠前」
- 冷却：「冷却至 HH:MM」
- 主密钥不可写的保存按钮 title 与可见说明一致

---

## 6. 折叠区键盘、焦点与错误关联

### 6.1 统一折叠控件

三处折叠都改为 **button + `aria-expanded` + `aria-controls`**，不要混用无标签 `<details>` 与自定义按钮。原生 `<details>` 可留，但必须：

- `summary` 可 Tab
- 面板有 `id`
- `aria-controls` 指向面板
- 打开后第一个输入可程序聚焦（仅用户主动打开时，不在页面加载时抢焦点）

目标：

| 区域 | 触发器 | 面板 id 例 |
| --- | --- | --- |
| 添加供应商 | 「添加供应商」 | `provider-create-panel` |
| 供应商分组 | 「已配置」等 | `provider-group-configured` |
| 供应商卡 | 卡片标题按钮 | `provider-card-{id}` |
| 连接高级区 | 「连接与端点」 | `connection-advanced-{id}` |

卡片上的「停用」与标题折叠按钮分开，避免一个按钮既展开又停用。当前结构已经分开（`:250`、`:255`），实现时保持。

### 6.2 键盘

- Tab 顺序：工具栏（搜索 → 类型 → 能力 → 已验证 → 排序 → 跳到结果 → 添加）→ 分组头 → 卡片标题 → 重命名/停用/删除 → （展开后）密钥字段 → 连接操作 → 高级 → 模型行 → 手工表单。
- 分组头、卡片标题、添加、高级：Enter / Space 切换。
- **搜索焦点（前后必须一致，禁止再写「过滤时移焦」）**：
  - 输入、粘贴、防抖过滤：**只**更新匹配集并展开分组/卡片；`document.activeElement` 保持搜索框。
  - 搜索框内 **Enter**（且有匹配）：把焦点移到第一张匹配卡标题。
  - 「跳到结果」点击或激活：同上；无匹配时按钮 `disabled`，不移焦。
  - 不得在防抖回调、`useEffect(filter)` 或 `forceExpanded` 里 `focus()`。
- 模态确认（图片费用、删除）：初始焦点在「取消」或「确认」中破坏性较弱的一个；Esc 关闭；关闭后焦点回触发按钮。
- 不要用 `display:none` 丢掉卡片计数导致读屏只听到名字、不知道有多少模型。

### 6.3 错误关联

规则：谁失败，谁旁边说，谁被 `aria-invalid`。

| 失败 | 关联 |
| --- | --- |
| 创建供应商 | 名称/URL 字段 `aria-describedby` → `#provider-create-error`；打开添加区；焦点到第一个无效字段 |
| 保存密钥 | `#connection-{id}-key-error`；焦点到 API Key |
| 保存连接 / JSON | 对应 textarea `aria-invalid` + 字段下错误（语法、非对象、非字符串值、禁头分条） |
| 测试连接 / 同步模型 / 余额 | `#connection-{id}-action-error`，放在操作按钮行下，不是面板最底与密钥错误混用 |
| 重命名 / 删除供应商 | `#provider-{id}-edit-error`；409 原文 |
| 手工模型 | `#connection-{id}-manual-error` |
| 平台查询 | 结果区 `#provider-platform-error`，`role="alert"` |
| 模型查询 | 该连接模型区 alert |

连接级不要再把 6 个 mutation 折成一条 `error`（当前 `:138-139`）。至少分成：密钥、连接保存、连接探测（单动作）、余额、手工模型、供应商生命周期。

成功消息用 `role="status"` / `aria-live="polite"`，错误用 `role="alert"`。密钥相关错误文本禁止包含输入值。

**JSON 校验（失焦 + 保存前，不靠 `JSON.parse` 抛 `SyntaxError`）**，两个 textarea 分开报错：

1. **语法**：`JSON.parse` 失败 → 「不是合法 JSON」。
2. **顶层对象**：必须是非 null 普通对象；数组、数字、字符串、`null` → 「必须是 JSON 对象，不能是数组或单值」。
3. **键值字符串**：每个 key 为非空字符串；每个 value 必须是字符串（禁止嵌套对象/数组/数字/布尔）。→ 「每个键和值都必须是字符串」。
4. **`endpoint_templates` 形状**：满足 1–3；值视为路径或 URL 模板字符串。允许空对象。不在本设计新增 schema 去枚举合法键名；未知键可以保存（服务端再拒）。
5. **`extra_headers` 禁头**：在 1–3 通过后，键名大小写不敏感，禁止 `Authorization`、`x-api-key`、`Host`、`Content-Length`（含 `X-Api-Key` 等变体）。→ 「不能设置 Authorization、x-api-key、Host 或 Content-Length」。

任一项失败：「保存连接」`disabled`，该 textarea `aria-invalid="true"`，`aria-describedby` 指向字段下错误。

---

## 7. 组件拆分建议

目标：单文件 318 行拆成可测单元；样式仍用现有 `provider-*` class，不引入新设计系统。文件名 kebab-case，组件 PascalCase。

建议目录 `apps/web/components/provider-settings/`：

| 模块 | 职责 | 大约边界 |
| --- | --- | --- |
| `provider-management.tsx` | 查询、筛选 state、分组数据、平台错误/加载 | 现在的 export |
| `provider-toolbar.tsx` | 搜索、类型、能力、已验证、排序、跳到结果、添加开关 | 现在 `:310` |
| `provider-create-form.tsx` | 自定义创建、字段错误 | `:311-315` |
| `provider-lifecycle-controls.tsx` | 重命名、删除、启用/停用、409/version | 卡片头操作 |
| `provider-group.tsx` | 分组折叠 | `:261-280` |
| `provider-card.tsx` | **统一外壳**：头、启用、展开；不按供应商名分支 | `:233-259` |
| `connection-panel.tsx` | 连接布局；按 `credential_source` 与 capability 插槽渲染 | `:27-192` 骨架 |
| `credential-source-renderer.tsx` | `CONNECTION_KEY` / `ENV_SERVICE_ACCOUNT` 凭据区；不读取协议名 | 新建 |
| `connection-credentials.tsx` | API Key 表单与芯片、脱敏（仅 `api_key`） | `:151-157` |
| `connection-actions.tsx` | 所有连接共用验证；按 capability 插入发现、余额动作；禁止重复 discover | `:159-163` |
| `model-probe-actions.tsx` | 按模型 `operations` 生成文本、视觉、图片 `MODEL_SMOKE`；图片带费用确认 | 从现有模型行与专用卡迁入 |
| `connection-advanced.tsx` | Base URL、JSON 形状/禁头校验、保存 | `:164-171` |
| `model-catalog.tsx` | 模型状态机、筛选应用、批量条 | `:172-182` |
| `model-row.tsx` | 单行展示、测试、展示偏好、可用性与 confidence 芯片 | 行 |
| `manual-model-form.tsx` | 手工添加 | `:183-187` |
| `provider-copy.ts` | 健康/置信度全表/风险/能力中文映射 | 新建 |
| `provider-filters.ts` | 搜索、排序、筛选纯函数 | 抽出 `:204-230`、`:299-306` |
| `provider-json.ts` | 端点模板与 extra_headers 校验 | 新建 |
| `api.ts` 增补 | `deleteProvider`；重命名走已有 `updateProvider` | `api.ts:821` 起 |

`settings/page.tsx` 继续挂 `<ProviderManagement />`。不要把平台塞进 `app/providers.tsx`。

### 7.1 移除 Vertex 四按钮卡：迁移期兼容与退出条件

现有 `.vertex-control` 四按钮卡（`settings/page.tsx:81-94`）**不是终态双入口**。允许暂时并存，必须同时满足退出条件后删除该 article：

1. V02-02 Phase B 的统一连接健康/验证端点可用，旧 `/settings/vertex/*` 仅作兼容转发且前端不再调用。
2. `ProviderCard` 按 `credential_source=ENV_SERVICE_ACCOUNT` 渲染账号型连接，不出现 API Key 表单，也不展示凭据文件路径。
3. 文本、视觉、图片测试均由目录模型 `operations` 生成，不再硬编码 NB2/Pro 或固定四动作。
4. 设置页主栏不再出现第二张「VERTEX AI / PROVIDER」卡；状态条读取统一连接健康聚合。
5. 迁移期可用 `id="vertex-native"` 做页内跳转；退出后该锚点落在统一供应商卡片上。回归断言 `.vertex-control` 不再渲染。

未满足前：专用卡可留，但文档与实现注释必须写明「迁移期，待 §7.1 退出」。新功能不得写入专用卡；通用动作只进入 `connection-actions` / `model-probe-actions`。

纯函数优先单测：排序、搜索（含模型 ID）、类型/能力/已验证过滤、confidence 映射全表、JSON 形状与禁头、文案映射。组件测交互与可访问性。

---

## 8. 测试矩阵

现有 `provider-management.test.tsx` 三条全部保留（健康错误可见、创建失败、凭据失败不回显）。下面按实现切片补齐。默认 Vitest + Testing Library；布局用 class/CSS 断言或 jsdom 宽度；Playwright 仅在已有设置页 E2E 上加一条冒烟，不作为本设计的门禁。

### 8.1 供应商生命周期（创建 / 编辑 / 重命名 / 删除）

前端必须有 `api.deleteProvider`；重命名走已有 `updateProvider({ version, name })`。下列均须实现与测试，不许标「若做」。新增备用连接为单独 `DEFERRED` 切片；编辑和停用现有连接不延期。

| ID | 场景 | 期望 |
| --- | --- | --- |
| P1 | 名称或 URL 为空 | 提交按钮禁用 |
| P2 | 创建 400（非法 URL 等） | 字段 `aria-invalid` + `aria-describedby`；面板保持打开 |
| P3 | 创建 409（名称已存在） | 错误可见并关联名称字段；不关闭面板（现有用例升级选择器） |
| P4 | 创建成功 | 刷新 providers；表单清空；新卡展开；焦点到密钥输入 |
| P5 | 停用内置 | `PATCH` `{ version, enabled: false }`；卡进入已停用 |
| P6 | 重命名成功 | `PATCH` `{ version, name }`；标题更新 |
| P7 | 重命名 409 乐观并发 | 中文「供应商已在别处更新，请重新加载」；本地输入不丢；可放弃草稿 |
| P8 | 删除自定义成功 | 确认后 `DELETE /providers/{id}`；卡片消失 |
| P9 | 内置不可删 | 无删除按钮；`built_in === true` 不调用 DELETE |
| P10 | 删除 409 任务占用 | 展示服务端中文 detail，卡片仍在 |
| P11 | 删除 409 内置（误调防护） | 若测试直接打 API，UI 展示「内置供应商只能停用，不能删除」 |
| P12 | 人工停用账号型连接 | `PATCH` 连接 enabled=false；响应/后端契约清除 `auto_enable_pending`，凭据仍 ready 也不自动恢复 |
| P13 | 凭据暂时不可见 | 已启用账号型连接保持 enabled；UI 显示 readiness/健康降级，不擅自发送停用 PATCH |

### 8.2 连接探测 / 手工模型 / JSON

连接验证、模型发现和模型冒烟是三种语义。基线组合模式允许一次 `CREDENTIALS` 同步目录；统一契约模式按 capability 分开显示。测试必须证明同一点击最多触发一次对应动作，且凭据验证不触发文本/图片生成。

| ID | 场景 | 期望 |
| --- | --- | --- |
| C1 | `CONNECTION_KEY` / `ENV_SERVICE_ACCOUNT` | 前者渲染密钥表单，后者只显示服务端管理状态；同一协议字符串不参与判定 |
| C2 | 统一凭据验证成功 | 一次 `CREDENTIALS`；中文 status 含延迟；不调用文本/图片生成，不重复 discover |
| C3 | 凭据验证 401 | 操作区错误；DOM 无密钥明文或凭据路径 |
| C4 | 支持模型发现 | 显示同步动作；一次 discover 后列表出现结果，`INFERRED` 映射为「推断/待验证」 |
| C5 | 不支持模型发现 | 不渲染同步按钮；空态引导手工建模/预设种子，不按协议名判断 |
| C6 | 手工添加 | POST 含 ID、显示名、类型；成功后 ID 清空；新行 `confidence` 映射为「待验证」（`MANUAL`）；开启「仅已验证」后该行消失 |
| C7 | 图片测试 | 先出确认层；取消不发请求；确认后 `acknowledge_cost: true` |
| C8 | Anthropic 图片 | 无图片测试按钮 |
| C9 | JSON 语法非法 | 保存禁用；该 textarea 中文错误；不出现 `SyntaxError` |
| C10 | JSON 顶层为数组/`null`/数字 | 「必须是 JSON 对象…」；`aria-invalid` |
| C11 | 嵌套对象或非字符串值 | 「每个键和值都必须是字符串」 |
| C12 | `extra_headers` 含 `Authorization` / `x-api-key` / `Host` / `Content-Length`（大小写变体） | 「不能设置 Authorization…」；仅该字段报错 |
| C13 | 合法 `{"models":"/models"}` 与空对象 | 保存启用；无错误 |

### 8.3 搜索 / 筛选 / 排序

| ID | 场景 | 期望 |
| --- | --- | --- |
| F1 | 名称命中 | 只留匹配供应商；分组强制展开 |
| F2 | 模型 ID 命中 | 含该模型的供应商可见（实现搜索扩展后） |
| F3 | 无命中 | 筛选空文案，不是加载失败 |
| F4 | 类型=图片 | 文字行隐藏 |
| F5 | 能力=视觉 | 仅 `multimodal_analysis` |
| F6 | 仅已验证 | `VERIFIED` 可见；`MANUAL`/`DECLARED`/`INFERRED`/`PARTIAL`/未知均隐藏；芯片文案符合 §4.2 |
| F6b | `mapConfidence` 纯函数 | `MANUAL`/`DECLARED`→待验证；`INFERRED`→推断/待验证；`PARTIAL`→部分验证；`VERIFIED`→已验证；`""`/`FOO`→未知 |
| F7 | 类型+已验证+搜索 组合 | 同时生效 |
| F8 | 排序=健康 | HEALTHY 先于 OFFLINE；中文名稳定次序 |
| F9 | 排序=延迟 | 无延迟的排在有延迟之后（当前 `Infinity` 行为需在实现时产品确认，见开放问题） |

### 8.4 批量显隐与已验证

| ID | 场景 | 期望 |
| --- | --- | --- |
| V1 | 隐藏单行 | 写展示偏好；默认列表消失；「显示已隐藏」可见；`AIModel.enabled` 不变 |
| V2 | 批量隐藏 | 调用 V02-12 确定的批量/单条偏好接口；部分失败保留失败项；不得调用模型 enabled PATCH |
| V3 | 隐藏后仅已验证 | 隐藏项即使已验证也不在默认列表 |
| V4 | 三态分离 | 分别构造展示隐藏、`AIModel.enabled=false`、派生 `/models.enabled=false`，断言 UI 与写入目标不混淆 |

### 8.5 键盘 / 焦点 / 错误

| ID | 场景 | 期望 |
| --- | --- | --- |
| A1 | Tab 到分组头，Space 折叠 | `aria-expanded` 切换 |
| A2 | 打开添加区 | 焦点到名称 |
| A3 | 创建失败 | 焦点到无效字段；`aria-describedby` |
| A4 | 打开高级区 | 焦点到 Base URL |
| A5 | 图片确认 Esc | 焦点回「测试图片」 |
| A6 | 删除密钥有可访问名称 | 现有 `aria-label` 保留并加确认 |
| A7 | 搜索输入与防抖 | 分组展开、列表过滤；焦点仍在搜索框 |
| A8 | 搜索框 Enter 且有匹配 | 焦点到第一张匹配卡标题 |
| A9 | 「跳到结果」 | 有匹配则移焦；无匹配按钮 disabled 且不移焦 |

### 8.6 密钥脱敏

| ID | 场景 | 期望 |
| --- | --- | --- |
| S1 | 列表只出现 hint | 无完整密钥 |
| S2 | 输入 type=password | 保留 |
| S3 | 保存成功清空 | 保留 |
| S4 | 任意错误 DOM | `document.body.textContent` 不含输入明文（已有 C3） |
| S5 | hint 展示 | 接受 `••••1234` 或夹具 hint，不把 `sk-live-` 当合法展示 |

### 8.7 加载 / 空 / 错误

| ID | 场景 | 期望 |
| --- | --- | --- |
| L1 | providers pending | 平台加载，不渲染空分组 |
| L2 | providers reject | 平台错误 + 重试 |
| L3 | models reject | 连接头可见，模型区错误 |
| L4 | 模型空数组 | 真空间态 |
| L5 | 主密钥不可写 | 保存禁用 + 说明 |

### 8.8 窄桌面

| ID | 场景 | 期望 |
| --- | --- | --- |
| N1 | 1100px 工具栏 | 搜索独占一行，筛选不横向溢出 |
| N2 | 900px 创建/密钥 | 单列，按钮可点区域 ≥ 32px |
| N3 | 卡片计数 | 可见，不只在宽屏 |
| N4 | 长 URL / 长模型 ID | 省略不撑破卡片 |

### 8.9 明确不测

- 真实供应商、真实 Key、付费图片。
- PostgreSQL/Redis/Playwright 全量（除非后续独立 E2E 任务）。
- 服务端 discover 有界读取、凭据加密（已有后端测试）。

---

## 9. V02-02 已确定契约与 V02-12 待决项

V02-02 已在提交 `5ac0383` 合并。本节把已确定的后端/跨模块方向与仍需 V02-12 决定的展示偏好分开，避免实现 Issue 继续把已回答问题标成 `BLOCKED`。

### 9.1 V02-02 已确定（实现必须遵守）

1. 凭据渲染依据是 `credential_source = CONNECTION_KEY | ENV_SERVICE_ACCOUNT`，不是供应商名或协议字符串。
2. 连接健康与验证使用统一入口；`CREDENTIALS` 不做文本/图片生成，模型冒烟使用 `MODEL_SMOKE(catalog_model_id)`。
3. 模型发现由连接能力表声明；不支持发现时提供手工建模/预设种子，不能显示无效同步按钮。
4. 模型可用性保持 `AIModel.enabled && connection.enabled && provider.enabled`，Key 型另需可用 Key；`/models.enabled` 是派生结果。
5. 账号型新连接使用 `auto_enable_pending` 一次性状态机；旧连接不回填，人工启停清除 pending，凭据暂时不可见不自动停用。
6. 设置页终态不保留 Vertex 专属状态请求、验证卡或硬编码模型按钮；差异进入凭据来源与模型能力 renderer。
7. 自动路由资格是 `confidence == "VERIFIED"` 且连接 `HEALTHY`；设置页「仅已验证」只按 confidence 浏览，不等同于 auto eligibility。

### 9.2 V02-12 / 实现 Issue 仍需决定

1. **创作界面展示偏好存在哪里？** 必须独立于 `AIModel.enabled` 和派生 `/models.enabled`，并由生成台、工作流、项目默认模型选择器共同消费。本审计不决定 schema。
2. **设置页管理列表用哪个 GET？** 需要能同时读取来源、`last_verified_at`、持久可用性和展示偏好；若扩展 `GET /models`，字段名不得继续重载 `enabled`。
3. **批量展示/隐藏 API**：采用批量端点还是多个偏好 PATCH；必须定义部分失败响应和重试语义。
4. **手工模型能力修正**：`ProviderModelUpdate` 已能改 `operations`/`capabilities`，首发是只做添加 + 测试，还是同时开放修正表单。
5. **新增备用连接 UI**：后端端点已存在，但连接优先级、删除、故障切换与资源所有权应另开实现 Issue；不是 V02-02 契约阻塞。
6. **模型发现端点形态**：Phase B 若保持组合动作，UI 只渲染一次；若拆分验证与发现，必须由响应能力明确告知，不能靠协议猜测。
7. **可用性文案**：不可用但仍设置为展示的模型，在设置页应显示「未就绪」，创作界面是隐藏还是禁用展示需统一。

### 9.3 可直接落地的 UI 决策

- 不新增路由；入口仍是 `/settings`。
- 供应商生命周期：创建走 `createProvider`；重命名/停用走 `updateProvider`；删除补 `deleteProvider` wrapper 后走已有 `DELETE`。
- 编辑/人工启停现有连接纳入首发；新增备用连接单独 `DEFERRED`。
- 所有凭据、发现、验证和模型测试控件按 `credential_source` 与 capability 渲染。
- 筛选「仅已验证」用 `confidence === "VERIFIED"`；芯片映射用 §4.2 全表。
- 展示偏好写入契约未落地时，显隐控件显示禁用说明；不提供只在本地生效的假保存，不写 `AIModel.enabled`。
- 窄桌面断点只改 `.provider-platform` 及相关表单 grid。
- Vertex 专用四按钮卡只作迁移兼容；满足 §7.1 后删除，新动作只加到通用组件。

---

## 10. 建议实现切片（供后续 PR，本任务不执行）

1. **供应商生命周期**：`deleteProvider` wrapper；创建/重命名/停用/删除 UI；400/409/乐观并发/内置无删除按钮。补 P1–P11。
2. 文案映射（含 confidence 全表）+ 平台加载/错误/筛选空态 + 搜索扩展（输入不抢焦点；Enter / 跳到结果才移焦）。补 L1–L3、F1–F3、A7–A9。
3. 工具栏筛选（类型/能力/已验证/排序）与窄桌面 grid；补 F4–F8、F6b、N1–N4。F6 断言 MANUAL/DECLARED/INFERRED/PARTIAL/未知。
4. 折叠/焦点/字段错误 + JSON 语法/对象/字符串值/禁头校验（`provider-json.ts`）。补 A1–A6、C9–C13。
5. **连接契约 UI**：凭据来源 renderer、现有连接人工启停、统一 CREDENTIALS、按能力发现、图片费用确认与脱敏。补 P12–P13、C1–C8、S1–S5；Spy 断言无重复 discover、无凭据泄露。
6. 模型行状态机 + 手工模型显示名与 `MANUAL`→待验证（C6）；展示偏好只在 V02-12 写入契约落地后启用，补 V1–V4。
7. **统一连接外壳**：`credential_source` renderer + 目录驱动 `model-probe-actions`；满足 §7.1 后删除 `.vertex-control`。未退出前不往专用卡加功能。
8. **新增备用连接**另开切片，定义优先级/删除/故障切换后再实现，不阻塞编辑现有连接。
9. 与 V02-12 对齐生成台、工作流和项目设置选模，确保展示偏好只有一份事实来源。

每片保持可独立审查。不要顺手改运行设置、诊断、roadmap 或 `plan.md`。

---

## 11. 本审计未做的验证

- 未运行 `npm run check`、Vitest、Playwright、浏览器实机。
- 未读凭据、未调用真实供应商、未连 PostgreSQL/Redis。
- 已在 lead 接管修订时对照合并提交 `5ac0383` 的 V02-02 契约；V02-12 尚无可供本审计核对的已批准实现契约。
- 行号相对基线 `1eb2ae1c`；后续提交后需要对照更新。
