# 本地开发与数据管理

本文承接 [中文 README 的首次启动流程](../README.zh-CN.md#快速开始)，集中记录 Windows 环境、独立 Worker、代理和数据备份。命令均在仓库根目录的 PowerShell 中执行；不要把其他机器的绝对路径直接复制到自己的配置。

## 日常启动与更新

要求 Node.js 22+、Python 3.12+。首次安装使用仓库自带的 [setup-codex.ps1](../scripts/setup-codex.ps1)：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-codex.ps1
```

该脚本会安装依赖、在缺失时创建 `.venv` 与 `.env`、建立数据目录，并执行 Alembic 升级。它不会覆盖已有 `.env`；更新代码后若依赖或迁移有变化，也可在备份后重新执行。

日常使用 [start-dev.ps1](../scripts/start-dev.ps1)：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

脚本要求 `.venv` 和 `.env` 已存在，先迁移数据库，迁移失败则不会继续启动。随后以开发环境启动 Web 和 API，并启用任务队列。停止时在该窗口按 `Ctrl+C`。

不使用启动脚本时，先迁移再启动；单独的 `npm run dev` 不负责数据库升级：

```powershell
.\.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
npm run dev
```

默认入口为 [Web](http://localhost:3000)、[API 文档](http://localhost:8000/api/docs) 和 [健康检查](http://localhost:8000/api/v1/health)。端口被占用时先确认是否已有开发服务运行，不要直接终止不属于本项目的进程。

## 配置供应商与代理

完整配置模板见 [.env.example](../.env.example)。普通工作台启动不要求 Vertex 凭据；也可以在设置页配置其他支持的供应商。

只有使用 Vertex 时，才需要按自己的 Google Cloud 环境填写 `GOOGLE_CLOUD_PROJECT`、`GOOGLE_CLOUD_LOCATION` 和 `GOOGLE_APPLICATION_CREDENTIALS`。凭据文件使用本机真实绝对路径，不把服务账号 JSON 放进 Git。此文不承诺某个预设模型在所有账号、区域或供应商中可用。

开发环境第一次保存供应商 API Key 时，可以在 `storage/.provider-credential-master-key` 自动建立本机主密钥。生产环境必须显式提供 `MANGAFLOW_CREDENTIAL_MASTER_KEY`；保留数据库却丢失主密钥，可能无法解密已保存的 API Key。不要将示例字符串作为真实密钥。

如果访问上游需要代理，在 `.env` 中设置 `MANGAFLOW_PROXY_URL` 为自己代理软件实际使用的 HTTP / Mixed 地址。启动脚本会设置当前进程的 `HTTP_PROXY`、`HTTPS_PROXY`，并将 `localhost`、`127.0.0.1` 加入直连范围。仅 SOCKS 端口不适用于这个配置入口。

模型能力测试本身也可能发起付费请求。确认费用、数据发送范围与供应商条款后，再做文本、视觉或图片测试。更多接入说明见[供应商与模型平台](provider-platform.md)。

## 任务执行模式

| 模式 | 当前设计行为 |
| --- | --- |
| `AUTO` | 开发环境中 Redis 不可用时使用本地执行器 |
| `LOCAL` | 使用本地执行器，不依赖独立 Redis / RQ Worker |
| `REDIS` | 依赖 Redis 与独立 Worker；Redis 不可用时不能正常派发任务 |

开发依赖容器由 [docker-compose.yml](../docker-compose.yml) 定义：

```powershell
docker compose up -d
```

此命令需要 Docker Compose，会启动 PostgreSQL 与 Redis 容器并使用持久化卷，**不会启动 MangaFlow 应用，也不会自动把默认 SQLite 切换成 PostgreSQL**。更换数据库需检查 `DATABASE_URL` 并对目标库执行迁移。

仅在 Redis 已启动、配置匹配且需要独立 RQ Worker 时使用：

```powershell
npm run dev:full
```

当前 `dev:worker` 命令使用 `redis://localhost:6379/0` 与 `mangaflow` 队列，定义见 [package.json](../package.json)。如果修改 Redis 地址或队列名，API 与 Worker 必须一致；不要认为脚本会自动读取所有自定义值。

> 这些模式已存在，但不能据此保证所有取消、重试和并发等待场景可靠。当前任务状态与调度问题按 P1-7、P1-9～P1-11 跟踪，见[路线图](roadmap.md)。

## Codex 本地环境

如果使用设置了 `CODEX_WORKTREE_PATH` 的 Codex Windows 本地环境，设置脚本可以填写：

```powershell
powershell -ExecutionPolicy Bypass -File "$env:CODEX_WORKTREE_PATH\scripts\setup-codex.ps1"
```

环境创建完成后，仍使用 `scripts/start-dev.ps1` 启动。`setup-codex.ps1` 优先使用 `CODEX_WORKTREE_PATH`；普通终端不要将它指向另一个工作区，以免初始化错误的目录。

## 数据与备份

默认存储是 SQLite 元数据与本地文件目录。数据库保存业务关系和相对 `storage_key`，图片文件不直接存入 SQLite；实际根目录可由配置调整。

| 数据 | 默认位置 |
| --- | --- |
| 项目、版本、分镜、候选、任务和工作流记录 | `storage/mangaflow.db` |
| AI 生成原图 | `storage/generated/<project_id>/<batch_id>/` |
| 生成图缩略图 | `storage/thumbnails/<asset_id>/` |
| 用户上传原文件 | `uploads/<project_id>/` |
| 上传图缩略图 | `uploads/thumbnails/<asset_id>/` |
| PNG、PDF、项目 JSON 和素材清单 | `storage/exports/<project_id>/<chapter_id>/` |
| 界面偏好与尚未导入服务端的旧版工作流草稿 | 浏览器 `localStorage` / `sessionStorage` |

候选撤回只改变采用关系，不删除候选或原图；项目和素材的普通删除是软删除，不代表磁盘空间已释放。素材显示名保存在数据库中，不会改写磁盘上的文件名或 `storage_key`。

清除浏览器缓存不会删除服务端保存的项目与素材，但可能清除界面偏好或尚未导入服务端的旧版草稿。当前还有工作流保存竞态，重要修改应确认保存成功，见路线图 P1-8。

### 备份范围

Git 忽略本地数据与凭据。提交代码、推送分支或合并到 `master` 均不构成备份。

1. 先停止 API 和 Worker 的写入，再复制 `storage/mangaflow.db`；若不能停机，应使用 SQLite 在线备份方式生成一致性快照，不直接复制正在写入的数据库作为可靠备份。
2. 同时备份 `storage/generated/` 与 `uploads/`。只有数据库，没有这些目录，无法恢复原始素材。
3. 要保留已经导出的成品，再备份 `storage/exports/`。缩略图目录也可一并保留。
4. 单独加密保存 `.env`、`storage/.provider-credential-master-key`（如存在）和使用中的 Vertex 凭据；不要把密钥混入普通素材归档或提交到仓库。

仅包含 `.db` 的迁移备份不包含上传和生成图片，不能替代上述完整备份。

### 恢复检查

恢复到另一台机器时，保留数据目录相对结构，重新配置本机路径、依赖与密钥，并备份恢复副本后执行 Alembic 升级。不要把“迁移成功”当成完整恢复验收：还应打开项目、读取原图、确认候选与分镜关系、检查导出文件和供应商密钥能否正常解密。

此处提供操作边界，不声称已完成跨机器恢复演练。演练任务在[路线图 P2-5](roadmap.md)跟踪。
