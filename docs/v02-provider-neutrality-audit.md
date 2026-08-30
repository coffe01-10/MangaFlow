# V02-02 供应商平权审计：Vertex 特判盘点与统一契约设计

- 基线提交：`1eb2ae1c1a6d1332e601d01e6cb5d98edb0037ef`（分支 `master`）
- 工作分支：`codex/v02-02-provider-neutrality-audit`
- 任务性质：L2 只读审计/设计，不实现重构；唯一交付物为本文件
- 对应任务：GitHub Issue #39（`[0.2.0][V02-02] Audit Vertex coupling and define provider-neutral contracts`）
- 修订记录：2026-08-30 正式修复轮（唯一一次）后由 lead 接管收口——1.2 节门禁改为 PowerShell 5.1 可验证的 0/1/2 契约并固定 Git 工作目录（P1-2）、L2/R14/4.3 别名规范化映射及 Page/Asset 候选夹具（P1-1）、Phase C grandfather 零回填与 `auto_enable_pending` 一次性状态机（P1-3）、R13/M9 对象更名 `GenerationRecord`/`ModelCallAttempt`（P1-4）、P4 表述收窄、M5/M7/M13/M14 重写并新增 §6 派发约定
- 说明：`docs/roadmap.md` 基线中无 `V02` 编号条目（该文件为工作日志体，最近条目到 PR #38）；本审计以 Issue #39 为唯一范围依据
- 所有 `文件:行号` 均以基线提交为准

## 1. 执行摘要与"供应商平权"的可验证定义

### 1.1 执行摘要

当前代码库已有一个协议中立的供应商目录骨架（`ProviderProfile → ProviderConnection → ProviderKey / AIModel`，兼容协议 OPENAI/ANTHROPIC 走统一 `CompatibleRuntime`），`/models` 可用性判定、页面 readiness 统计和自动路由评分这三条核心链路已经是目录驱动、协议无关的。这是平权改造的有利基础。

但 Vertex 原生通道在基线中仍享有 **四类产品级偏置**，分布在 9 个后端模块和 5 个前端入口：

1. **准入偏置**：预设初始化把 vertex-ai 连接自动置为启用、健康特判为 `DEGRADED`，并注入 3 个 `confidence="VERIFIED"`、`priority=90` 的模型（`provider_presets.py:320`、`provider_presets.py:326-330`、`provider_presets.py:443-450`）；其他供应商必须手动录入 Key 并完成能力测试才能参与自动路由。结果是全新安装未做任何验证时，Vertex 模型即具备自动路由资格——"原生协议实现不同"被实现成了"产品必须优先"。
2. **凭据与健康双轨制**：Vertex 走环境级服务账号 + 独立 `ProviderHealth` 单行表 + 专属 `/settings/vertex/status`、`/settings/vertex/verify` 端点（以及无人调用的 `/models/vertex/*` 兼容别名），其余协议走加密 `ProviderKey` + 连接级健康字段。Dashboard/设置页有独立的 Vertex 状态请求和专属验证卡片，且首页请求预算测试静态固化了这一偏置（`test_dashboard_ai_overview.py:316`）。
3. **默认值偏置**：新项目默认 `image_model_alias="image.nano_banana_2"`（`projects.py:60`、`models.py:85`、`schemas.py:695`）、`text_model_alias="text.fast"`（`models.py:82`、`schemas.py:29`）；风格分析、剧情解析、质量检查、默认工作流全部硬编码 `model_alias="text.fast"`（`asset_generation.py:461`、`asset_generation.py:494`、`sources.py:199`、`workflow/inspection.py:53`、`workflow_engine/catalog.py:239-253`）。
4. **能力偏置**：Vertex 连接不能做模型发现（`provider_catalog.py:502-506` 直接返回预设固定 3 个模型），前端按协议字符串隐藏密钥表单（`provider-management.tsx:151`），验证请求 schema 硬编码两个图片别名 Literal（`settings_schemas.py:58`）。

统一方向：把"凭据形态、结构化输出模式、错误分类映射"这三类**真实协议差异**下沉为声明式的 `credential_source` / 能力字段 / 每协议分类器，把上述四类产品偏置从代码中移除；`text.fast`、`image.nano_banana_*` 降级为**只读兼容层**（历史项目与历史候选中的真实 provider/model ID 保持可解析，含 `image.fast`/`image.quality` 迁移映射，见 `data-model.md:143`），不再作为任何新建数据的默认值。

### 1.2 "供应商平权"的可验证定义

以下五条每条都可由自动化检查判定，通过即视为平权达成：

1. **目录可达性等价**：任意协议连接在录入等价凭据后，可获得相同的模型目录能力（发现或手动建模、能力测试、参与显式选择与自动路由）。判定：代码中不存在仅针对某个 `preset_key` / 协议字符串授予目录特权（预置 `VERIFIED`、跳过发现、自动启用）的分支；白名单仅允许出现在适配器绑定与凭据适配层。
2. **默认值中性**：新建项目、任务、工作流节点产生的模型引用不指向硬编码供应商别名。判定：`text.fast` 与 `image.nano_banana_*` 在业务写路径中只允许作为 legacy_alias 解析输入出现，不允许作为新建实体默认值或新建任务硬编码实参。
3. **健康与验证入口统一**：每个连接使用同一健康查询与验证端点、同一验证级别枚举；UI 不出现协议专属验证卡片。判定：`/settings/vertex/*` 与 `/models/vertex/*` 端点删除（或收窄为纯兼容别名且前端不再调用），前端状态请求按连接发起。
4. **统计口径唯一**：Dashboard、供应商列表、`/models`、页面 readiness 的"可用/已配置/健康"判定共用同一个函数（现状已基本达成：`model_availability.py:12-32`），且该函数不再含协议硬编码分支。判定：`native_configured` 类第二份判定不存在。
5. **协议字符串引用清单化**：产品代码中 `VERTEX_NATIVE`、`vertex-ai`、`vertex_configured` 的引用仅允许出现在受版本控制的允许清单文件中。判定：`scripts/check-provider-neutrality.ps1`（Phase A 按本节内嵌脚本逐字落地）退出码为 `0`。

五条规则的执行载体与通过判据：

| 规则 | 执行载体 | 通过判据 |
| --- | --- | --- |
| 1 目录可达性等价 | pytest M1-M4（第 6 节；M1 覆盖跨协议目录/显式与自动路由，M2-M4 覆盖预设、验证） | 全部通过 |
| 2 默认值中性 | pytest M5/M6/M7 + 本脚本 | 全部通过 |
| 3 健康与验证入口统一 | pytest M4/M8/M9 + 组件测试 M10-M12 | 全部通过 |
| 4 统计口径唯一 | pytest M1（含"无第二份 configured 判定"断言） | 全部通过 |
| 5 协议字符串清单化 | `scripts/check-provider-neutrality.ps1` | 退出码 0 |

脚本与允许清单均为受版本控制文件。退出码契约：`0` = 无违规；`1` = 存在允许清单外命中（逐条打印 `文件:行号:匹配文本`）；`2` = 运行环境不可用（Git 命令、仓库元数据或允许清单缺失，或 `git grep` 致命失败）。以下为 Phase A 实现 Issue 必须逐字落地的脚本；允许清单格式为"每行一个 git grep 输出路径（`/` 分隔），`#` 开头为注释"，初始清单由脚本的 `-UpdateAllowlist` 在 Phase A 分支上生成（不手抄，避免遗漏），门禁范围 `apps/`（`tests/` 与 `tests/e2e` 的偏置断言由 M1-M14 覆盖，不进本门禁）。

`scripts/check-provider-neutrality.ps1`：

