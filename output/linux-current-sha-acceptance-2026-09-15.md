# Linux 当前基线持续验收报告 · 2026-09-15

状态：**PASS_WITH_GAPS**（见「缺口与 NOT RUN」）

## 当前 SHA 与环境

- 工作树 `/workspace/MangaFlow-linux-accept` · 分支 `night/linux-accept-20260915`
- **HEAD = `e53ddde`**（"Update WPF migration status and backlog"），与 origin/master 一致（无漂移、无未推送提交）。含预期 c3f8376 与 ea3f0bf。
- Node v20.19.2；Python 3.13.5（复用 `/workspace/MangaFlow-N1-Core/.venv`，未重建/未联网安装）
- Rust：cargo + rustup（x86_64-pc-windows-msvc target 已装）；llvm-rc 已装
- Chrome 151（内置浏览器，remote-debugging 9225/9224）可用

## Web/API 门禁（Linux 实测）

| # | 命令 | 结果 | 耗时 |
| --- | --- | --- | --- |
| 1 | `npm run lint --workspace @mangaflow/web` | **PASS**（首轮失败：ESLint 找不到 config——web workspace 依赖未安装；`npm install --workspace @mangaflow/web` 后重跑通过。环境问题，非代码缺陷） | 13.4s |
| 2 | `.venv/bin/python -m ruff check apps/api tests` | **PASS**（All checks passed） | 0.1s |
| 3 | `npx tsc -p tsconfig.scripts.json` | **PASS** | 1.4s |
| 4 | `npx tsc -p tsconfig.runners.json` | **PASS** | 1.1s |
| 5 | `.venv/bin/python -m pytest tests` | **PASS：1554 passed, 104 skipped** | 2m58s |
| 6 | `npm run test --workspace @mangaflow/web` | **PASS：600 passed** | 17.8s |
| 7 | `npm run build --workspace @mangaflow/web` | **PASS** | 15.6s |

说明：根 `package.json` 的 dev/test/lint 脚本硬编码 `.venv\Scripts\python.exe`（Windows 反斜杠路径），Linux 等价门禁按任务要求用 venv 解释器直跑等价命令。

## 供应商平权

`pwsh` 不存在 → `check-provider-neutrality.ps1` **NOT RUN**（未弱化扫描冒充）。

## 桌面壳验证（Linux 实测）

### shell-core `cargo test`
**169 passed, 0 failed**（`apps/desktop/shell-core`）。覆盖：启动协议（READY/GO/journal 校验、symlink/FIFO 拒绝、64KiB bound）、OwnedTree/stop、日志导出/轮转（含 #430 staging 自愈、#561 link 拒绝）、路径边界、子进程清理。

### sidecar e2e（run-sidecar-e2e.sh）
**160 passed**（真实 API + 33 个 alembic 迁移 + SQLite 隔离 + 假模型 + READY/GO + 健康检查 + 生成→候选采用→PNG + 协作停机清理）。full-loop、plan-B web loop、exit-watch、relay 全覆盖。

### Windows 交叉编译检查（非运行验收）
先决条件齐备（rustup msvc target + llvm-rc）。首轮失败：`resource path web/standalone doesn't exist`——跑 `assemble-web-resources.py`（Linux 兼容，node 走 PATH）装填 4MB 资源后 **cargo check --target x86_64-pc-windows-msvc PASS**。这是**交叉编译检查**，不是 WPF 运行验收。

### 静态前端构建 + verify-static-origin
- `build-frontend-static.sh` PASS（静态导出拷贝至 dist/frontend）。
- `verify-static-origin.mjs`：首轮失败（helper 报 `No module named 'alembic'`——脚本默认 `python3` 无 API 依赖）；以 `MANGAFLOW_DESKTOP_PYTHON=<venv python>` 重跑 **D5 PASS**（静态导出 + 运行时 origin 注入 + 直连 CORS-allowed API + 页面渲染 + 静态服务器 `/api/*` 零命中）。

## 隔离 SQLite / Web 服务的浏览器页面矩阵

- 33 个 alembic 迁移应用于隔离 `sqlite:////tmp/accept-data/mangaflow.db`（首跑用 `-x db_url` 未生效——env.py 走 `get_settings().database_url`，改用 `DATABASE_URL` 环境变量后成功；教训已记录）。
- API：`/api/v1/health` ok、`/api/v1/projects` 200（隔离 DB）。
- plan-B 前端（dist/web-standalone/server.js）以 PORT=3012/HOSTNAME=127.0.0.1/MANGAFLOW_API_ORIGIN=relay 启动，`/` 200。

**页面矩阵（HTTP 层 + 静态资源；真实交互需 WPF/WebView2，NOT RUN）：**

| 路由 | 结果 |
| --- | --- |
| `/` | 200（MangaFlow AI 工作台 title） |
| `/help`、`/workflow`、`/settings`、`/settings/usage` | 200 |
| `/characters`、`/outfits`、`/scenes` 等旧平铺路径 | 404（路由已迁移至 `/projects/[id]/…` 结构，属预期） |
| 项目内：`/projects/[id]/assets`、`/settings`、`/workflow` 等 | 路由存在（动态段，需真实项目 id 才有内容） |

**未做（须 Windows/人工）**：窄宽布局、加载/空态/错误恢复的视觉确认、表单校验与键盘焦点、Esc 弹层——这些需要真实渲染的 WebView2/WPF 页面，Linux 只能验证 HTTP/路由/静态资源层。

## 修改的文件
- `output/n1-shell-audit.md`（本 Goal 各轮追加；用户既有文档未触碰）
- `output/linux-current-sha-acceptance-2026-09-15.md`（本报告）
- 工作树其余部分干净；`dist/` 构建产物未提交（gitignored）。

## 新增测试
本轮无新增测试文件（验收轮；所有 pin 均已在 master 的 1554 pytest + 600 vitest + 169 cargo 中）。

## 缺口与 NOT RUN（全部为平台限定或既有未验证边界）
- net8.0-windows WPF 运行、WindowsDesktop 测试、Job Object、WebView2、NSIS/MSI、签名、自动更新：**NOT RUN**（须 Windows 实机）。
- `cargo check --target x86_64-pc-windows-msvc`：**交叉编译检查 PASS**——不是 WPF 运行验收。
- `check-provider-neutrality.ps1`：**NOT RUN**（无 pwsh）。
- 真实供应商调用/真实密钥：**NOT RUN**（全程假供应商 + 隔离 SQLite）。
- 浏览器矩阵的交互层（窄宽/焦点/Esc/表单）：**NOT RUN**（须 WebView2/WPF 渲染）。

## 清理结果
- 无遗留任务进程；无遗留持有端口（3011/3012 已释放）。
- `/tmp/accept-data`（隔离 DB/storage/uploads）与 `/tmp/accept-*.log` 保留为失败证据与复验环境；无临时 worktree 残留。
- `git status` 干净；`git diff --check` 无冲突标记。
- 开始时已有用户文档改动：无（基线 e53ddde 干净），未被覆盖。

## 唯一推荐的下一项构建任务
在 Windows 实机上跑通「安装器 → 启动 → 工具菜单 → 日志导出」链路，并把 measure-native-startup.ps1 的递归树快照接上 NUI-7 的重测（其余残留均以此项为前置）。
