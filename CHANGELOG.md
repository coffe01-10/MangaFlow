# Changelog

## 1.0.0-rc1 — 2026-09-20（Release Candidate）

桌面壳换代发布候选：Windows 原生 WPF 客户端（MangaFlow.Native）取代 Tauri 壳成为唯一桌面形态，配套嵌入式 sidecar 后端与 NSIS 安装器；工具链迁移 .NET 10。

### Added（桌面 · WPF 原生客户端）

- **WPF 原生工作台**取代 Tauri/WebView2 壳：项目导航（章节/参考资产/剧本/分页/单页/素材/任务）、可视化分镜画布、工作流编排（节点增删/拖动/连线/校验/发布/版本列表/草稿自动保存）、生成台与任务中心桌面实现；UIA 自动化语义（AutomationId/名称）与键盘焦点环逐格验收。
- **嵌入式 sidecar**：客户端经 native-host.exe 派生 .venv-desktop 后端（127.0.0.1 随机端口、owner token），独立 SQLite 用户数据目录；保存链为 版本化 PATCH + 防抖 + 离开确认。
- **NSIS 安装器**（`apps/desktop/scripts/build-native-installer.ps1` + `installer/mangaflow-native.nsi`）：静默装/覆盖升级/静默卸三态验收脚本 `verify-native-installer.ps1`（安装目录与用户数据取临时路径，不触碰真机数据）；注册表 DisplayVersion 写入。
- 升级路径基线自 0.9.0 起跳（0.9.0 为上一轮安装器验证版本），本轮统一至 1.0.0-rc1。

### Fixed

- 种子数据 draft_graph 为 legacy 形状（{label,params}/{source,target}），API GET 原样透传导致 native 端保存链全量 PATCH 必 422——改为 canonical v2（显式端口 + source_node 四元组）并加回归测试。
- 工作流「添加节点」读取目录的 display_name 字段而目录实际暴露 label，新增节点空名触发 422——取值改为 label 优先。
- .NET 10 迁移暴露的迟到失败家族（render 套件 12 处断言/超时口径）修复，56 项 render 全绿。

### Verified（本轮实测）

- G1 启动基线：net8 N=20 P50=1058.7ms/P95=1114ms；net10 N=20 P50=1198.7ms/P95=1231.5ms（+130ms 归因冷启动样本，见台账）。
- M9 版式重建成功支线、M6 局部修改入口、工作流节点拖拽持久化（draft_version 3→4→6）实机通过。

### NOT RUN / BLOCKED（如实保留）

- 真代码签名证书（采购决策，AUTH-REQ）、应用内检查更新（设计未实现，AUTH-REQ）。
- 工作流连线拖拽、G2 素材库 100/300/500/600 内存档位、安装器 1.0.0-rc1 六格复跑：本轮实机会话被人工使用占用，合成输入停发，留待实机空闲复验。
- 多 DPI / 跨屏（单屏环境 BLOCKED）；付费类调用一律 AUTH-REQ。

## 0.2.0 — 2026-09-06（Release Candidate）

自 0.1.0 以来的第一个发布候选：连续漫画生产工作台（Web）交付完成，Windows 桌面壳（Tauri 2）达到可安装形态，生产缺陷经多轮红队/定向复审收口。

### Added（桌面）

- **Windows 桌面壳 `apps/desktop/`**（Tauri 2，选型已经 ADR 终批）：冻结启动协议（原子绑定 127.0.0.1:0、owner token、journal、GO 门控）、Job Object 进程所有权（CREATE_SUSPENDED → assign → resume，KILL_ON_JOB_CLOSE）、单实例（最小化窗口还原）、统一日志目录 + 按大小轮转 + ZIP 导出、安全本地文件选择（能力表）、用户数据安装/升级/卸载安全契约（卸载不删 `%LOCALAPPDATA%` 用户数据，NSIS 实机验证）。
- **壳内 Web 形态（方案 B）**：helper 派生捆绑 node 运行 Next standalone（完整生产 UI + rewrites，替代受限静态导出）；固定回环中继 39443 解决构建期固化目的地；WebView2 实机验证完整工作台；Next 安全头（CSP/nosniff/XFO/no-referrer）。
- **PyInstaller 冻结 sidecar**（Linux + Windows 双形态，`_internal/` 布局硬约束）；NSIS/MSI 安装包构建链（NSIS 23.7MB / MSI 35.4MB 含 web 资源）。

### Added（Web 工作台）

- 导演模式命令行与结构化 diff 预览、局部选区编辑（region regenerate + 候选血缘）、场景资产/变体、角色模型包（版本矩阵 + 发布继承）、能力验收 fail-closed 门禁、桌面视觉系统（token/焦点/动效/模板）、任务中心（归档/批量/恢复）、用量与成本看板、工作流引擎（发布竞争防护、run 生命周期、取消传播）。
- 可视化分镜画布编辑器（几何/对白/阅读顺序 + 原子几何快照）。

### Fixed

- 44 项红队缺陷（PR #176）：适配器错误分类、租约围栏/重试互斥、SOURCE_PARSE 互斥、plan 串行化、项目归属扫描（39 端点）、CLI 通道诚实化、备份完整性、前端冲突恢复、桌面日志导出覆盖等。
- RC 定向复审：**并发编辑丢失页级分镜围栏增量**（原子 SQL 自增 + 页行锁 + 导演 accept 锁序）；**生成台消费归档视图致检查静默卡死**（固定近期视图 + 共享终态谓词）；场景服装围栏调用点。
- Windows 实机发现：CPython 3.12 venv launcher 式 python.exe 的 READY pid 为孙进程（协议改按 Job 成员验收）；单实例对最小化窗口无效（补 unminimize）。

### Performance

- **生成台 CLS 0.477 → 0.000**（两轮 Lighthouse 验收，#28 关闭）。
- **storyboard 路由 perf 81/84 → 91/90**（section 按路由代码分割，unused-JS 810ms 归因修复）；全部路由两轮 ≥85，FPS 100 节点两轮 exit 0。

### Verified（真实环境）

- PostgreSQL live 18 项（127.0.0.1:55432 隔离库）、Redis/RQ SimpleWorker live 8 passed / 7 skipped（56379/15）——`docs/acceptance/phase2-database-queue.md` 2026-09-06 节。
- Windows 实机桌面验收轮：Job Object 全链（协作停机/强杀清树）、WebView2 渲染、单实例多开、安装器装卸数据安全、冻结 sidecar 冒烟——`docs/v02-windows-leftover-status.md`（RUN 9 / NOT RUN 11 / BLOCKED 4）。

### NOT RUN / BLOCKED（如实保留）

- Windows Job Object 独立 Worker 7 项（需 Windows + PG/Redis 同机）；V02-52A N=20 全样本（两轮 LH/FPS 门禁已过）；真实供应商与付费调用；代码签名/自动更新（无证书/服务器）；WebView2 缺失安装行为、壳内工具页对话框实机交互、CSP 指令级逐条执行、用户数据 ACL 收紧、Electron 对比壳。逐项见 `docs/v02-windows-leftover-status.md`。