```powershell
#Requires -Version 5.1
param(
  [switch]$UpdateAllowlist,
  [string]$RepositoryRoot
)
$ErrorActionPreference = 'Stop'

function Exit-EnvironmentError([string]$Message) {
  [Console]::Error.WriteLine($Message)
  exit 2
}

$repoRoot = if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
  [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
} else {
  [IO.Path]::GetFullPath($RepositoryRoot)
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.git'))) {
  Exit-EnvironmentError 'repository metadata missing'
}
if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
  Exit-EnvironmentError 'git command missing'
}
$patterns = 'VERTEX_NATIVE', 'vertex-ai', 'vertex_configured'
$allowlistPath = Join-Path $PSScriptRoot 'provider-neutrality-allowlist.txt'
if (-not (Test-Path -LiteralPath $allowlistPath)) {
  Exit-EnvironmentError 'allowlist missing'
}
$allowed = @{}
Get-Content -LiteralPath $allowlistPath | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith('#')) { $allowed[$line] = $true }
}
$hits = @()
foreach ($pattern in $patterns) {
  $patternHits = @(& git -C $repoRoot grep -n -I --fixed-strings -e $pattern -- apps 2>&1)
  $grepExit = $LASTEXITCODE
  if ($grepExit -gt 1) {
    $patternHits | ForEach-Object { [Console]::Error.WriteLine([string]$_) }
    Exit-EnvironmentError "git grep failed for pattern: $pattern"
  }
  if ($grepExit -eq 0) {
    $hits += $patternHits | ForEach-Object { ([string]$_).Replace('\', '/') }
  }
}
if ($UpdateAllowlist) {
  if ($allowed.Count -ne 0) {
    Exit-EnvironmentError '-UpdateAllowlist requires an empty allowlist'
  }
  $paths = @($hits | ForEach-Object { ($_ -split ':', 2)[0] } | Sort-Object -Unique)
  $content = @('# Generated baseline; remove a path when its final allowed hit is removed.') + $paths
  [IO.File]::WriteAllLines(
    $allowlistPath,
    $content,
    (New-Object Text.UTF8Encoding($false))
  )
  exit 0
}
$violations = @($hits | Where-Object {
  $path = ($_ -split ':', 2)[0]
  -not $allowed.ContainsKey($path)
} | Sort-Object -Unique)
if ($violations.Count -gt 0) { $violations; exit 1 }
exit 0
```

Phase A 落地顺序：提交**仅含注释、不含路径**的空清单与脚本 → 在任意当前目录运行 `powershell -ExecutionPolicy Bypass -File <repo>\scripts\check-provider-neutrality.ps1 -UpdateAllowlist`，脚本用 `git -C $repoRoot` 生成排序、去重、UTF-8 无 BOM 的初始快照并提交 → 门禁生效。`-UpdateAllowlist` 只接受空清单，防止后续误把新偏置自动加入白名单。`-RepositoryRoot` 仅供脚本级测试把临时脚本指向受测仓库；CI/开发者正常调用必须省略，使仓库根固定为脚本父目录。此后每修复一个 [偏置] 项，同步从允许清单删除对应文件行（文件级粒度：该文件最后一个命中消失时删除），Phase E 收敛至仅适配器层与 legacy 解析层（终态清单以实际生成为准，本审计不预先手抄）。Phase A 必须在 Windows PowerShell 5.1 实跑四种情形：正常 `0`、清单外命中 `1`、清单缺失 `2`、从仓库外以绝对脚本路径执行仍扫描同一 `$repoRoot`。

## 2. 特判清单（带文件/行号）

分类标记：**[偏置]** 必须删除的产品级偏置；**[技术差异]** 必要协议/凭据差异，保留但需抽象为声明式契约；**[兼容]** 历史数据兼容，保留；**[中性]** 已平权，仅记录现状。

### 2.1 配置与凭据层

| # | 位置 | 当前行为 | 分类 | 说明与建议 |
| --- | --- | --- | --- | --- |
| C1 | `apps/api/app/config.py:22-25` | `google_cloud_project` / `google_cloud_location` / `google_application_credentials` / `google_genai_use_vertexai` 为顶级设置 | [技术差异] | 服务账号三要素是合法的环境级凭据形态。建议归入统一的 `credential_source=ENV_SERVICE_ACCOUNT` 契约（第 4 节），字段可保留 |
| C2 | `apps/api/app/config.py:27-29` | `vertex_text_model` 等 3 个模型 ID 以环境变量形式硬编码默认值 `gemini-3.5-flash` 等 | [偏置] | 模型 ID 应属于目录数据而非应用配置；过渡期保留为预设种子值来源，Phase C 后仅作为 legacy 模型 ID 解析兜底 |
| C3 | `apps/api/app/config.py:66-72` | `vertex_configured` 属性：仅检查 Vertex 三要素 | [偏置] | 与 `provider_credentials_writable`（`config.py:74-88`）构成两套凭据判定。统一为连接级 `credential_ready`（第 4 节 C-3） |
| C4 | `apps/api/app/services/vertex_credentials.py:86-225` | `VertexCredentialManager`：进程级凭据缓存、串行刷新、认证失败重试、退避 | [技术差异] | 逻辑本身协议中立（OAuth 刷新是服务账号形态的必需品）。保留；命名去 Vertex 化并入通用凭据层 |
| C5 | `apps/api/app/services/vertex_credentials.py:25-77` | `classify_vertex_failure`：按错误文本/状态码映射为统一错误码（AUTHENTICATION/PERMISSION/RATE_LIMIT/…） | [技术差异] | 错误码集合本身已是统一契约（`architecture.md:113`）；问题在归属：`google.py:16,38` 的 **GOOGLE_NATIVE 适配器复用了这个"vertex"分类器**。建议每协议一个 classifier、共享码集，文件名中性化 |
| C6 | `apps/api/app/model_adapters/vertex.py:28-29` | 适配器构造时检查 `settings.vertex_configured`，否则抛 `AUTHENTICATION` | [技术差异] | 等价于兼容协议的"无 Key 不发请求"；保留但改为凭据契约检查 |

### 2.2 预设初始化与目录

