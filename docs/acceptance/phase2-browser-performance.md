# Phase 2 浏览器回归与 Lighthouse/FPS 验收

负责人：Grok Build。分支：`codex/phase2-browser-performance-acceptance`。基线：`b7d89c0`。本文件记录当前代码的独立验收，不引用 `cb324e3` 的旧通过记录。

## 环境

| 项 | 值 |
| --- | --- |
| 工作目录 | `D:\自媒体\漫画工作流-grok` |
| 测量时父提交 | `b7d89c0a0e8b7e80abb7293b561d9213bc79ee3d` |
| 本 PR 分支 | `codex/phase2-browser-performance-acceptance` |
| Node | v22.16.0 |
| npm | 11.4.2 |
| Python | 3.12.3（主目录 `.venv` 解释器，未改共享依赖；`PYTHONPATH` / `--app-dir` 指向本 worktree `apps/api`） |
| Chrome | 151.0.7922.174 |
| 构建 | Next.js 16.3.3 production（`next build` + `next start --hostname 127.0.0.1`） |
| API | `scripts/serve_e2e_api.py`：临时 SQLite、独立 storage/uploads、`QUEUE_ENABLED=false`、无真实凭据/代理 |
| 端口 | 独占 `127.0.0.1:3000` / `8000`；`reuseExistingServer` 默认关闭 |
| 数据 | 无 `.env`，未复制凭据或生产库，未调用付费供应商 |

## 修复与回归

1. Playwright 不再默认复用未知 API/Web 进程；新增隔离启动入口，迁移只打在临时库。
2. 复现工作流页 Axe `color-contrast`：节点元信息 `#829087` / `#69726c` 叠在 `#242a27` 上不足 4.5:1。最小修复为改用已有暗色面板灰 `#a4ada7`。
3. 新增浏览器行为回归（真实 UI，不复述 Vitest）：
   - 慢保存期间继续拖动节点并发布最新草稿
   - 保存失败后拦截发布，恢复后才发布
   - 任务 `CONSISTENCY_CHECKING` / `REPAIRING` 持续轮询，`COMPLETED` 后停止
   - 未生产通过时整章 PNG/PDF/JSON 导出按钮禁用，生成页无“生成下一页”

## 失败里程碑：`npm run check:full` Axe color-contrast

**命令**（worktree `D:\自媒体\漫画工作流-grok`，`PYTHONPATH` 指向本树 `apps/api`）：

```
npm run check:full
```

**退出码：** 1（约 403.9s）  
**未跳过 Axe，未降低 serious/critical 阈值。**

### 各门禁

| 门禁 | 结果 |
| --- | --- |
| ESLint | 通过 |
| Ruff | 通过 |
| Pytest | 250 passed |
| Vitest | 27 passed |
| Next.js production build | 通过 |
| Playwright / Axe | **失败**（8 项中 4 failed / 4 passed） |

Playwright 当时已通过：首页导航、项目深链、首屏请求预算、生产门禁/导出阻断。  
失败项：Axe 对比度；以及当时仍在调试的慢保存/保存失败/任务轮询选择器（随后同样修好，不作为对比度豁免）。

### Axe 失败路由与元素

测试：`tests/e2e/platform-v2.spec.ts`「核心页面没有严重或致命 Axe 问题」  
失败路由：`/projects/b2087056-ac97-4244-b27c-04d361fb78ea/workflow`  
规则：`color-contrast`，impact `serious`，3 个节点。断言信息：`/projects/{id}/workflow：color-contrast(3)`。

原失败证据（Axe 4.12 / Playwright，WCAG 2 AA 4.5:1，未改阈值）：

| 元素 | 选择器 | 前景 | 背景 | 实测对比度 | 要求 |
| --- | --- | --- | --- | --- | --- |
| `<small>json</small>` | `div[data-id="adapt"] > … > small` | `#69726c` | `#242a27` | 2.94 | 4.5:1 |
| `<span>director.storyboard</span>` | `div[data-id="storyboard"] > … > header > span` | `#829087` | `#242a27` | 4.38 | 4.5:1 |
| `<small>json</small>` | `div[data-id="storyboard"] > … > small` | `#69726c` | `#242a27` | 4.5:1 未达标（2.94） | 4.5:1 |

