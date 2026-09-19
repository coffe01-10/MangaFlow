# NUI-6/7 实机验收轮结果记录（native-ui-migration.md）

在 `docs/native-ui-migration.md` 追加 2026-09-19 实机并排验收轮章节（本任务明确授权编辑该文件）。内容：

- 历史 `ERR_CONNECTION_REFUSED` 并排对照阻塞解除；工具见 `nui67/acceptance-tooling`。
- 17 页主流程与像素对照逐格走通的结论与边界。
- 三个实机缺陷及修复（详见 `nui67/defect-fixes`，本 PR 文档引用其结论）。
- NUI-7 部分推进：启动计时/退出清理复跑 PASS；125% DPI 单屏与三档宽度 PASS；G1/G3、安装/升级/卸载/签名如实记录 BLOCKED/NOT RUN 及原因；真实供应商生成保持 AUTH-REQ。

## 验证方式

- 纯文档改动；逐格证据索引在 `output/nui67-acceptance/matrix.md`（不跟踪，lead 可按 SHA 复核提交与截图路径）。
- 建议 merge 顺序：defect-fixes → tooling → report（report 引用前两者的结论）。