| # | 位置 | 当前行为 | 分类 | 说明与建议 |
| --- | --- | --- | --- | --- |
| P1 | `apps/api/app/services/provider_presets.py:320` | 创建预设连接时 `enabled=preset.key == "vertex-ai" and settings.vertex_configured`，其余预设连接一律 `enabled=False` | [偏置] | 唯一凭凭据状态自动启用的供应商。统一规则：所有预设连接 `enabled` 由统一凭据就绪函数决定（Key 型连接录入首个启用 Key 时启用——现状 `provider_catalog.py:325-326` 已有，Key 型与账号型规则对齐即可） |
| P2 | `apps/api/app/services/provider_presets.py:326-330` | vertex 连接初始 `health_state="DEGRADED"`（已配置时），其余 `UNCONFIGURED` | [偏置] | "已配置但未验证"对 Key 型连接同样成立（`provider_catalog.py:324` 有等价 message）。统一初始态语义 |
| P3 | `apps/api/app/services/provider_presets.py:342-343` | `ensure_provider_presets` 每次调用都执行 `sync_vertex_connection_health` + `_ensure_vertex_models`；该函数又在每次 `resolve_model`（`model_router.py:74`）、`/models`（`routes/models.py:22`）、readiness（`page_readiness.py:188`）、启动（`main.py:52`）被调用 | [偏置] | 每次模型解析都隐式执行 Vertex 特有同步/注入，是性能与正确性的双重耦合。改为一次性迁移/显式管理动作 |
| P4 | `apps/api/app/services/provider_presets.py:384-455` | `_ensure_vertex_models` 硬编码 3 个模型定义；创建时 `confidence="VERIFIED"`、`enabled=True`、`priority=90`（`provider_presets.py:446-448`）；对已存在行用 `setattr` 重写 definition 内字段（`provider_presets.py:452-454`） | [偏置] | **最重偏置**：其他来源模型必须 `confidence == "VERIFIED"` 且连接 `HEALTHY` 才能自动路由（`model_router.py:58-62`、`model_router.py:223-224`），Vertex 模型未经验证即获满置信度。重写范围需精确表述：`provider_presets.py:452-454` 只覆盖 definition 内 9 个字段（provider_model_id/display_name/model_type/输入输出模态/operations/api_surfaces/capabilities），已存在行的 `enabled`/`confidence`/`priority`/`source` **不被触碰**；但 `capabilities` 整体重写会丢弃已有 `verified_operations` 与用户附加键，`display_name`/`operations` 的手动修改被回退（对比 `provider_catalog.py:631` 对 `MANUAL` 来源的保护）。改为一次性种子 + 字段级合并尊重用户修改 |
| P5 | `apps/api/app/services/provider_presets.py:63-68` | vertex-ai 预设 `base_url="vertex://google-cloud"` 伪 URL | [技术差异] | 绕过 HTTP(S) base_url 校验（该校验在 `provider_catalog.py:53-62` 且只作用于用户输入路径）。保留但契约上把"无 HTTP 端点的原生连接"显式化（endpoint 模板为空） |
| P6 | `apps/api/app/services/provider_registry.py`（不存在） | 预设表 `PRESETS`（`provider_presets.py:61-251`）中 vertex-ai 与 gemini-api 是仅有的两个原生协议预设 | [中性] | 预设结构本身协议中立；问题只在 P1-P4 的特判路径 |
| P7 | `apps/api/app/services/model_registry.py:25-54` | `build_registry` 硬编码 3 个 `provider="vertex-ai"` 能力（display_name "Gemini 3.5 Flash"/"Nano Banana 2/Pro"） | [偏置] | 代码级模型注册表与数据库目录并存且以代码为准（`model_router.py:145-147` 先查 registry 再回退）。退役为 legacy_alias 兼容数据，能力以 `AIModel.capabilities` 为准 |
| P8 | `apps/api/app/services/provider_catalog.py:502-506` | `discover_models` 对 `VERTEX_NATIVE` 不做发现，直接返回该连接现有模型 | [偏置] | 兼容协议可从上游同步模型（`provider_catalog.py:507-585`），Vertex 连接在 UI 上"同步模型"按钮可用但只返回预设 3 个（`provider-management.tsx:161` 按钮未按协议禁用）。统一：按连接能力声明是否支持发现；Vertex 真实发现能力为**未验证边界**（见第 7 节） |
| P9 | `apps/api/app/services/provider_catalog.py:136-148` | 供应商列表 `configured = native_configured or any(key.enabled...)`，其中 `native_configured` 特判 `VERTEX_NATIVE + settings.vertex_configured` | [偏置] | 第二份"已配置"判定（与 C3 重复）。统一为连接级 `credential_ready` 派生字段 |
| P10 | `apps/api/app/services/model_availability.py:30-32` | 可用性函数中 `VERTEX_NATIVE` 分支：不需要 Key 即可用 | [偏置→契约] | 判定方向正确（账号型连接不需要 Key），但实现按协议字符串分支。改为按 `credential_source` 字段判定 |
| P11 | `apps/api/app/model_adapters/google.py:16,36-41` | GOOGLE_NATIVE 适配器复用 `classify_vertex_failure`，错误文案却是 "Gemini API …"（`google.py:77,82,109,114,174`） | [技术差异] | 见 C5；分类器归属重构 |
| P12 | `apps/api/app/model_adapters/fake_acceptance.py:72` | 验收假适配器默认 `model_id="fake-acceptance-gemini-flash"` | [兼容] | 测试夹具命名，无产品语义；可顺手中性化但非必需 |

### 2.3 健康与验证双轨

| # | 位置 | 当前行为 | 分类 | 说明与建议 |
| --- | --- | --- | --- | --- |
| H1 | `apps/api/app/models.py:1024-1041` | `ProviderHealth` 表：`provider` 唯一单行（`models.py:1027`），字段含 Vertex 专属语义 `configured`/`credential_file_present`/`text_model_access`/`image_model_access`（`models.py:1028-1029,1040-1041`） | [偏置] | 每供应商一行的旧设计与连接级健康字段（`models.py:913-922`，已含 health_state/last_checked_at/last_success_at/latency_ms/error_code/message）并存。`text_model_access`/`image_model_access` 应由 `ModelProbe`/`verified_operations` 取代 |
| H2 | `apps/api/app/services/vertex_health.py:20` | `PROVIDER = "vertex-ai"` 常量；`get_or_create_health`（`vertex_health.py:38-70`）维护单行状态 | [偏置] | 随 H1 收敛为连接级健康 |
| H3 | `apps/api/app/services/vertex_health.py:97-195` | `verify_vertex`：三级验证（CREDENTIALS/TEXT_MODEL/IMAGE_MODEL），写 access 状态，最后 `sync_vertex_connection_health` 回写连接（`vertex_health.py:190-192`） | [偏置→契约] | 验证流程本身（凭据冒烟 / 低 token 文本冒烟 / 1K 图片冒烟）是良好的通用设计。抽象为统一 `ConnectionVerifier`，级别改为 `CREDENTIALS` / `MODEL_SMOKE(catalog_model_id)` |
| H4 | `apps/api/app/api/routes/settings.py:44-55` | `GET/POST /settings/vertex/status|verify` 专属端点 | [偏置] | 统一为 `/providers/connections/{id}/health` 与 `/providers/connections/{id}/verify` |
| H5 | `apps/api/app/api/routes/models.py:74-87` | `GET/POST /models/vertex/status|verify` 兼容别名（注释称"保留一个版本"），**前端与测试均未调用**（`api.ts:808-810` 用 `/settings/vertex/*`；grep 测试无 `/models/vertex` 命中） | [偏置] | 死代码级别的兼容层；Phase D 直接删除或随 H4 统一 |
| H6 | `apps/api/app/schemas.py:168-175` | `VertexStatusRead` 旧 schema，全库无引用（仅定义） | [偏置] | 死代码，删除 |
| H7 | `apps/api/app/settings_schemas.py:35-58` | `VertexHealthRead` / `VertexVerifyRequest`；`image_model_alias` 为硬编码 Literal `["image.nano_banana_2","image.nano_banana_pro"]`（`settings_schemas.py:58`），而前端类型已放宽为 `string`（`api.ts:6`） | [偏置] | 验证请求统一为 `catalog_model_id` 引用，别名经 legacy 层解析 |
| H8 | `apps/api/app/services/provider_presets.py:349-381` | `sync_vertex_connection_health`：legacy `ProviderHealth` → 连接健康字段单向同步 | [兼容] | 双轨收敛期的桥；在 H1 迁移完成后删除 |
| H9 | `apps/api/app/api/routes/settings.py:76,94-117` | 诊断页 `oauth`/`text-model` 检查直接读 Vertex 单行健康并硬编码文案（`settings.py:108` "Gemini 3.5 Flash 最近一次验证成功"） | [偏置] | 诊断检查改为遍历连接健康，按实际模型 display_name 生成文案 |
| H10 | `apps/api/app/services/worker_handlers/story_parse.py:205` | 错误文案硬编码"原文第 N 段被 Vertex 拒绝"，与实际路由到的供应商无关 | [偏置] | 文案去掉供应商名，供应商事实已由模型调用账本记录 |

### 2.4 任务创建、路由与默认值

