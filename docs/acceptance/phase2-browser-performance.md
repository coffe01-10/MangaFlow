# Phase 2 浏览器回归与 Lighthouse/FPS 验收

成员交付：Grok Build；当前由组长接管。分支：`codex/phase2-browser-performance-acceptance`。基线：`b7d89c0`。本文件保留成员历史报告；组长独立结果以最新接管章节为准。历史报告不代表当前代码已通过验收。

## 合并前独立复验（2026-08-29，本节为准）

最终代码提交：`833197c5b77671c01833559e02ec44adbf117c3a`。组长没有直接采信交付报告；本轮实际发现并修复两项验收入口问题：

- 首次 `npm run check:full` 在中文仓库路径下因控制器把混合编码日志先解为 UTF-8、再写入 GBK 终端而触发 `UnicodeEncodeError`。修复为原始字节透传并补回归；隔离测试由 29 增至 30。
- 首次两轮性能复验中 Lighthouse 全过，但 FPS 第 1 轮为平均 127.65、1% low **35.97 < 45**，第 2 轮通过。失败保留，未以第二轮抵消。审计发现门禁在新浏览器首次交互时立即采样，且 1200 帧上限在约 144Hz 下只覆盖约 8.3 秒。修复为固定 2 秒预热后采满 10 秒，仍保持 100 节点、10 秒、平均 55 / 1% low 45 和两轮全过标准。

最终准确 SHA 上 `npm run check:full`（run_id `2a9ab9abe0c044c3baa81cc292770fcb`，exit 0）：ESLint/Ruff、**280 Python**（19 条既有警告）、**27 Vitest**、Next.js 生产构建、**8 Playwright/Axe** 全部通过；外层报告 `process_tree_stopped=true`、`runtime_removed=true`。

最终性能运行（run_id `1766d340c743496f9063d6e111b9397f`，外层 exit 0）：

| 轮次 | `/` | storyboard | generate（1页/1候选/5检查） | workflow | settings |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 98/96/93 | 93/92/100 | **94/96/100，CLS 0.000** | 93/100/100 | 92/96/96 |
| 2 | 97/96/93 | 95/92/100 | **94/96/100，CLS 0.000** | 93/100/100 | 92/96/96 |

| FPS轮次 | 样本 | 实测时长 | average | 1% low | p99 frame | max frame | 删除 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1414 | 10052.3ms | 140.66 | 71.94 | 13.9ms | 20.9ms | 204 |
| 2 | 1408 | 9962.0ms | 141.34 | 71.94 | 13.9ms | 27.8ms | 204 |

本次只使用随机临时 SQLite/存储与本地占位配置；未读取真实 `.env`/凭据，未连接 PostgreSQL/Redis/RQ，未调用供应商。`/settings` CLS 0.142/0.146 仍记录为观察项，但 performance 92 高于 85 门槛。

## GLM 交付验收记录（2026-08-28，历史）

状态：**浏览器回归与 Lighthouse/FPS 验收在最终代码 `ee73240` 上完成，两轮全过、无跳过、无降阈值。** PR 合并仍等待审阅；真实 PostgreSQL/Redis/RQ 不在本 PR 范围。

### 本轮修复（组长接管后）

