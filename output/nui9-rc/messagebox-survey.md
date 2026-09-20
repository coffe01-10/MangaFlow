# NUI-8-A：MessageBox.Show 全仓普查表（NUI-9 P2-2）

- 普查范围：`apps/desktop/native`（生产代码），`MessageBox.Show` 共 **79 处**。
- 口径：`MessageBoxButton.YesNo` 确认类（=目标定义的「43 处」）全部迁移到 `ConfirmDialog`（Esc 关闭 / 默认焦点安全钮「取消」/ Tab 键盘可达 / danger 分支红钮 / 无 IsDefault 回车不触发）；`MessageBoxButton.OK` 纯信息与错误提示类保留不动。
- 残留核验：迁移后 `MessageBoxButton.YesNo` 在生产代码中出现 **0 次**；`MessageBox.Show` 残留 38 处全部为 OK/info/错误提示。

## 已迁移（43 处，ConfirmDialog）

| 文件 | 原 MessageBox（行号按迁移前） | 确认钮 / danger |
| --- | --- | --- |
| AssetsView.cs:1121 | 删除服装档案 | 删除 / danger |
| CharacterAssetPage.cs:209 | 重分类（解除绑定） | 继续 / – |
| CharacterAssetPage.cs:213 | 删除素材 | 删除 / danger |
| CharacterPackagePane.cs:176 | 角色模型包归档/恢复 | 归档·恢复 / – |
| CharacterPackagePane.cs:428 | 未保存的规格（离开丢弃） | 离开 / – |
| CharacterPackagePane.cs:445 | 发布版本（不可变） | 发布 / – |
| CharacterPackagePane.cs:466 | 删除草稿（不可撤销） | 删除 / danger |
| GenerateView.cs:787 | 删除候选 | 删除 / danger |
| GenerateView.cs:805 | 人工校对并暂选 | 继续 / – |
| GenerateView.cs:822 | 保持结构升清（计费） | 继续 / danger |
| GenerateView.cs:885 | 沿用并重新检查 | 继续 / – |
| GenerateView.cs:1516 | 离开确认（导演指令丢弃） | 离开 / – |
| JobsView.cs:220 | 重试任务（计费） | 重试 / danger |
| JobsView.cs:242 | 彻底删除任务 | 彻底删除 / danger |
| JobsView.cs:308 | 归档全部终态 | 归档 / – |
| LibraryView.cs:247 | 撤回暂选 | 撤回 / – |
| LibraryView.cs:255 | 隐藏候选 | 隐藏 / – |
| LocalEditWindow.cs:323 | 采用局部修改结果 | 继续 / – |
| LocalEditWindow.cs:355 | 关闭局部修改（丢弃失败仍可关） | 关闭 / danger |
| LocalEditWindow.cs:375 | 关闭确认（选区丢弃） | 关闭 / – |
| OutfitWorkspace.cs:273 | 删除服装档案 | 删除 / danger |
| OutfitWorkspace.cs:391 | 修改素材用途 | 继续 / – |
| OutfitWorkspace.cs:392 | 删除素材 | 删除 / danger |
| ProjectSettingsView.cs:436 | 删除项目 | 删除 / danger |
| ProjectSettingsView.cs:469 | 离开确认（设置丢弃） | 离开 / – |
| ReferenceWorkspace.cs:146 | 参考素材 Ask（seam fallback） | 继续 / – |
| SceneWorkspace.cs:209 | 删除变体 | 删除 / danger |
| SceneWorkspace.cs:266 | 归档场景（可恢复） | 归档 / – |
| ScriptView.cs:420 | 删除剧本 | 删除 / danger |
| ScriptView.cs:504 | 离开确认（章节切换丢弃） | 切换 / – |
| SettingsView.cs:89 | 离开确认（seam fallback） | 离开 / – |
| SettingsView.cs:493 | 丢弃草稿确认（seam fallback） | 丢弃 / danger |
| SourceView.cs:249 | 修改原文（覆盖输入） | 覆盖 / – |
| SourceView.cs:284 | 取消修改（丢弃文本） | 丢弃 / danger |
| SourceView.cs:396 | 删除章节（可撤回） | 删除 / danger |
| SourceView.cs:483 | 离开确认 | 离开 / – |
| StoryboardView.cs:1504 | 删除气泡（seam fallback） | 删除 / danger |
| StoryboardView.cs:1784 | 离开确认（几何草稿丢失） | 离开 / – |
| StyleProductionCard.cs:52 | 重新载入（放弃色板修改） | 放弃 / danger |
| StyleWorkspace.cs:207 | 更改参考用途 | 继续 / – |
| StyleWorkspace.cs:220 | 删除参考页 | 删除 / danger |
| WorkflowView.cs:851 | 恢复版本（覆盖草稿，seam fallback） | 恢复 / danger |
| WorkflowView.cs:2449 | 离开确认（保存失败放弃） | 放弃并离开 / danger |

## 保留（36 处，纯信息 / 错误提示，OK 单钮）

App.xaml.cs:50；MainWindow.xaml.cs:728,762；ProjectSettingsView.cs:433,457；ScriptView.cs:373,430,445,931；SettingsView.cs:1517；SourceView.cs:414,445；StoryboardView.cs:1130,1350,1404,1441,1481,1526,1558,1615,2325；UsageView.cs:817,828,832；ViewKit.cs:150；WorkflowView.cs:884,890,1689,1694,1725,1750,1794,1802,1827,1918,1964。

## 测试缝保留

迁移只替换 seam 的 fallback 分支，headless 检查缝原样保留：`CharacterPackagePane.DestructiveConfirmOverride / PublishConfirmOverride / SpecLeaveConfirmOverride`、`LocalEditWindow.CloseConfirmOverride`、`ReferenceWorkspace.Confirm`、`SettingsView.LeaveConfirmOverride / DiscardDraftsConfirmOverride`、`StoryboardView.DeleteConfirmOverride / LeaveConfirmOverride`、`GenerateView.LeaveConfirmOverride`、`WorkflowView.RestoreConfirmOverride / SaveFailLeaveOverride`、`ProjectSettingsView.LeaveConfirmOverride`。

## 回归

- 新增 `NativeButtonChecks.ConfirmDialogKeyboardChecks`：初始焦点=取消（安全钮）；Esc 处理并 `DialogResult=false` 关闭；Tab 从取消可达确认；danger 确认钮用 `DangerButton` 样式；确认钮无 `IsDefault`（回车不触发危险确认）。
- 原生全套 56 项全绿（含全部迁移后的交互路径与 seam 驱动的保存/删除/离开检查），对照 NUI-8 基线 56 项无漂移。