| # | 位置 | 当前行为 | 分类 | 说明与建议 |
| --- | --- | --- | --- | --- |
| R1 | `apps/api/app/api/routes/projects.py:60` | 新项目 `image_model_alias=values["last_image_model_alias"] or "image.nano_banana_2"` | [偏置] | 默认值硬编码 Vertex 别名；且与产品规则"图片模型按任务显式选择"（`page_readiness` readiness、`projects/[id]/settings/page.tsx:100`）矛盾。默认应为"空 + 每任务显式"，历史字段只读保留 |
| R2 | `apps/api/app/models.py:82,85` | `Project.text_model_alias` 默认 `"text.fast"`、`image_model_alias` 默认 `"image.nano_banana_2"`（DB 列默认） | [偏置] | 同 R1；列默认值改为 NULL，历史行不动 |
| R3 | `apps/api/app/schemas.py:29,695` | `ProjectCreate.text_model_alias` 默认 `"text.fast"`；`PageReadinessProvider.image_model_alias` 默认 `"image.nano_banana_2"`（后者实际已被 `page_readiness.py:320` 以 `"explicit"` 覆盖） | [偏置] | schema 默认值清理；readiness provider 块字段重命名为目录口径（见 T6） |
| R4 | `apps/api/app/api/routes/asset_generation.py:461,494`、`apps/api/app/api/routes/sources.py:199`、`apps/api/app/api/routes/workflow/inspection.py:53` | 风格分析 / 剧情解析 / 质量检查任务创建硬编码 `model_alias="text.fast"` | [偏置] | 改为 `auto`（走 `resolve_model` 自动路由，`model_router.py:65-121`）或项目 `default_text_model_id` |
| R5 | `apps/api/app/services/workflow_engine/catalog.py:239-241,253` | 默认工作流 parse/adapt/storyboard/inspect 节点 `model_alias="text.fast"` | [偏置] | 默认工作流模板应使用 `auto` 或留空由路由策略决定 |
| R6 | `apps/api/app/services/workflow_engine/reconciliation.py:116` | 节点模型解析回退 `or "text.fast"` | [偏置] | 回退改为"无别名 → auto 路由" |
| R7 | `apps/api/app/services/worker_handlers/provider.py:282-284` | `_text_model_reference`：`job.model_alias == "text.fast"` 时作为哨兵回退到项目默认 | [偏置→兼容] | "text.fast = 默认"的魔法语义；过渡期保留解析，Phase C 后 legacy 解析层只读 |
| R8 | `apps/api/app/model_adapters/vertex.py:65-71` | Vertex 文本适配器支持 `thinking_budget` 元数据；兼容/Google 适配器无此参数（`google.py:57-115`、`compatible.py` 无对应处理） | [技术差异] | `metadata` 透传本就是能力协商；在 `ModelCapability`/capabilities 中声明 `supported_parameters`（兼容协议已有该字段：`provider_catalog.py:700`），调用方按能力传参 |
| R9 | `apps/api/app/services/model_router.py:144-152` | `bind_adapter` 按 `connection.protocol == "VERTEX_NATIVE"` 选择 Vertex 适配器；`model_router.py:145-147` 能力解析先查 `build_registry(settings)` 硬编码表，命中失败才回退目录字段 `_legacy_capability`（`model_router.py:300-311`） | [偏置→契约] | 分派本身是适配器层的合法职责；**registry 优先于目录**的顺序必须反转为"目录字段为准、legacy registry 仅兜底"，最终删除兜底 |
| R10 | `apps/api/app/services/model_router.py:236-237` | `_require_available_credentials` 对 `VERTEX_NATIVE` 直接放行（不查 Key） | [偏置→契约] | 同 P10，改 `credential_source` 判定；账号型连接的"可用"条件= 凭据就绪（C6 检查保留在适配器层兜底） |
| R11 | `apps/api/app/services/model_router.py:221-222`、`apps/api/app/services/provider_catalog.py:410-416,684-692,716` | Anthropic 协议连接禁止图片模型/操作 | [技术差异] | 真实协议能力差异（Anthropic 无图片生成端点），保留；建议以协议能力表声明而非散落 if |
| R12 | `apps/api/app/api/routes/workflow/generation.py:79-83`、`apps/api/app/api/routes/asset_generation.py:677` | 图片任务禁止 `auto`，必须显式选择模型 | [中性] | 产品一致性规则，协议无关，保留 |
| R13 | `apps/api/app/models.py:420`（`GenerationRecord.provider`；写入方 `worker_handlers/page_generate.py:407-411`、`worker_handlers/asset_generate.py:312` 均显式传值）；`ModelCallAttempt.provider`（`models.py:489`）无默认、始终显式（写入方 `model_call_audit.py:57-70`） | `GenerationRecord.provider` 列默认 `"vertex-ai"` | [偏置] | 两张账本表的 provider 均由实际绑定显式写入（`worker_handlers/provider.py:119-121` 组装 meta），`GenerationRecord` 的列默认值是死重；删除默认值。注意：仓库无 `ModelCallAudit` 类，持久化对象为 `GenerationRecord`（`models.py:413-439`）与 `ModelCallAttempt`（`models.py:442-504`） |
| R14 | `apps/api/app/models.py:964-966`（单值、`unique=True`）、`apps/api/app/services/model_router.py:46-50` | `AIModel.legacy_alias` 列 + `get_catalog_model` 主键/单别名精确匹配 | [兼容] | 解析通道保留并前置规范化映射（4.3）；只清默认值与硬编码写入；不做多别名 schema 扩展（理由见 4.3 说明） |

### 2.5 Dashboard 与 readiness

| # | 位置 | 当前行为 | 分类 | 说明与建议 |
| --- | --- | --- | --- | --- |
| D1 | `apps/api/app/api/routes/projects.py:90-96` | `_ai_overview` 的 `configured_connection_count`：`has_enabled_key or (protocol == "VERTEX_NATIVE" and settings.vertex_configured)` | [偏置] | 与 P9 同源的第二份判定。复用 `model_availability`/连接级 `credential_ready` |
| D2 | `apps/api/app/services/page_readiness.py:67-98,304-323` | readiness 的 provider 块由目录可用性计数驱动，`image_model_alias="explicit"`（`page_readiness.py:320`） | [中性] | 已平权；仅 `PageReadinessProvider` 字段名（`schemas.py:690-697`）残留 Vertex access 语义，重命名属 Phase D 文档级清理 |
| D3 | `apps/web/app/page.tsx:158-161` | 首页独立 `useQuery ["vertex-status"]` + `verifyVertex` mutation | [偏置] | 首页对 Vertex 的独立状态请求；改用 dashboard `ai_overview` 内的连接健康聚合 |
| D4 | `apps/web/app/page.tsx:24-34,209-220` | "VERTEX AI / LEGACY" 专属卡片：健康灯、区域、**硬编码 "Gemini 3.5 Flash"**（`page.tsx:216`）、验证按钮 | [偏置] | 改为通用"连接健康"侧卡，列出各连接 health_state 与模型名（数据来自目录） |
| D5 | `apps/web/app/settings/page.tsx:31,46,58-59,81-90` | 设置页 VERTEX AI / PROVIDER 专属卡片：独立 query、四个验证按钮（凭据/文本/两个图片别名）、硬编码 "验证 Gemini 3.5 Flash"/"验证 Nano Banana 2/Pro" 文案（`settings/page.tsx:88-90`） | [偏置] | 验证入口改为每个连接统一"验证凭据 / 模型冒烟"（图片冒烟保留付费确认，与 `provider-management.tsx:176` 一致） |
| D6 | `apps/web/lib/api.ts:6,51-68,808-810` | `VertexStatus` 类型 + `api.vertexStatus` / `api.verifyVertex` 客户端 | [偏置] | 随 H4 替换为连接健康 API 类型 |
| D7 | `apps/web/components/provider-management.tsx:151-158` | `connection.protocol !== "VERTEX_NATIVE"` 时才渲染密钥表单/密钥列表 | [偏置→契约] | 判定依据改为 `credential_source !== "CONNECTION_KEY"`；账号型连接显示"凭据由服务端环境管理"而非空白 |
| D8 | `apps/web/app/projects/[id]/settings/page.tsx:105-107` | 文本路由下拉：`"text.fast"` 作为"兼容默认 · Gemini 3.5 Flash"硬编码选项，并过滤 `logical_alias !== "text.fast"` | [偏置] | 选项改为 `auto` + 目录模型列表；显示名取 `display_name` 字段 |
| D9 | `apps/web/app/help/page.tsx:22` | 帮助页故障排查区块标题硬编码 "VERTEX AI / 故障排查" | [偏置] | 改为"供应商连接 / 故障排查"，内容通用化 |
| D10 | `apps/api/app/api/routes/settings.py:119-127` | 诊断 checks 固定包含 `oauth`（Google OAuth）与 `text-model` 项 | [偏置] | 与 H9 合并处理：检查项按已配置连接动态生成 |

### 2.6 历史数据兼容（保留清单）