1. `aa7ebe0` — **生成页 CLS 0.477 修复**。诊断方法：`lighthouse-gate.mjs` 新增 layout-shifts 审计导出（仅诊断，不改阈值），归因显示全部 CLS 来自单一元素 `section.generation-reference-check.production-readiness`：其加载态只有一行文字，workbench 查询返回后渲染阻塞列表加默认展开的诊断网格（384px 宽度下高约 1016px），把整个画布向下推。修复：`project-workspace.tsx` 在 workbench/批次/模型数据就绪前显示 `.generate-skeleton` 占位，数据就绪后一次性插入整个画布；插入点下方只有固定定位元素，因此不再产生位移。数据集、阈值、空态均未改动。
2. `956e4d0` — 控制器子环境移除 `QUEUE_ENABLED=false`（队列隔离由 `serve_e2e_api.py` 按服务设置）。该变量曾泄漏进 `npm run check` 的 pytest，使 14 个队列相关测试误报 `QUEUE_DISABLED`。第一次全量运行（`f904c891` 报告）暴露并保留此失败。
3. `5a7e96c` — `phase2_runner` 共享逻辑抽为 `scripts/phase2_runner_lib.cjs`。Playwright 以 CJS 转译加载 `playwright.config.ts`/`global-setup.ts`，直接 import ESM 的 `.mjs` 会在配置加载时崩溃（`import.meta` 不可用），第二次全量运行暴露并保留此失败。`.mjs` 保留为再导出，`run_phase2_performance.mjs` 与 Node helper 测试不受影响。
4. `ee73240` — lib.cjs 内 `fileURLToPath(__filename)` 误用修正为 `__dirname`（CJS 下 `__filename` 不是 file URL）。由 `test_supervised_node_verifies_membership_and_child_api_configuration` 捕获。
5. `52ac107` — `tests/test_e2e_isolation.py` 显式 `from builtins import ExceptionGroup`：根目录 Ruff 以默认 target 检查 `tests/`（pyproject 在 `apps/api` 下），3.11+ 内建名报 F821。

### 最终门禁（代码 SHA `ee732403f54733e8d636f7a03c4c59772ac8d64f`）

`npm run check:full`（外层控制器模式 `full`，报告 `output/playwright/owned-7fa0cbd9cfd74809ac2bcb1fd2a432ab/summary.json`，exit 0）：

| 门禁 | 结果 |
| --- | --- |
| ESLint | 通过 |
| Ruff | 通过 |
| Pytest | **279 passed**（19 条既有警告，无 skip 计通过） |
| Vitest | **27 passed** |
| Next.js production build | 通过 |
| Playwright / Axe | **8 passed**（含 Axe color-contrast 用例） |

### 最终 Lighthouse / FPS（`npm run test:phase2-performance`，run_id `3d44d9df53894a9d94fc1ad623c7b417`，外层报告 exit 0、`runtime_removed=true`）

数据集：`e2e-lighthouse-workbench`（1 页、1 候选、5 类检查通过），阈值 performance>=85 / accessibility>=90 / best-practices>=90，FPS 100 节点 10s 平均>=55、1% low>=45。

Lighthouse（performance / accessibility / best-practices）：

| 轮次 | `/` | storyboard | **generate（有候选）** | workflow | settings | exit |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 98/96/93 | 96/92/100 | **94**/96/100，cls=0.000，lcp=3060ms | 91/100/100 | 93/96/96 | 0 |
| 2 | 98/96/93 | 96/92/100 | **94**/96/100，cls=0.000，lcp=3064ms | 92/100/100 | 92/96/96 | 0 |

100 节点 FPS：

| 轮次 | average | 1% low | 删除 |
| ---: | ---: | ---: | ---: |
| 1 | 142.41 | 140.85 | 204 |
| 2 | 142.65 | 140.85 | 204 |

### 保留的失败与诊断轮（未丢弃）

- 成员历史失败：`53f3444` round 2 与 `8584ff5` 两轮 `/generate` performance=73、CLS=0.477（`docs/acceptance/phase2-lh-generate-audit.json`）。
- 组长诊断轮 `009d7aa261a346608d3af7acc4e6c3cb`（修复前，`4c06cb4`）：`/generate` 58/72、CLS=0.477；layout-shifts 归因如上。
- 修复后诊断轮 `8a811c5a0348455c9623d578303f7a1e`（`aa7ebe0` 工作树）：`/generate` 94/93、CLS=0.000。

### 遗留观察

- `/settings` 仍有 CLS 0.146，performance 92–93 仍高于 85 门槛；如实记录，未隐藏，未做进一步改动。
- `/generate` LCP 仍约 3.0s（客户端查询链 chapters→pages→workbench→batches→candidates 串联）；CLS 修复后分数已达 94，按审阅要求未做大模块重构。
- 历史上新控制器已在 `tests/test_e2e_isolation.py`（29 passed）与 Node helper（9 passed）复核；本轮四项配套修复后两组测试再次全部通过。

## 组长接管后的安全入口修复（2026-08-28）

状态：**中间修复，PR 未通过验收，禁止以本节定向结果替代浏览器/性能通过。**

