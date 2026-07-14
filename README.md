# MangaFlow AI

面向小说作者与漫画创作者的私有 AI 漫画生产工作台。项目按结构化闭环建设：原作、角色、服装、风格、剧本、页面、分镜、生成记录和修复计划都是独立数据，而不是一条无法维护的超长提示词。

## 当前进度：里程碑 1 已完成

完整需求见 [`plan.md`](plan.md)，逐里程碑实施状态见 [`docs/development-progress.md`](docs/development-progress.md)。

已完成并可实际运行：

- Next.js 16 + React 19 中文创作工作台。
- FastAPI + SQLAlchemy + SQLite/PostgreSQL 数据层。
- 项目创建、读取、设置更新、乐观锁与软删除。
- 人物、服装、风格参考图安全上传、校验、去重与资产登记。
- 页面/任务状态机、任务 DAG 数据结构和服务端锁定字段校验。
- Vertex AI 服务账号的服务端专用接入与无费用凭据验证。
- 统一文本/图像 Adapter 和模型能力注册表。
- Alembic 初始迁移及升级/回滚验证。
- PostgreSQL、Redis 开发依赖的 Docker Compose 配置。

明确尚未完成：小说解析、角色/风格 AI 分析、剧本改编、分页分镜、图像任务队列、OCR、一致性检查、局部修复和导出。这些入口在 UI 中标注了对应里程碑，不会返回静态成功数据。

设计文档：

- [技术架构](docs/architecture.md)
- [数据模型与状态机](docs/data-model.md)
- [产品需求](plan.md)

## Vertex AI 模型

所有调用只发生在 API/Worker，浏览器不会拿到凭据。

| 逻辑别名 | 模型 | Vertex 模型 ID | 用途 |
| --- | --- | --- | --- |
| `text.fast` | Gemini 3.5 Flash | `gemini-3.5-flash` | 解析、剧本、分页、分镜、结构化检查 |
| `image.fast` | Nano Banana 2 | `gemini-3.1-flash-image` | 默认草稿和批量生图 |
| `image.quality` | Nano Banana Pro | `gemini-3-pro-image` | 关键页与复杂修复 |

两款图像模型在能力注册表中显示 1K、2K 和 4K；4K 仍标记为 Preview。模型 ID 由环境变量选择，业务代码只使用逻辑别名。

## 本地启动（Windows PowerShell）

要求：Node.js 22+、Python 3.12+。

```powershell
npm install
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r apps/api/requirements-dev.txt
Copy-Item .env.example .env
npm run dev
```

当前工作区已经配置了本地 `.env`。如在新环境启动，需要设置：

```env
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
GOOGLE_APPLICATION_CREDENTIALS=D:/absolute/path/to/service-account-key.json
```

打开：

- Web 工作台：`http://localhost:3000`
- API 文档：`http://localhost:8000/api/docs`
- API 健康检查：`http://localhost:8000/api/v1/health`

## 数据库迁移

```powershell
.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
```

本地开发默认使用 `storage/mangaflow.db`。如果要启用 PostgreSQL 与 Redis：

```powershell
docker compose up -d
```

随后将 `DATABASE_URL` 改为：

```env
DATABASE_URL=postgresql+psycopg://mangaflow:mangaflow@localhost:5432/mangaflow
```

## 检查

```powershell
npm run lint
npm run test
npm run build
```

当前验证结果：8 项后端测试通过、Ruff 通过、ESLint 通过、Next.js 生产构建通过、Alembic 升级/回滚通过、Vertex 服务账号 OAuth 刷新成功。

## 安全说明

- `.env`、服务账号 JSON、数据库、上传素材和生成结果均被 Git 忽略。
- API 不返回凭据路径、服务账号邮箱、令牌或完整项目唯一标识。
- 上传限制为 PNG/JPG/WEBP/TXT/Markdown、最大 20 MB，并校验图片文件内容。
- 生成日志只保存脱敏错误和资产 ID，不记录认证头或原文正文。
- `npm audit` 当前报告 Next.js 16.2.10 内置 PostCSS 8.4.31 的 2 个中危项。官方审计给出的自动修复是破坏性降级到 Next 9，未采用。应用不接受或拼接用户 CSS；待 Next 稳定版升级其间接依赖后更新。