| # | 位置 | 当前行为 | 分类 | 说明 |
| --- | --- | --- | --- | --- |
| L1 | `apps/api/app/api/routes/asset_generation.py:86`、`apps/api/app/api/routes/characters.py:149`、`apps/api/app/api/routes/uploads.py:285` | 资产来源过滤集合含 `"VERTEX_GENERATED"`；新写入统一为 `"AI_GENERATED"`（`worker_handlers/asset_generate.py:78`、`worker_handlers/page_generate.py:230`） | [兼容] | 旧库历史资产标记，只读保留 |
| L2 | `apps/api/app/models.py:964-966`（`AIModel.legacy_alias`：单值、`unique=True`）、`apps/api/app/services/model_router.py:46-50`（仅"主键精确匹配 → 单个 legacy_alias 精确匹配"一次）、`apps/api/migrations/versions/20260714_01_revised_mvp_workflow.py:29-34`（一次性把 `projects.image_model_alias` 全表改写为 nano_banana 别名）、`docs/data-model.md:143` | 历史别名兼容现状：每模型只有一个 `legacy_alias`；`image.fast`/`image.quality` 不在任何 `legacy_alias` 中，且 `projects` 表已被 20260714_01 改写，旧值残留只可能存在于该迁移未触及的存储（`generation_jobs.model_alias`、同代候选行等） | [兼容] | **Issue 验收红线：不得破坏历史候选中的真实 provider/model ID**。统一契约在解析前置一个规范化映射（见 4.3），不改写任何存储值；所有别名仅作解析输入，禁止作为新默认值（对应 R1-R7 的清理与 M5/M7 不变性断言） |
| L3 | `apps/api/app/worker_tasks.py:48`、`apps/api/app/services/worker_handlers/provider.py:42-59` | `_adapter` 测试缝（legacy 别名 → 适配器注入） | [兼容] | 测试基建；平权后由假适配器按目录 ID 注入，逐步退役 |

### 2.7 文档约定现状

| # | 位置 | 内容 | 分类 |
| --- | --- | --- | --- |
| OC1 | `docs/architecture.md:20` | 架构图模型层节点为 "Vertex / Gemini 原生"（未列 OpenAI/Anthropic 之外的第三方原生位） | [偏置] |
| OC2 | `docs/architecture.md:103-113` | 第 7 节："兼容协议只允许 OpenAI 与 Anthropic；Vertex/Gemini 原生适配用于兼容已有部署"；逻辑别名→默认 Vertex 模型 ID 对照表；"旧逻辑别名仍可用，但新任务会解析到目录模型 ID" | [兼容+偏置] |
| OC3 | `docs/provider-platform.md:5` | "Vertex AI 和 Gemini API 是已有工作流的内置原生连接，不属于用户可新增协议" | [兼容+偏置] |
| OC4 | `docs/provider-platform.md:23,29` | 自动路由仅用 VERIFIED 模型；显式选择可用"兼容旧别名" | [中性] |
| OC5 | `docs/data-model.md:112-128` | 供应商能力示例 JSON 以 `vertex-ai`/`image.nano_banana_2` 为例；"UI 依据 API 能力渲染，不在客户端自造模型优先级" | [中性] |
| OC6 | `docs/roadmap.md:21,177-182,363-365` | 既有决策：Dashboard 与 `/models` 共用可用性条件、"原生 Vertex 与 configured/healthy 统计语义保留"、首页仅 dashboard + vertex status 两请求 | [偏置]（约束来源） |

### 2.8 受影响测试现状

| # | 位置 | 固化的行为 |
| --- | --- | --- |
| T1 | `tests/test_dashboard_ai_overview.py:240-258,260-277` | 原生 Vertex 无 Key / 凭据只读时的 configured 统计特判（D1） |
| T2 | `tests/test_dashboard_ai_overview.py:311-316` | 首页源码必须包含 `queryKey: ["vertex-status"]` —— **测试固化前端 Vertex 专属请求**（D3） |
| T3 | `tests/test_multi_provider_platform.py:98,114,122-143,634-635` | vertex 预设连接继承 legacy 健康（H8）；VERTEX_NATIVE 连接夹具 |
| T4 | `tests/test_platform_contracts.py:37-41` | `VertexVerifyRequest.image_model_alias` Literal 拒绝未知别名（H7） |
| T5 | `tests/test_vertex_health_and_settings.py:47-96,220-244,264-265` | 失败分类、重试、状态端点不泄露凭据路径、`ProviderHealth.provider=="vertex-ai"` 单行（C5/H1/H3） |
| T6 | `tests/test_vertex_adapter.py:42-47` | Vertex 适配器客户端生命周期（含 `build_registry(settings)["text.fast"]` 依赖，R9） |
| T7 | `apps/web/components/project-workspace/generate-section.test.tsx:230,277-278`（夹具 `provider:"vertex-ai"`、`protocol:"VERTEX_NATIVE"`）；同文件 :69,:143,:216-232,:267,:280 以 `image.nano_banana_2` 别名作生成夹具 | 组件夹具依赖 Vertex 目录与别名（R1/D8 清理后需同步） |
| T8 | `tests/e2e/platform-v2.spec.ts` | 未引用 vertex 与别名（使用假模型通道），E2E 不受直接影响 |

## 3. 分类结论：保留的适配器差异 vs 必须删除的产品偏置

### 3.1 必须保留的原生适配器差异（技术事实，不是产品优先）

1. **凭据形态**：环境级服务账号（OAuth 刷新、令牌缓存、认证失败重试，`vertex_credentials.py:86-225`）vs 连接级 API Key（加密存储、轮换、冷却，`provider_catalog.py:291-337`、`credential_crypto`）。→ 抽象为 `credential_source`（第 4 节），两条通道都保留。
2. **结构化输出模式**：原生 `response_schema`（严格模式，`vertex.py:72-79`、`google.py:67-79`）vs 兼容协议 `JSON_MODE`/`response_format`（`compatible.py:518-526,569-573`）。→ 已由 `capabilities.structured_output_mode` 声明（`provider_presets.py:405`、`provider_catalog.py:699`），保持目录声明驱动。
3. **错误形态映射**：不同上游错误对象需要不同分类器，但映射到**同一套错误码**（AUTHENTICATION/PERMISSION/MODEL_NOT_FOUND/RATE_LIMIT/TIMEOUT/CONTENT_POLICY/CONFIGURATION/INVALID_OUTPUT/UPSTREAM，`vertex_credentials.py:25-77`、`provider_catalog.py:721-739`）。→ 每协议一分类器 + 共享码集。
4. **能力边界**：Anthropic 无图片生成（R11）；原生文本适配器独有 `thinking_budget` 透传（R8）；图片模型参考图上限由模型能力声明（Vertex 14 张 vs 兼容协议发现默认 1 张，`provider_presets.py:419`、`provider_catalog.py:705`）。→ 全部收进 capabilities 协商，调用前校验（`worker_handlers/provider.py:287-293` 已有统一容量校验）。
5. **传输通道**：Vertex 走 `google-genai` SDK 客户端而非 HTTP 端点模板，因此不经操作员代理/loopback 校验路径（`proxy_url_for_connection` 对伪 URL 天然不生效，`provider_presets.py:272-288`）。→ 保留；契约中标注该连接类型无 HTTP 面。

### 3.2 必须删除的产品偏置（按严重度排序）

1. **预设注入未验证的 VERIFIED 模型，且每次调用重写 definition 内字段——`capabilities` 整体重写会丢弃既有 `verified_operations`**（P4，附 P3 的调用频率）。
2. **Vertex 连接自动启用 + 健康初始态特判**（P1、P2）。
3. **双健康体系与专属端点/前端卡片/独立首页请求**（H1-H9、D3-D6、D9-D10；含固化偏置的测试 T2）。
4. **默认模型别名指向 Vertex**（R1-R7、D8；含 DB 列默认 R2、schema 默认 R3）。
5. **第二份"已配置"判定**（C3/P9/D1 → 统一 `credential_ready`）。
6. **目录能力特权**：不可发现但预设固定三模型（P8）+ 代码 registry 优先于目录（R9/P7）。
7. **协议字符串驱动的 UI 分支与硬编码文案**（D7、D5、D4、H9、H10）。
8. **验证请求硬编码别名 Literal**（H7）。
9. **审计列默认值 `vertex-ai`**（R13）。

## 4. 统一契约草案

### 4.1 领域对象

