**Found:** 第七轮终扫（a08dae8..a7b7e55），组长已读代码核实（2026-09-11）。#388（web 侧已修）的桌面同构缺口。

## 机制

`apps/desktop/native/Views/WorkflowView.cs` RenderApprovals（1406-1454）：

1. 行 1420-1421：`modelBox.SelectedItem = ...FirstOrDefault(tag == drawModel) ?? First()`——选中失效别名时回退到占位项，但 `SelectionChanged`（行 1422）在赋值**之后**才挂上，事件不触发，`drawModel` 字段保留失效别名。
2. 行 1451/1428：`approve.IsEnabled = ... drawModel.Length > 0`——只查长度不查目录成员资格。
3. 行 1466-1467：载荷以 `image_model_alias: drawModel` 提交失效别名 → 后端 resolve_model 拒绝。

触发：桌面审批栅栏选中模型未确认 → 供应商被删/目录重验 → LoadRunsAsync 重渲染审批条 → 下拉显示占位项但「确认继续」仍可点。与 web #388 修复（3af44d4）确立的契约不等价。

## 修复面

谓词改为与 web 同构：`drawModel.Length > 0 && imageModels.Any(m => m.Text("logical_alias") == drawModel)`（注意行 1413-1414 的下拉可见行豁免 `(enabled&&display_enabled) || alias == drawModel` 与 creatorVisibleModels 语义一致；成员资格判定基于全 imageModels）。SelectionChanged（1428）与初始使能（1451）两处同改。native 测试（NativeWorkflowRunChecks）补钉：选中后目录剔除该行 → 按钮禁用；目录仍在 → 解禁回归。
