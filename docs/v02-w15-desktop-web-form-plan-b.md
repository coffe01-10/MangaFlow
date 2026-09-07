# W-15 设计稿：桌面壳 Web 形态采用方案 B（捆绑 node 运行 `next start`）

> 状态：**待 lead 批准的设计稿**（W-21 ADR 终批时显式留下的决策点；本文批准后成为 W-15 实现契约）。
> 依据：ADR `docs/adr/v02-desktop-shell-evaluation.md` §3.1 否决条件 3（flag 级静态导出存在确定性阻塞，
> 方案 B 未验证）；`apps/desktop/README.md` D5。

## 1. 结论与理由

**方案 B**：桌面壳捆绑一份 node 运行时，helper 启动 `next start`（standalone 产物）作为第二本地服务，
WebView 加载 `http://127.0.0.1:<web_port>`；API 走 Next rewrites 同源代理，保留全部现有路由与
rewrites 兼容性。选它而非静态导出正式改造的原因：工作台组件树的 SSR 预渲染崩溃与动态段预渲染组合
是全前端改造（跨所有路由/组件），而方案 B 的改动集中在壳层进程编排与打包，`apps/web` 业务代码
零改动（已核实，见 §3）。

## 2. 目标 / 非目标

- 目标：安装版桌面壳内的前端 = 完整 Next 生产形态（全部路由、rewrites、未来任何页面）；
  进程所有权/停止/清树语义与现有冻结协议一致；安装包可构建。
- 非目标：签名与自动更新（W-13/W-14 维持 BLOCKED）；静态导出管线的删除（验收通过后另行退役）；
  任何 `apps/web` 业务组件改造。

## 3. 现状核实（决定改动量的三个事实）

1. `apps/web/lib/api.ts:1` —— `NEXT_PUBLIC_API_URL ?? "/api/v1"`：前端默认**同源相对路径**调用 API。
2. `apps/web/next.config.ts` —— rewrites 目标在服务启动时读 `MANGAFLOW_API_ORIGIN` 环境变量。
3. helper 已有 `--web-origin` 参数（传给 API CORS `WEB_ORIGIN`）。

⇒ **`apps/web` 零改动、`next.config.ts` 仅加 `output: "standalone"`（不影响常规构建产物）**：
页面同源调 `/api/v1/*` → Next rewrites 服务端代理到动态 API 端口（浏览器无跨域请求，CORS 形同备用）。

## 4. 架构变化

```
壳（Rust，Job 根，KILL_ON_JOB_CLOSE）
└─ helper（python：alembic + uvicorn API @127.0.0.1:<api>）
   └─ node（next standalone server @127.0.0.1:<web>）   ← 新增，helper 的子进程
WebView → http://127.0.0.1:<web>（WebviewUrl::External，仅回环）   ← 原 WebviewUrl::App
```

- node 由 **helper 派生**（不是壳直派）：helper 已在 Job 内，子进程自动入 Job——`KILL_ON_JOB_CLOSE`
  与强杀清树对 node 自动成立，无需第二个 OwnedTree/assign 流程；Unix 侧 PDEATHSIG 链同理。
- **协议扩展（仅两处身份字段）**：helper 在启动 node、探测其健康后，把 `web_origin` 写入
  journal 并追加进 READY 行；壳校验其必须为回环 origin（复用 `is_loopback_origin`）后才建 WebView。
- **停止顺序**：helper 收到协作停机（stdin EOF→SIGTERM）时先 `terminate` node 再退出；
  Windows 兜底不变（TerminateJobObject 连 node 一起杀）；崩溃路径不变（Job 关闭全灭）。

## 5. 变更清单（按 PR 切片，每片独立可审）

| # | 切片 | 内容 |
| ---: | --- | --- |
| 1 | 协议 + helper | helper `--web-port`/node 参数编排（GO 前 spawn node、健康探测、journal/READY 加 `web_origin`）；shell-core 协议测试扩展；`next.config.ts` 加 `output:"standalone"` |
| 2 | 壳 | `WebviewUrl::External`（校验回环）；停止/清树断言含 node；实机冒烟脚本 |
| 3 | 打包 | 资源清单：pinned node.exe（构建脚本下载+校验哈希）+ `standalone/` + `.next/static` + `public`；`tauri.conf` resources；安装包体积记录 + NSIS 装卸数据安全复验 |

不动的东西：静态导出占位与编译门禁（W-15 验收通过后另轮退役 `patches/` 与 `build-frontend-static.sh`）；
假闭环 e2e 的 API 侧断言；picker/日志/单实例/导出全部照旧。

## 6. 安全不变量对照

| 不变量 | Plan B 下 |
| --- | --- |
| token/journal/GO 门控 | 不变；journal/READY 增加身份字段 `web_origin`（回环校验） |
| Job 所有权/崩溃清树 | 不变且自动覆盖 node（helper 子进程继承 Job 成员） |
| GO 前 API 零流量 | 不变（uvicorn 仍 GO 门控） |
| **GO 前 web 可见性** | **残余（记录在案）**：node 在 READY 前启动，GO 前可拉到 UI 静态壳，但零 API 数据（API 未放行）。接受理由：本机回环、每用户目录、单实例壳 |
| WebView 文档 CSP | tauri.conf CSP 不再约束 External 文档 → 用 Next `headers()` 加等价安全头（实现切片 2 交付，验收含安全头审计） |
| web 端口分配 | helper bind(0) 探测后传给 node（bind-close），极小 TOCTOU 残余记录在案（回环+每用户目录） |

## 7. 验收标准（W-15 出口条件）

1. sidecar 假闭环 e2e 扩展：python 编排 helper+node，断言 web 健康经 rewrites 代理
   （`GET http://127.0.0.1:<web>/api/v1/health` → 200）、生成→候选闭环在 web origin 下全绿、
   停机后 node 与 helper 全灭。
2. Windows 实机 debug 壳：WebView2 加载 Next 生产 UI、生产台可用、关窗协作停机 exit 0、
   `taskkill /F` 清树（含 node）。
3. 双安装包构建成功并记录体积增量；NSIS 装卸数据安全脚本复验通过（复用 W-12 脚本）。
4. 浏览器 E2E 全套（owned Playwright 17 项）不受影响照跑全绿；`npm run check` 全绿。
5. 安全头审计：web 文档响应含 CSP/`X-Frame-Options`/`X-Content-Type-Options`，且不破坏
   Next 内联引导脚本（nonce 或 hash 方案，实现时定）。

## 8. 代价与回滚

- 体积：node.exe（pinned LTS，约 80MB 落盘 / NSIS 压缩后约 +25–35MB）+ standalone 产物；
  实测数字在切片 3 交付。
- 回滚：壳侧以 env/feature 判断 web 形态，静态导出路径在本轮保持可编译；W-15 验收通过后
  另开退役轮删除静态导出管线。