```text
Provider
  id, preset_key?, name, category(OFFICIAL|GATEWAY|CUSTOM), built_in,
  enabled, risk_label, documentation_url          # 现状已达标（models.py:878-891）

Connection
  id, provider_id, name, protocol,                # protocol 保留为传输协议枚举
  base_url,                                       # 原生连接允许非 HTTP 伪 URL（P5 显式化）
  endpoint_templates, extra_headers, balance_config, nonsecret_config,
  use_responses_api,
  credential_source,                              # 新增（见 4.2）
  enabled, health_state, last_checked_at, last_success_at, latency_ms,
  error_code, message                             # 健康字段唯一事实来源（models.py:913-922）

Credential
  credential_source = CONNECTION_KEY | ENV_SERVICE_ACCOUNT
  CONNECTION_KEY:        provider_keys 行（加密、轮换、冷却，现状保留）
  ENV_SERVICE_ACCOUNT:   settings 三要素 + 进程级缓存管理器（C4，现状保留）
  派生只读字段 credential_ready: bool               # 取代 vertex_configured / native_configured 的统一判定

Model (AIModel)
  catalog_id, connection_id, provider_model_id, display_name,
  legacy_alias?,                                  # 单值、unique=True；多别名经 4.3 规范化映射（L2）
  model_type, input_modalities, output_modalities, operations,
  capabilities, confidence, enabled, priority, source   # 现状字段已达标（models.py:954-977）

Capability (AIModel.capabilities 约定键)
  structured_output_mode: STRICT_SCHEMA | JSON_MODE
  resolutions, preview_resolutions, max_reference_images
  supported_parameters[]                            # 兼容协议已有（provider_catalog.py:700），原生协议补齐（R8）
  verified_operations[]                             # 能力测试产物（providers.py:359-363）

Health (统一在 Connection 上)
  GET  /api/v1/providers/connections/{id}/health      → { health_state, last_checked_at,
                                                         last_success_at, latency_ms, error_code, message }
  POST /api/v1/providers/connections/{id}/verify      → 请求 { level: CREDENTIALS | MODEL_SMOKE,
                                                         catalog_model_id? }
  # 语义映射：CREDENTIALS=只刷新/列举凭据不调用模型；MODEL_SMOKE=低 token 文本或 1K 图片冒烟
  # （付费确认由前端按 model_type 保留，provider-management.tsx:176 的既有交互）
  # text_model_access / image_model_access 由 ModelProbe + verified_operations 取代（H1）
```

### 4.2 判定规则统一（取代全部特判）

| 规则 | 统一定义 | 取代 |
| --- | --- | --- |
| 连接已配置 `configured` | `credential_ready`：CONNECTION_KEY → 存在启用且未冷却 Key；ENV_SERVICE_ACCOUNT → 环境三要素齐备且文件存在 | C3、P9、D1 |
| 连接启用 `enabled` | `enabled` 是用户/初始化状态，不由当前 `credential_ready` 持续重算。旧连接原值不动；新建 ENV_SERVICE_ACCOUNT 连接可带 `nonsecret_config.auto_enable_pending=true`，只在该标记仍为真且凭据首次就绪时 False→True 并清除标记；任何人工启用或停用都先清除标记。凭据暂时不可见永不触发 True→False。Key 型保持"用户录入 Key 即启用"（`provider_catalog.py:325-326`），不使用后台锁存 | P1、P3 |
| 初始健康态 | 未配置 → `UNCONFIGURED`；已配置未验证 → `DEGRADED`（对一切协议一致） | P2、H2 |
| 模型可用 | `model.enabled and connection.enabled and provider.enabled`，Key 型另需可用 Key —— **现状公式不变**（`model_availability.py:28-32`），分支依据改 `credential_source` | P10、R10 |
| 自动路由资格 | `confidence == "VERIFIED"` 且 `connection.health_state == "HEALTHY"`（`model_router.py:223-226`）——对一切来源一致，无预设豁免 | P4 |
| 模型发现 | `connection 支持发现 = 协议能力表声明`；不支持发现的原生连接 UI 显示"手动建模/预设种子"而非可点击但无效的"同步模型" | P8 |
| 默认模型 | 新建项目/任务/工作流不写默认别名；文本走 `auto`（RoutingPolicy + 已验证目录）或项目 `default_text_model_id`（目录 ID）；图片按任务显式选择（R12 不变） | R1-R7、D8 |
| 验证 | 统一 `ConnectionVerifier`：凭据冒烟（无模型调用）+ 模型冒烟（计费调用）；结果写连接健康 + ModelProbe | H3、H7、D5 |

### 4.3 兼容映射（Phase 完成后仍必须成立）

| 输入 | 解析链 | 依据 |
| --- | --- | --- |
| `image.fast` / `image.quality` | 先经解析前规范化映射 `ALIAS_ALIASES = {"image.fast": "image.nano_banana_2", "image.quality": "image.nano_banana_pro"}`（legacy 解析层常量，封闭集合）映射为现行别名，再走 `legacy_alias` 精确匹配；**映射只发生在解析时，任何存储值不重写** | L2、`20260714_01_revised_mvp_workflow.py:29-34`（projects 表已一次性改写，本映射覆盖其未触及的残留行） |
| `text.fast` / `image.nano_banana_2` / `image.nano_banana_pro` | 直接经 `AIModel.legacy_alias` 精确匹配（单值、`unique=True` 列） | L2、`models.py:964-966` |
| 历史候选/任务/账本中的真实 provider/model ID | 只读不变；`GenerationRecord`/`ModelCallAttempt` 历史行不改写（M9 不变性断言） | Issue 验收红线 |
| `asset.source == "VERTEX_GENERATED"` | 读取过滤集合继续包含；新写入仍为 `AI_GENERATED` | L1 |
| `/settings/vertex/status\|verify` | 过渡期（Phase B-D）保留并标记弃用，内部转发统一实现；Phase E 删除 `/models/vertex/*`（当前无人调用，H5）与死 schema（H6） | H4-H6 |

别名载体选型说明：选择"解析前规范化映射"而非多别名表/数组结构。理由：(a) 多别名结构需要一次 schema 迁移，而别名集合封闭且已知（5 个），映射常量即可覆盖；(b) 仓库既有先例就是规范化而非保留多别名（20260714_01 对 projects 表的一次性改写）；(c) 映射位于 legacy 解析层，与红线"不改写存储值"天然兼容。若后续出现第 6 个历史别名，按同一常量追加。

## 5. 分阶段改造顺序、兼容性风险与回滚点

原则：每阶段一个独立 PR，可独立合并与回滚；阶段内不夹带无关重构（遵守 AGENTS.md 拆分规则）。

### Phase A：契约地基（无行为变化）

- 内容：新增 `credential_source` 派生层（先在服务层函数，不动 schema）；错误分类器按协议拆分（`classify_vertex_failure` → `native_error.py` 共享码集，`google.py` 改引用）；落第 1.2 节 grep 门禁（先记录全量基线命中，允许清单即第 2 节 [偏置] 项）；`ModelCapability.supported_parameters` 补齐原生适配器。
- 风险：低。错误分类器拆分需保持 `google.py` 现有文案与码集不变（T5 覆盖）。
- 回滚点：纯重构 + re-export， revert 即可。

### Phase B：健康与验证统一（后端）

- 内容：统一 verify 服务（CREDENTIALS/MODEL_SMOKE）；新端点 `/providers/connections/{id}/health|verify`；`/settings/vertex/*` 转发统一实现并标记弃用；删除 `/models/vertex/*` 与 `VertexStatusRead`（H5、H6）；诊断检查项动态化（H9、D10）；预设连接初始健康态语义统一为"未配置→`UNCONFIGURED`、已配置未验证→`DEGRADED`"（P2）；`_ensure_vertex_models` 停止每次调用（P3）改为启动时一次性种子且不再对已存在行 `setattr`（P4 的重写范围见该行精确表述）。
- 风险：中。`ProviderHealth` 单行表仍是 Vertex 健康的事实来源（H1 暂不迁移表结构，保留 `sync_vertex_connection_health` 桥，H8）；`configured` 语义变化影响 T1/T3 断言。
- 回滚点：新端点独立路由文件；弃用旧端点保持响应 schema 不变（`VertexHealthRead` 字段），前端未切换前可整体 revert。
- 不做：不改 `provider_keys`/连接表 schema（无 Alembic 迁移即无回滚负担）。

