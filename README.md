# MangaFlow AI

面向小说作者与漫画创作者的私有 AI 漫画生产工作台。MVP 已接通完整闭环：

`完整原文 → 无损分段 → 剧本任务 → 动态分页 → 单页抽卡 → 人工校对并暂选 → 视觉检查通过 → 单页成品 → 整章导出`

完整需求见 [`plan.md`](plan.md)，实际完成度见 [`docs/development-progress.md`](docs/development-progress.md)。

## 已实现

- Next.js 16 + React 19 中文创作工作台，FastAPI + SQLAlchemy API。
- 粘贴、TXT、Markdown 原作导入；不可变修订、来源区间、无损片段和覆盖率校验。
- 角色主要姓名、绰号、歧义冲突、参考图与服装/风格素材绑定。
- 按原文长度动态分页，每页可选 3–5 格并支持动态错落版式，最多 8 个气泡、中文硬上限 180 字；总页数不设上限。
- 分镜区分实际出镜、画外人物和仅被提及人物，道具独立保存；只有实际出镜人物要求人物与服装参考。
- 页面统一 readiness 检查原文/剧本覆盖、人物与服装参考、彩色风格和实际执行器；供应商与模型能力由统一模型目录在排队前单独校验。
- AI 概念设定图先作为草稿，人工确认后才同时成为人物与服装规范参考；彩色风格必须依次确认色板、测试图并激活。
- 右至左分镜数据，以及原文、Scene、Beat、对白和页面之间的追溯关系。
- 内置 20 余个供应商预设，可接入 OpenAI、Anthropic、Gemini、DeepSeek、OpenRouter、智谱 GLM、火山方舟、OpenCode Zen 等，也可新增自定义兼容供应商。
- 文字模型与图片模型分目录管理；每个候选可显式选模型，也可从已验证模型中自动路由。
- 同一页多批次、多候选、跨模型抽卡；收藏多个、暂选一个，并可随时撤回而不删除候选或生成文件；只有分镜版本已确认且视觉检查通过的暂选版本，才可进入下一页或导出。
- 按“章节 → 页面 → 批次”读取素材库，支持页面、角色补图、服装图、风格测试和修复图批次。
- 持久化任务、DAG、幂等、取消、重试、超时和并发限制；`AUTO` 在开发环境无 Redis 时使用本地执行器，`LOCAL` 强制本地执行，`REDIS` 不可用时任务安全停留在 `WAITING`。
- 多供应商多模态视觉检查、分级视觉修复、人工文字校对，以及通过章节生产门禁后的 PNG、PDF、项目 JSON 和素材清单导出。
- Vertex 与各供应商 API Key 只由 API/Worker 读取；API Key 使用 AES-256-GCM 加密，浏览器只接收末四位提示。

## AI 供应商与模型

兼容连接只暴露两种协议：`OPENAI` 和 `ANTHROPIC`。Vertex AI 与 Gemini API 作为内置原生连接保留。OpenAI 协议连接可分别配置 `/models`、Chat Completions、Responses、图片生成与图片编辑端点；Anthropic 协议使用 Messages 端点。

设置页支持预设供应商、自定义 Base URL、多 API Key 轮换、模型发现、手动模型、文字/视觉/图片能力测试、延迟基准和可选余额查询。名称推断的模型不会自动参与路由；模型必须完成对应能力测试并标记为 `VERIFIED`。第三方网关会显示风险标签，启用前应自行核对隐私、价格和数据保留政策。详细说明见 [`docs/provider-platform.md`](docs/provider-platform.md)。

原有 Vertex 模型仍作为兼容预设存在：

| 逻辑别名 | 模型 | Vertex 模型 ID | 用途 |
| --- | --- | --- | --- |
| `text.fast` | Gemini 3.5 Flash | `gemini-3.5-flash` | 解析、剧本、分页、分镜和检查 |
| `image.nano_banana_2` | Nano Banana 2 | `gemini-3.1-flash-image` | 页面、资产和修复图生成 |
| `image.nano_banana_pro` | Nano Banana Pro | `gemini-3-pro-image-preview` | 页面、资产和修复图生成 |

