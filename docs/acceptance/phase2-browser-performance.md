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
| Pytest | 250 passed（当时；后续隔离测试增加到 257） |
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

返工后数据集：`e2e-lighthouse-workbench`（1 页、1 候选、5 类检查通过）。命令：隔离 runner 内 `npm run test:lighthouse --workspace @mangaflow/web`。阈值未降低。未挑选最好一轮。

### Round 1（exit 0）

| 路由 | performance | accessibility | best-practices |
| --- | ---: | ---: | ---: |
| `/` | 98 | 96 | 93 |
| `/projects/{id}/storyboard` | 97 | 92 | 100 |
| `/projects/{id}/generate`（有候选） | 88 | 96 | 100 |
| `/projects/{id}/workflow` | 94 | 100 | 100 |
| `/settings` | 93 | 96 | 96 |

### Round 2（exit 1）

| 路由 | performance | accessibility | best-practices |
| --- | ---: | ---: | ---: |
| `/` | 98 | 96 | 93 |
| `/projects/{id}/storyboard` | 97 | 92 | 100 |
| `/projects/{id}/generate`（有候选） | **73** | 96 | 100 |
| `/projects/{id}/workflow` | 94 | 100 | 100 |
| `/settings` | 93 | 96 | 96 |

`/generate` 第 2 轮 performance=73 低于 85，未丢弃、未改阈值。空态生成页不再作为本门禁的通过依据。原始脱敏摘要：`docs/acceptance/phase2-metrics.json`。

## 100 节点 FPS（10s，平均>=55，1% low>=45）

命令：隔离 runner 内 `npm run test:workflow-fps --workspace @mangaflow/web`。两轮均渲染 100/100 节点，exit 0。删除使用 `confirm_name`，两次均为 204。

| 轮次 | nodes | seconds | average_fps | one_percent_low_fps | 删除 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 100 | 10 | 133.21 | 48.08 | 204 |
| 2 | 100 | 10 | 140.54 | 71.94 | 204 |

第 1 轮 1% low 48.08 仍高于 45。未改阈值。

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

## 第2轮返工（PR #15 review 5049878461，受审 `0049bb0`）

Windows 清理：不再把 `cleaned=True` 写在 `rmtree(ignore_errors=True)` 之前。控制器创建并记录本次 runtime/`owner.pid`，`stopOwned` 检查 taskkill 退出码（0 或 128），等待子进程树后再删目录；清理失败写入最终 summary 且非零退出。Playwright `globalTeardown` 先停本次 API pid，再 `removeOwnedRuntime`。回归覆盖锁文件、taskkill /F 跳过 Python finally、失败重试、summary 落盘。指标 run_id `2cf64f6b495145ef879810fa37afb0e8` 的临时目录事后已删除，确认不存在。未做临时目录通配符删除，未复用未知实例。

生成页 Lighthouse：审计明细见 `docs/acceptance/phase2-lh-generate-audit.json`。有候选的 `/generate` 两轮均为 performance=73，主因 **CLS=0.477**，LCP≈3.0s，TBT 仅 84–113ms。保留 `53f3444` 的失败轮与本轮失败轮。阈值未降，未改空态，未挑选最好结果。最小修复（预生成缩略图、候选图 `fill`+预留 3:4、动态拆出剧本/分镜面板、`lucide-react` package import 优化、生成区 min-height）未能把 CLS 从 0.477 拉下来。这是正式第 2 轮、用户上限内最后一轮；生成页 85 分门槛仍阻塞，应交组长接手。

本轮最终树 SHA `8584ff5` 上 `npm run check:full`：ESLint/Ruff 通过；Pytest **257**；Vitest 27；Playwright 8 passed；Next production build 通过。指标文件随后只补了该 SHA 字段。

### 本轮 Lighthouse（有候选，阈值未降）

| 轮次 | `/` | storyboard | **generate** | workflow | settings | exit |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 98/96/93 | 96/92/100 | **73**/96/100，cls=0.477 | 92/100/100 | 93/96/96 | 1 |
| 2 | 98/96/93 | 97/92/100 | **73**/96/100，cls=0.477 | 92/100/100 | 92/96/96 | 1 |

### 本轮 FPS

| 轮次 | average | 1% low | 删除 |
| ---: | ---: | ---: | ---: |
| 1 | 142.53 | 140.85 | 204 |
| 2 | 142.41 | 140.85 | 204 |

## 清理

- 已停止 3000/8000 上的本次服务
- 已删除 `output/playwright` 下临时库、trace 与性能摘要（gitignored）
- 未提交 `.env`、凭据、生产库、生成媒体

## 未验

- 未跑真实 Vertex / 付费兼容网关
- 未跑 Redis / PostgreSQL / Docker 集成
- `/generate` 有候选时 Lighthouse performance 两轮均为 73 < 85，主因 CLS 0.477；最小布局预留未消除该位移。未降低阈值。最后一轮返工后仍阻塞，应交组长接手。