### Phase C：默认值与路由平权（含一次仅可空性的零数据迁移）

- 内容：`projects.text_model_alias`/`image_model_alias` 改为可空并删除 ORM/Pydantic 默认值（两列现为 `nullable=False`，`migrations/versions/949d8856e6a4_initial_core_schema.py:64-65`；ORM default 在 `models.py:82,85`）——这是本阶段唯一的 Alembic 迁移，**只改可空性/默认值，零数据重写**；新项目/任务/工作流默认值清理（R1-R6）；`_text_model_reference` 的 text.fast 哨兵语义移除，legacy 解析改为"规范化映射 → legacy_alias"只读链（R7、L2、4.3）；`GenerationRecord.provider` 默认值删除（R13）；`build_registry` 降级为 legacy 兜底且目录字段优先（R9/P7）；P10/R10 改 `credential_source` 判定。
- 旧安装 grandfather 规则（不因升级使 Vertex-only 安装退化）：
  - 迁移不读外部凭据状态，**不做任何 `enabled`/`confidence` 回填**：既有数据行的 `enabled`、`confidence`、`verified_operations`、别名值一律原样保留。Vertex-only 现网安装升级后连接仍启用、预设模型仍 `VERIFIED`，自动路由候选不减。
  - `_ensure_vertex_models` 自 Phase B 起停止逐次调用、停止对已存在行 `setattr`；预设种子只在**空库首次初始化**执行。
  - 既有连接不注入 `auto_enable_pending`，因此 `enabled=True` 的 Vertex-only 安装即使升级/启动时凭据文件暂时不可见也保持启用；既有 `enabled=False` 同样保持停用，不能猜测它是"尚未配置"还是"人工停用"。这避免了在没有持久化来源信息时把人工停用误判为待自动启用。
  - 仅**新建** ENV_SERVICE_ACCOUNT 连接使用一次性状态机：创建时若凭据已就绪则直接 `enabled=True` 且不留标记；若未就绪则 `enabled=False` 并在既有 `nonsecret_config` 写入 `auto_enable_pending=true`。后续同步只有在该标记为真且凭据首次就绪时才置 True，并在同一事务清除标记；任何人工 enable/disable 都清除标记。其后凭据暂时缺失只改变 `credential_ready`/健康态，绝不自动 True→False。Key 型继续由显式录入 Key 的用户动作启用，不进入此状态机。
  - `sync_vertex_connection_health`（`provider_presets.py:364-367`）不得再把 `enabled` 赋为瞬时 `settings.vertex_configured`；同步逻辑只能执行上一条有持久化标记保护的一次性转换。这样既覆盖"凭据文件暂时不可见"窗口，也保留人工停用语义。
  - Alembic 迁移中禁止 import `Settings`/读取凭据文件（实现 Issue 的禁止改动条款 + M7 断言）。
- 新安装初始化规则（与旧安装不同，显式声明）：空库上 Vertex 连接按上一条 `auto_enable_pending` 状态机创建；预设模型以 `confidence="DECLARED"` 种子创建，不享有 `VERIFIED` 豁免；经 Phase B 统一 `MODEL_SMOKE` 验证后由验证器按 `providers.py:359-368` 同款语义写入 `verified_operations`/`confidence`，进入自动路由。仅用 Vertex 的新安装需在初始化引导中完成一次模型冒烟验证（产品交互变化，实现 Issue 中向 lead 确认文案）。
- 风险：中。(a) 历史项目默认别名行为变化——R7 兼容链 + M5/M7 不变性断言覆盖；(b) `nullable` 迁移在 SQLite 走 batch_alter 重建表——沿用既有迁移测试模式；(c) 新安装多一步验证引导。
- 回滚点：迁移的 down 分支恢复 `nullable=False`，**执行前校验两列无 NULL 行，存在则拒绝降级**（保护历史数据，不重写、不伪造旧值）；应用层每个特判移除独立成 commit，可按 commit 粒度回退。

### Phase D：前端与请求口径统一

- 内容：删除首页/设置页 Vertex 专属 query 与卡片（D3-D6、D9）；项目设置文本路由下拉改目录驱动（D8）；`provider-management` 按 `credential_source` 渲染凭据区（D7）；验证按钮改统一级别（D5）。
- 风险：低-中。首页请求预算变化（移除 vertex-status 后首页 1 个请求，优于 roadmap.md:182 的 2 请求承诺，属收紧不属违约）；`test_dashboard_ai_overview.py:311-316` 源码断言需同步改写（T2）。
- 回滚点：前端独立于后端弃用窗口，任何时刻可 revert 前端 PR。

### Phase E：文档与收尾

- 内容：更新 `architecture.md`（第 7 节重写为协议能力表）、`provider-platform.md`（第 5 行重写）、`data-model.md`（示例改协议中立 + 新增 credential_source）；`story_parse.py:205` 文案去供应商名（H10）；grep 门禁收紧为阻断级（允许清单仅剩适配器层与 legacy 解析层）；`ProviderHealth` 表退役评估（若 Phase B 后无写入方则随本阶段出迁移删表或保留只读）。
- 风险：低。文档变更不触代码路径；`roadmap.md`/`development-progress.md` 由 lead 维护，本阶段不触碰。
- 回滚点：纯文档 + 文案，独立 revert。

## 6. 测试矩阵（应新增或修改）

> 派发约定：下表任一行的实现 Issue 必须按 AGENTS.md 规则 1 补齐基线 SHA、独立分支/worktree、精确范围与禁止改动、验收命令（含 M13 门禁与 `npm run check`）、端口/资源窗口与失败清理规则、未验证边界。涉及真实 PostgreSQL 升降级验收的条目按 L3 处理，SQLite 覆盖不作为其最终验收。