两个 Vertex 图像模型没有主次关系。统一目录还会展示其他供应商已启用的图片模型；页面生成只显示支持 `image_edit` 的模型，避免把纯文字或纯文生图模型误用于人物参考流程。

## 本地启动（Windows PowerShell）

要求：Node.js 22+、Python 3.12+。Windows 本地开发不强制安装 Redis。

### 已有环境：日常启动

在 PowerShell 中执行：

```powershell
cd "D:\自媒体\漫画工作流"
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

启动脚本会先执行数据库迁移，再同时启动 Next.js 前端和 FastAPI。Redis 不可用时，`AUTO` 队列模式会自动使用受并发限制的本地后台执行器。

打开：

- Web 工作台：`http://localhost:3000`
- API 文档：`http://localhost:8000/api/docs`
- API 健康检查：`http://localhost:8000/api/v1/health`

停止项目时，在启动项目的 PowerShell 窗口按 `Ctrl+C`。如果提示 3000 或 8000 端口已被占用，请先关闭此前启动的开发服务。

### 第一次安装

新电脑、首次克隆仓库，或 `.venv`、`node_modules` 已被删除时，执行：

```powershell
cd "D:\自媒体\漫画工作流"
powershell -ExecutionPolicy Bypass -File .\scripts\setup-codex.ps1
```

脚本会安装前后端依赖、创建 Python 虚拟环境、建立 `storage` / `uploads` 目录、创建 `.env` 并升级数据库。随后编辑 `.env`：

```env
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
GOOGLE_APPLICATION_CREDENTIALS=D:/absolute/path/to/service-account-key.json
REDIS_URL=redis://localhost:6379/0
QUEUE_ENABLED=true
JOB_LEASE_SECONDS=120
MANGAFLOW_PROXY_URL=http://127.0.0.1:7897
MANGAFLOW_CREDENTIAL_MASTER_KEY=replace-with-a-url-safe-base64-32-byte-key
```

配置完成后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

没有配置 Vertex 凭据时，仍可使用其他已配置供应商。开发环境第一次从设置页保存 API Key 时，会在 `storage/.provider-credential-master-key` 自动创建本机主密钥；生产环境必须显式配置 `MANGAFLOW_CREDENTIAL_MASTER_KEY`。接口只保存加密值且不会把密钥退回浏览器。Redis 是可选项：运行设置中的队列模式默认为 `AUTO`，检测不到 Redis 时自动切到本地执行器；选择 `LOCAL` 后不再探测 Redis；只有显式选择 `REDIS` 且 Redis 不可用时，新任务才保持 `WAITING`。

如果浏览器能访问 Google，但 API 显示 `DEGRADED / UPSTREAM`，可将 `MANGAFLOW_PROXY_URL` 设置为 Clash/Mihomo 的 HTTP 或 Mixed 端口。`scripts\start-dev.ps1` 会自动把它转换成 Python 网络库识别的 `HTTP_PROXY` / `HTTPS_PROXY`，并为 `localhost`、`127.0.0.1` 设置直连。不要填写仅支持 SOCKS 的端口。

### 手动启动

不使用启动脚本时，可以手动迁移数据库并启动前端和 API：

```powershell
cd "D:\自媒体\漫画工作流"
.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
npm run dev
```

只有已经启动 Redis 且需要独立 RQ Worker 时才使用 `npm run dev:full`。日常本地使用推荐 `scripts\start-dev.ps1`。

### Codex / Windows 一键环境

在 Codex 的“创建本地环境 → Windows → 设置脚本”中填写：

```powershell
powershell -ExecutionPolicy Bypass -File "$env:CODEX_WORKTREE_PATH\scripts\setup-codex.ps1"
```

环境建立后，一键启动命令为：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

开发模式没有 Redis 也能运行：任务会进入最多 8 线程的本地后台执行器，并继续遵守项目设置的 1–8 路并发上限。正式部署仍建议使用 Redis/RQ。

PostgreSQL 与 Redis 的开发容器可用 `docker compose up -d` 启动；默认数据库仍为 `storage/mangaflow.db`。

## 数据存储与备份

默认部署是本地单用户工作台，采用“SQLite 元数据 + 本地文件目录”的双层存储。图片二进制不会写入数据库；数据库只保存资产 ID、相对 `storage_key`、版本状态和业务关系。

