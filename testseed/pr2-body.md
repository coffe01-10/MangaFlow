# NUI-6/7 实机验收工具（三件套编排 + 固定数据集 + 证据采样器）

为解决历史并排对照 `ERR_CONNECTION_REFUSED` 阻塞并支撑 NUI-6/7 实机逐格验收，新增以下可复跑工具。本 PR 只含工具，不含业务代码改动；验收结果文档另见 `nui67/acceptance-report`。

## 1. `scripts/nui67_side_by_side.py` — 三件套编排器
- `start`：预检端口/构建产物 → 以隔离数据目录（绝不触碰用户 `storage/mangaflow.db`）启动 web:3000 + API:8000 → 健康检查 → 双侧播种 → 启动 WPF 客户端。
- `stop`：WPF 优雅关闭（WM_CLOSE）→ 根进程镜像校验 + 后代快照逐一镜像校验的树杀（measure-native-startup 配方）→ 端口/进程残留核验，任何残留只报告不盲杀。
- 进程所有权：CreateProcessW(CREATE_SUSPENDED) + 命名 Job Object + msvcrt.get_osfhandle 可继承日志句柄（修复了 fd≠OS 句柄导致的日志静默丢失）。

## 2. `scripts/nui67_seed.py` — NUI67-DS1 固定数据集
- 2 项目 / 3 章（含分段）/ 场景+节拍+剧本 READY / 3 页面板对白 / 2 角色+参考 / 2 服装 / 场景资产+变体 / 风格 / 3 生成批次 3 候选（含已选+质检）/ 完成+失败任务+调用尝试 / 定价与对账 / 工作流草稿 / 供应商+连接+模型目录。
- 同一函数对 web 库与 WPF user-data 库各播一份，保证并排对照数据一致。

## 3. `apps/desktop/scripts/capture_native_window.py` / `capture_web_page.js`
- PrintWindow 物理 DPI 感知截图（PerMonitorV2 + BGRX 字节序，避免坐标虚拟化裁切与 R/B 互换）与固定视口 web 截图，用于成对证据。

## 4. 采样器（PowerShell + UIA，全样本保留 CSV）
- `measure_page_switch.ps1`：N=20 侧栏切换，以每页唯一标题标记为完成信号。
- `measure_workflow_layout.ps1`：N=20 工作流「自动布局」Invoke → 「待保存」可见延迟 + 保存往返。
- `measure_canvas_drag.ps1`：分镜拖拽延迟（SendInput 通道；在本环境被沙箱拦截，40 失败样本保留于证据目录，口径与结论见台账）。

## 验证方式

- 完整 start → 健康检查 → 逐页交互 → stop 循环 5 次，`stop` 输出 `issues: none`（1 次为 WPF 拒绝优雅关闭后树杀兜底，端口/进程核验干净）。
- 种子数据经 API 读回验证（projects/chapters/script/characters/assets/thumbnails/pages/jobs/usage/providers/workflows 全部 200 且形状正确）。
- G2 采样实测 20/20 OK（P50=160ms / P95=241ms）；工作流采样 20/20 OK（P50=172ms / P95=182ms）。