第二轮成员交付 `0c59f05`（代码 `8584ff5`）已在审阅
[5050356909](https://github.com/coffe01-10/MangaFlow/pull/15#pullrequestreview-5050356909)
被阻塞并由组长接管。下文保留的 257 Python / 27 Vitest / 8 Playwright、
Lighthouse / FPS 数字是成员历史报告，不能用于本节修改后的代码。

### 本轮修改

- `scripts/run_e2e_owned.py` 是 Windows 验收的统一外层控制器。随机目录和 owner 排他创建，
  使用 Windows Job Object 挂起创建、先归属后恢复执行，持有进程 HANDLE。
  API、Node、Next、浏览器及所有后代必须退出后才清理 payload；不读取共享 PID 指针、不按 PID taskkill。
- `owned_processes.py` 基础来自 PR14 的 `6086155`。实际 API 冒烟额外发现：
  Job 的 active 计数归零可能先于进程 HANDLE 完成信号；已归属成员不能再调用 TerminateProcess，
  应等待 HANDLE。首次回归 24 passed / 1 failed（WinError 5），修复并补回归后通过。
  PR14 的对应模块尚未带入本次退出竞争修复，后续需同步该修复和回归。
- `e2e_runtime.py` 校验规范绝对路径、owner token、控制器创建时间和实际 Job 成员身份。
  子进程无权删除运行目录。监听端口通过只读查询获取 PID，再打开 HANDLE 验证属于本次 Job，
  未知实例不能用健康页或网页响应冒充。
- Python 环境采用允许列表并禁 dotenv。Next 的 `__NEXT_PROCESSED_ENV` 本身仍读取 dotenv，
  因此本次 Node 进程专用 preload 替换 `@next/env` 的加载入口；临时哨兵测试断言无 dotenv 读取。
  不修改全局 Node 配置。
- `npm run test:e2e`、`npm run check:full` 和 `npm run test:phase2-performance` 都进入控制器；
  完整检查仍执行 `npm run check`（包括生产构建）再运行 Playwright，不降低门禁。
  E2E 单独执行时先构建；不再重复完整检查后的构建。
- 权威最终报告为 `output/playwright/owned-<token>/summary.json`，包含 body/cleanup 错误、
  进程树停止和目录删除状态，失败非零；同目录日志保留。性能子报告位于
  `output/playwright/phase2/<token>/summary.json`，其运行结束先于外层清理，
  **必须和外层最终报告一起读取**。清理失败保留 owner，可通过同一控制器的 recover 模式恢复；
  活动控制器或 Job 一律拒绝恢复。

### 组长独立验证

最终：`tests/test_e2e_isolation.py` **29 passed / 1 条既有 Starlette 警告 / 13.72s**。
其中包含 Node helper 回归、真实 Windows 子进程/孙进程、控制器强退和异常退出、
实际 SQLite 文件锁、失败后重试、Job/监听归属拒绝、伪 dotenv 不读取、
真实 API 迁移/种子/健康身份/项目读取及实际 API 进程退出证明。
外层控制器也用实际轻量命令验证成功/非零失败、停止后续命令、最终报告和清理。
这些命令探针不是 Playwright 或性能测试。Ruff 本轮 Python 文件、Node 语法及 diff 检查通过。

临时数据全部归属本次测试并已清理；API 冒烟使用随机回环端口。
未读取真实 dotenv/凭据、未连接 PostgreSQL/Redis、未调用供应商。

### 尚未验收

（已在上方"组长最终验收"完成：`ee73240` 上 check:full 与两轮 Lighthouse/FPS 均通过；本节保留为历史记录。）
新入口上的 `check:full`、Playwright、两轮 Lighthouse/FPS 尚未运行。
有候选生成页的历史两轮 Lighthouse 仍为 **73 / 73，CLS=0.477**，没有降阈值、
删数据或挑选新分数。下一步先复核最终控制器的完整运行，再定位实际布局偏移元素修复，
对最终 SHA 独立全量与性能测量。Windows 以外的监督能力没有声明验证。

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
- （已解决）`/generate` 有候选时 Lighthouse performance 曾两轮 73 < 85，主因 CLS 0.477；已在组长最终验收章节修复并通过。`/settings` 的 CLS 0.146 仍作为观察项记录。