| 编号 | 层 | 场景 | 类型 | 覆盖阶段 |
| --- | --- | --- | --- | --- |
| M1 | 后端 | 跨协议平权等价（全程 fake，无网络）：同一 `ProviderProfile` 下构造 OPENAI + 已录未冷却 Key 与 VERTEX_NATIVE + 注入式凭据就绪两条连接，能力字段及 `VERIFIED` 模型对齐。OPENAI 通过注入 fake HTTP 的 `discover_models` 建目录，账号型协议在能力表声明不支持发现时通过统一手工建模入口建目录（若声明支持发现则同样注入 fake discover）；两者在目录列表、显式 `resolve_model`、auto 路由候选与最终绑定、`/models` 可用性、`model_availability.py:52-72` 计数、`page_readiness._catalog_model_availability`（`page_readiness.py:67-98`）、`/projects/dashboard` `ai_overview`（`projects.py:76-101`）各口径结果一致。另断言全库不存在第二份 `configured` 判定（配合 M13） | 新增 pytest（夹具沿用 `test_dashboard_ai_overview.py`、`test_multi_provider_platform.py:634-635` 风格） | B |
| M2 | 后端 | 预设初始化幂等且不覆盖：修改预设模型 display_name/enabled 后再次 `ensure_provider_presets`，修改保留（失败用例=现状 `provider_presets.py:452-454` 会覆盖） | 新增 pytest | B |
| M3 | 后端 | 预设模型不再无条件 `VERIFIED`：未经验证的预设模型不进入 auto 路由；显式选择仍可用 | 新增 pytest | C |
| M4 | 后端 | 统一验证端点：CREDENTIALS 不产生模型调用（复用 `test_vertex_health_and_settings.py:104` 的 manager 注入法）；MODEL_SMOKE 按 model_type 走文本/图片冒烟并写 ModelProbe | 新增 pytest | B |
| M5 | 后端 | legacy 解析与不变性。①解析：`text.fast`、`image.nano_banana_2`、`image.nano_banana_pro`（legacy_alias 直配）、`image.fast`、`image.quality`（经 4.3 规范化映射后直配）与目录 ID 混合输入全部解析成功且指向同一目录模型；②不变性：夹具含历史行——`generation_jobs.model_alias="image.fast"`、`PageCandidate(model_alias="image.fast", catalog_model_id=<真实历史目录 ID>)`、`AssetCandidate(model_alias="image.quality", catalog_model_id=<真实历史目录 ID>)`（均是 20260714_01 未触及的表）、`projects.image_model_alias="image.nano_banana_2"`（20260714_01:29-34 改写后的值），以及既有 `GenerationRecord`/`ModelCallAttempt` 的真实 provider/model_id/catalog_model_id——断言 Phase C 代码升级与解析过程**不重写任何存储值**，候选仍可按规范化别名或其既存目录 ID 解析；③历史项目 `text_model_alias="text.fast"` 升级后仍可创建文本任务 | 新增 pytest | C |
| M6 | 后端 | 新建项目/任务不写供应商默认别名：`Project.text_model_alias`、`image_model_alias` 为 NULL；文本任务走 auto | 新增 pytest | C |
| M7 | 后端 | Phase C 迁移验收，按 L3 拆为三个独立 Issue 派发：**M7a 迁移设计评审**（lead 审：仅可空性/默认值变更、零数据重写；不向旧连接注入 `auto_enable_pending`；迁移内禁止 import `Settings`/读凭据文件；down 恢复 NOT NULL 前必须查询两列 NULL，存在则以明确错误拒绝 downgrade）；**M7b 实现与 SQLite/服务层测试**（空库与含历史行库——`VERTEX_GENERATED` 资产、`text.fast` 项目、`image.fast` 任务、`PageCandidate(image.fast)`、`AssetCandidate(image.quality)`、既有 `VERIFIED` 预设模型及 vertex 连接——upgrade→downgrade→upgrade 后历史行逐字段不变；另在 upgrade 后新建 NULL 项目，断言 downgrade 被拒绝且数据完整；状态机分别覆盖旧 `enabled=True` 在凭据 false→true 窗口始终为 true、旧人工 `enabled=False` 在凭据为 true 时仍为 false、新连接 pending 在 false→true 时仅启用一次并清标记，随后凭据 false 不反向停用）；**M7c 真实 PostgreSQL 升降级验收**（独立环境重复 M7b 的成功往返与含 NULL 拒绝路径；SQLite 结果不替代；当前 NOT RUN，沿用项目既有真实 PG 边界） | 设计评审 + Alembic/服务层测试 + 独立环境验收 | C |
| M8 | 后端 | 弃用端点兼容：`/settings/vertex/status|verify` 响应 schema 与基线逐字段一致；`/models/vertex/*` 返回 404/410 | 新增 pytest | B |
| M9 | 后端 | 账本不变性，两类持久化对象分别断言（仓库无 `ModelCallAudit` 类）：①`GenerationRecord`（`models.py:413-439`；写入方 `page_generate.py:407-411`、`asset_generate.py:312`）——Key 型连接行 `provider` = 实际 preset_key、`catalog_model_id` = 目录 ID；账号型（VERTEX_NATIVE）行 `provider="vertex-ai"`（该表无 `selected_key_id` 列，不适用该项，断言中注明）；②`ModelCallAttempt`（`models.py:442-504`；写入方 `model_call_audit.py:57-70`）——Key 型行 `provider` = 实际 preset_key、`selected_key_id` 非空；账号型行 `selected_key_id IS NULL`；两类对象的 `provider`/`model_id`/`catalog_model_id` 均来自实际绑定，Phase C 后不被重写 | 新增 pytest（扩展 `test_model_call_audit` 既有覆盖） | C |
| M10 | 前端 | provider-management：账号型连接不渲染密钥表单但显示"服务端环境管理"状态；Key 型连接渲染不变 | 修改 `provider-management.test.tsx` | D |
| M11 | 前端 | 首页：无 Vertex 专属请求；`ai_overview` 三计数在"仅账号型"与"仅 Key 型"夹具下正确（替换 T2 源码断言为行为断言） | 修改 `test_dashboard_ai_overview.py` + 新增组件测试 | D |
| M12 | 前端 | 项目设置文本路由下拉：选项为 auto + 目录列表，display_name 来自 API（不再出现硬编码 "Gemini 3.5 Flash"） | 修改 projects settings 相关测试 | D |
| M13 | 门禁 | 落地 1.2 节内嵌的 `scripts/check-provider-neutrality.ps1` 与**仅含注释的空** `scripts/provider-neutrality-allowlist.txt`；在 Phase A 分支仅一次执行 `-UpdateAllowlist` 生成排序、去重、UTF-8 无 BOM 的初始清单并提交，非空清单再次更新必须返回 2，防止把新偏置洗入基线。Windows PowerShell 5.1 自动测试必须覆盖：正常命中均在清单内=0、移除一个实际命中路径=1 且输出命中、允许清单缺失=2、从仓库外 cwd 以绝对脚本路径运行仍扫描脚本所属仓库并返回 0；CI/`npm run check` 固定调用普通模式。此后每修复一个 [偏置] 项同步删除对应文件级 allowlist 条目 | 新增脚本、清单与脚本级测试（Phase A 代码 PR） | A 落地，E 收紧 |
| M14 | 端到端 | 现有 `tests/e2e/platform-v2.spec.ts` 全绿（假模型通道，不引用别名）+ 组件层别名夹具（T7 所列 generate-section.test.tsx 各行）在 Phase C/D 后继续可解析。执行契约（按 AGENTS.md 规则 8）：运行 `npm run test:e2e`（自建 web/API 服务器，独占端口）；禁止与 Lighthouse/FPS 门禁、浏览器性能作业或真实供应商联调并行占用资源；脚本自建的临时项目/工作流在成功与失败路径都必须清理（对齐 `architecture.md:131` FPS 脚本清理语义），失败需保留现场时必须在 Issue 中登记运行目录、端口与残留资源 | 既有 E2E + 组件测试 | C、D 各跑一轮 |

## 7. 未验证的真实供应商边界（明确标记 NOT RUN / NOT VERIFIED）

1. **未调用任何真实 Vertex/Gemini API**（遵守 Issue 禁止条款；未读取、未复制任何凭据）。OAuth 刷新、配额、限流、内容安全拦截行为仅由现有离线单测与 mock 覆盖（`test_vertex_health_and_settings.py`、`test_vertex_adapter.py`）。
2. **Vertex 原生模型发现能力未验证**：本审计未确认 Vertex AI 是否提供可与 `discover_models` 对齐的列表 API；P8 的统一方案按"协议能力表声明"留了两种结局（支持发现 / 声明不支持）。需要一次真实环境验证后才能定案。
3. **`thinking_budget` 行为差异未对比**：R8 所述仅原生适配器支持该参数，未在真实供应商上对比带/不带该参数的输出与计费差异。
4. **多区域 / 多服务账号**：契约草案按"每连接一个 ENV_SERVICE_ACCOUNT 凭据"设计；同一供应商多区域、多账号并行未设计也未验证。
5. **Phase C 迁移的真实 PostgreSQL 升降级未验证（NOT RUN）**：迁移仅改两列可空性、零数据重写，M7b 覆盖 SQLite 往返；真实 PostgreSQL 上的升降级验收（M7c）需独立环境，SQLite 结果不替代（沿用项目既有"真实 PG/Redis NOT RUN"边界）。
6. **前端改造后的性能门禁**：Lighthouse/FPS 门禁要求本地 Web/API 运行（`architecture.md:131`），本轮未运行；Phase D 需重跑。
7. **`gemini-api`（GOOGLE_NATIVE）链路**：`GoogleRuntime` 适配器存在（`model_router.py:155-166`）但与 Vertex 共享错误分类器（P11）；其真实 API Key 链路同样未在真实供应商上验证。

## 附录 A：协议字符串引用基线（供 grep 门禁初始快照）

基线 `1eb2ae1` 中 `VERTEX_NATIVE` / `vertex-ai` / `vertex_configured` 在产品代码的全部命中即第 2 节清单所列位置；测试文件命中见 2.8。门禁初始允许清单**不采用手抄清单**：Phase A 提交脚本与空清单后，运行 `powershell -ExecutionPolicy Bypass -File scripts\check-provider-neutrality.ps1 -UpdateAllowlist` 以实际命中生成并提交（1.2 节）；此后按阶段收敛至"仅适配器层与 legacy 解析层"。
