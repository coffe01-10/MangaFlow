# MangaFlow AI

面向小说作者与漫画创作者的私有 AI 漫画生产工作台。MVP 已接通完整闭环：

`完整原文 → 无损分段 → 剧本任务 → 动态分页 → 单页抽卡 → 收藏/采用 → 下一页 → 检查修复 → 导出`

完整需求见 [`plan.md`](plan.md)，实际完成度见 [`docs/development-progress.md`](docs/development-progress.md)。

## 已实现

- Next.js 16 + React 19 中文创作工作台，FastAPI + SQLAlchemy API。
- 粘贴、TXT、Markdown 原作导入；不可变修订、来源区间、无损片段和覆盖率校验。
- 角色主要姓名、绰号、歧义冲突、参考图与服装/风格素材绑定。
- 按原文长度动态分页，默认每页 3–7 格、最多 8 个气泡、中文硬上限 180 字；总页数不设上限。
- 右至左分镜数据，以及原文、Scene、Beat、对白和页面之间的追溯关系。
- Nano Banana 2 与 Nano Banana Pro 完全平级，每个候选显式选模型和分辨率。
- 同一页多批次、多候选、跨模型抽卡；收藏多个、采用一个，采用版本才进入下一页连续性上下文。
- 按“章节 → 页面 → 批次”读取素材库，支持页面、角色补图、服装图、风格测试和修复图批次。
- Redis/RQ 持久化任务、DAG、幂等、取消、重试、超时和并发限制；Redis 不可用时任务安全停留在 `WAITING`。
- Gemini 多模态检查、分级修复任务，以及 PNG、PDF、项目 JSON 和素材清单导出。
- Vertex 凭据只由 API/Worker 读取，浏览器不接触服务账号密钥。

## Vertex AI 模型

| 逻辑别名 | 模型 | Vertex 模型 ID | 用途 |
| --- | --- | --- | --- |
| `text.fast` | Gemini 3.5 Flash | `gemini-3.5-flash` | 解析、剧本、分页、分镜和检查 |
| `image.nano_banana_2` | Nano Banana 2 | `gemini-3.1-flash-image` | 页面、资产和修复图生成 |
| `image.nano_banana_pro` | Nano Banana Pro | `gemini-3-pro-image-preview` | 页面、资产和修复图生成 |

两个图像模型没有主次关系。项目只记录上次使用的模型；每次创建候选时必须重新显式传入模型。1K、2K、4K 均在能力注册表中声明，4K 标记为 Preview。

## 本地启动（Windows PowerShell）

要求：Node.js 22+、Python 3.12+，完整异步生成另需 Redis。

```powershell
npm install
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r apps/api/requirements-dev.txt
Copy-Item .env.example .env
.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
npm run dev:full
```

新环境需要在 `.env` 设置：

```env
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
GOOGLE_APPLICATION_CREDENTIALS=D:/absolute/path/to/service-account-key.json
REDIS_URL=redis://localhost:6379/0
QUEUE_ENABLED=true
```

如果只调试页面/API，或本机暂未安装 Redis，可使用：

```powershell
$env:QUEUE_ENABLED='false'
npm run dev
```

此模式仍会持久化任务和候选，但不会执行付费模型调用，任务显示为 `WAITING`。

打开：

- Web 工作台：`http://localhost:3000`
- API 文档：`http://localhost:8000/api/docs`
- API 健康检查：`http://localhost:8000/api/v1/health`

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

## 检查

```powershell
npm run check
```

当前验收结果：25 项后端测试通过、Ruff 与 ESLint 通过、Next.js 生产构建通过、Alembic 全新升级/回滚/再升级通过。Codex 内置浏览器实测左侧七步工作流、角色/服装/风格锁定项、10 页动态分页、两个平级生图模型和素材库筛选；浏览器日志为空，API 请求均成功。真实 Vertex 烟雾测试使用最多 32 输出 token 的 Gemini 3.5 Flash 请求和一张 Nano Banana 2 的 1K 图片请求，均成功。

## 安全说明

- `.env`、服务账号 JSON、数据库、上传素材和生成结果均被 Git 忽略。
- API 不返回凭据路径、服务账号邮箱、令牌或完整项目唯一标识。
- 上传限制为 PNG/JPG/WEBP/TXT/Markdown、最大 20 MB，并校验图片文件内容。
- 生成日志只保存脱敏错误和资产 ID，不记录认证头。
- 普通查询不触发模型调用；所有 AI 创建接口返回 `202 + job_id`。