| 数据 | 默认位置 | 说明 |
| --- | --- | --- |
| 项目与业务元数据 | `storage/mangaflow.db` | 项目、章节、原文修订、角色、服装、风格、分镜、候选、任务、工作流、检查与导出记录 |
| AI 生成原图 | `storage/generated/<project_id>/<batch_id>/` | 页面候选、角色/服装设定图、风格测试、修复和升清结果 |
| 生成图缩略图 | `storage/thumbnails/<asset_id>/` | 自动生成的 320px、640px WebP |
| 用户上传原文件 | `uploads/<project_id>/` | 文件名规范化为 UUID，原始扩展名保留 |
| 上传图缩略图 | `uploads/thumbnails/<asset_id>/` | 自动生成的 320px、640px WebP |
| 导出文件 | `storage/exports/<project_id>/<chapter_id>/` | PNG、PDF、项目 JSON 和素材清单 |
| 浏览器界面偏好 | `localStorage` / `sessionStorage` | 侧栏与检查器宽度、滚动位置、风格模式，以及尚未导入服务端的旧版工作流草稿 |

主要项目数据由 API 写入 SQLite，所有应用 SQLite 连接都会启用外键检查和 5 秒 busy timeout。采用或撤回候选只改变数据库关系：撤回不会删除候选、任务、资产或图片文件。素材库普通删除是软删除，生成文件和任务记录继续保留。

上传图和 AI 生成图都可以设置自定义显示名。显示名保存在数据库的 `assets.display_name`，不会改写 `original_name`、磁盘文件名或 `storage_key`，因此下载、追溯和去重关系不受影响。素材库可按 `generation_batches.chapter_id` 筛选章节批次；没有章节归属的角色、服装或风格素材仍归入项目级素材。

设置页的“删除项目”同样采用软删除：必须输入完整项目名确认，成功后项目从首页和工作台列表隐藏，但数据库记录和生成文件不会立即物理删除。该操作用于防止误删，不等同于释放磁盘空间。

清除浏览器缓存不会删除项目、分镜或素材，但会重置界面偏好，并可能移除尚未导入服务端的旧版工作流草稿。工作流正式版本、运行和节点运行记录保存在数据库中。

> [!IMPORTANT]
> `storage/`、`uploads/`、`.env` 和服务账号文件均被 Git 忽略。提交代码、切换分支或合并到 `master` 都不构成数据备份。

完整备份至少应包含：

1. `storage/mangaflow.db`
2. `storage/generated/`
3. `uploads/`
4. 如需保留已导出成品，再包含 `storage/exports/`
5. 单独加密保存 `.env`、`storage/.provider-credential-master-key`（如存在）和 Vertex 凭据；不要把凭据放进普通项目归档

复制数据库文件前应停止 API/Worker 写入，或使用 SQLite 在线备份工具生成一致性快照。迁移脚本产生的 `.db` 备份只保护数据库，不包含生成图和上传素材，因此不能替代完整备份。

如需迁移到另一台机器，应保持上述目录的相对结构；数据库中的 `storage_key` 使用相对路径，恢复文件后重新执行 Alembic 升级即可：

```powershell
.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
```

## 检查

```powershell
npm run check
```

当前质量基线包含 115 个后端测试和 4 个前端测试，并覆盖 Ruff、ESLint、TypeScript、Next.js 生产构建和 Alembic 全新升级/回滚/再升级。真实供应商图片调用不属于默认测试，避免意外费用。文字由用户在采用候选前人工校对；系统不再创建文字自动检查或文字区域修复任务。

## 安全说明

- `.env`、服务账号 JSON、数据库、上传素材和生成结果均被 Git 忽略。
- API 不返回凭据明文、凭据路径、服务账号邮箱、令牌或完整项目唯一标识。
- 自定义供应商默认要求 HTTPS，并在调用前阻止本机、链路本地、元数据和未显式允许的私有网络地址；重定向不会自动跟随。
- 上传限制为 PNG/JPG/WEBP/TXT/Markdown、最大 20 MB，并校验图片文件内容。
- 生成日志只保存脱敏错误和资产 ID，不记录认证头。
- 普通查询不触发模型调用；所有 AI 创建接口返回 `202 + job_id`。