对应源码：`workflow-studio.module.css` 的 `.node header span`（原 `#829087`）与 `.ports small`（原 `#69726c`），叠在 `.node` 背景 `#242a27` 上。

最小修复：两处改为已有暗色面板灰 `#a4ada7`。未 disable Axe 规则，未缩小扫描路由。

复跑：

```
npx playwright test
```

8 passed，exit 0（约 56.5s），含原 Axe 用例。之后才进入 Lighthouse / 100 节点 FPS。

## Lighthouse（阈值 performance>=85、accessibility>=90、best-practices>=90）

测试项目 `987822d3-cdc8-4049-84a6-79ec01f22cf6`（已导入原文，无模型解析分页）。命令：`npm run test:lighthouse --workspace @mangaflow/web`，两轮全部 exit 0。未挑选最好一轮。

### Round 1

| 路由 | performance | accessibility | best-practices |
| --- | ---: | ---: | ---: |
| `/` | 97 | 96 | 93 |
| `/projects/{id}/storyboard` | 95 | 95 | 100 |
| `/projects/{id}/generate` | 95 | 100 | 100 |
| `/projects/{id}/workflow` | 93 | 100 | 100 |
| `/settings` | 93 | 96 | 96 |

### Round 2

| 路由 | performance | accessibility | best-practices |
| --- | ---: | ---: | ---: |
| `/` | 98 | 96 | 93 |
| `/projects/{id}/storyboard` | 97 | 95 | 100 |
| `/projects/{id}/generate` | 95 | 100 | 100 |
| `/projects/{id}/workflow` | 91 | 100 | 100 |
| `/settings` | 92 | 96 | 96 |

本机波动：工作流页 performance 93 → 91，首页 97 → 98。两轮均高于阈值。

## 100 节点 FPS（10s，平均>=55，1% low>=45）

命令：`npm run test:workflow-fps --workspace @mangaflow/web`。两轮均渲染 100/100 节点，exit 0。

| 轮次 | nodes | seconds | average_fps | one_percent_low_fps |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 100 | 10 | 142.17 | 140.85 |
| 2 | 100 | 10 | 141.00 | 72.46 |

第 2 轮 1% low 明显低于第 1 轮，仍高于 45。两次都记录，没有只保留最好一次。门禁脚本删除临时项目时 API 返回 422（删除需要 `confirm_name`）；数据只存在隔离库，随后已清理。

## 第1轮返工（PR #15 review）

针对隔离 dotenv 泄漏、未知服务写入、空态门禁和 FPS 删除 422：

- `MANGAFLOW_DISABLE_DOTENV=1` 在导入 Settings 前禁用 `.env`；每次运行唯一临时库，拒绝复用已有 db，进程结束清理。
- 伪 `.env` 探针：`GOOGLE_CLOUD_PROJECT` / `MANGAFLOW_PROXY_URL` 不得进入 Settings。
- 性能 runner：占用端口失败关闭、health 必须带回本次 `e2e_run_id`、子进程先退出则失败、只停止本 run 拥有的 PID、异常仍写 summary。
- 门禁夹具含页面+候选：缺项 / 检查失败 / 分镜过期阻断，全通过才启用下一页和导出。
- 发布后 GET `/workflows/{id}/versions` 核对 published graph 节点位置。
- Lighthouse 使用 `e2e-lighthouse-workbench`（1 页、1 候选、5 类检查通过）。
- FPS 删除带 `confirm_name`，非 204/404 视为清理失败。

完整 `npm run check:full` 与两轮性能在本返工提交上重跑；脱敏原始摘要见 `docs/acceptance/phase2-metrics.json`。

## 清理

- 已停止 3000/8000 上的本次服务
- 已删除 `output/playwright` 下临时库、trace 与性能摘要（gitignored）
- 未提交 `.env`、凭据、生产库、生成媒体

## 未验

- 未跑真实 Vertex / 付费兼容网关
- 未跑 Redis / PostgreSQL / Docker 集成
- 生成页 Lighthouse 在“没有可抽卡页面”空态，不是带候选的生产工作台
- 未把 FPS 门禁的项目删除 422 改成产品修复（超出本任务后端范围）
