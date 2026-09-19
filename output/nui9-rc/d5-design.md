# D5 全局设置双头部修复设计（NUI-9 P2-1）

依据：NUI-8 台账 §P1-2 D5 行的既定方案；web 对照 `apps/web/app/settings/page.tsx:126-133`。

## 现状

- web：**单一条** `topbar.settings-topbar`——kicker `SYSTEM / CONTROL ROOM` + 标题「系统设置与运行诊断」+ 右侧三动作（用量与成本看板 ghost ／ ←返回项目 ghost ／ 保存运行设置 ink，`disabled={!draft || save.isPending}`）。
- WPF：**两条头部**——壳顶栏（BrandKicker `SYSTEM / CONTROL ROOM` + TopTitle「系统设置」+ `SettingsActions` 三按钮，`MainWindow.xaml:50-54`）与页面自绘头部（`SettingsLayout.cs` BuildSystemPage 的 header Border：同 kicker + 标题「系统设置与运行诊断」+ 同三按钮）。
- 保存中禁用态挂在页面按钮 `runtimeSave`（`SettingsView.cs:702/780`）；壳保存按钮只绑 `Connected`（xaml:53）。

## 决策

1. **动作所有权归壳**：`SettingsActions` 独占三动作；视图删除整个 header Border（含 usage/back/runtimeSave 的页面副本与 actions WrapPanel）。
2. **事件还是回调：事件**（沿用既有 `RuntimeSaved` 事件模式）——SettingsView 新增 `internal event Action? RuntimeSavingChanged` 与 `internal bool RuntimeSaving`，`SaveRuntime` 在置位/清位 `runtimeSaving` 处（702/780）raise。理由：saving 只有 SaveRuntime 会改，事件沿该路径即时同步、无轮询间隙；壳订阅处（NavigateAsync 的 SettingsView 分支，`MainWindow.xaml.cs:214-220`，与 RuntimeSaved 同址 -=/+= 幂等重绑）。
3. **壳侧降级路径**：
   - 处理器以 `ContentHost.Content is SettingsView` 为守卫：非设置页/视图未挂时不碰按钮状态（cached view 的陈旧订阅无副作用）。
   - `SaveRuntimeSettings()` 在 ContentHost.Content 非 SettingsView 时 no-op（现状保留）。
   - 离开设置页不再显式复位：按钮态由下次进入设置页时订阅块里的 `OnRuntimeSavingChanged()` 重算。
   - `runtimeSave` 字段保留为非视觉对象（SaveRuntime 的 sender/IsEnabled 语义锚点），不再进视觉树；离屏检查仍用 `Field<Button>("runtimeSave")` 驱动。
4. **标题统一**：`UpdateChrome` 的 TopTitle 与 Breadcrumb 在 settings-global 分支改「系统设置与运行诊断」（web strong 文案）；kicker 不变（web 相同）。壳保存按钮 xaml 移除 `IsEnabled="{Binding Connected}"`（避免绑定与本地值冲突），加 `x:Name="SaveRuntimeButton"`，由代码管理 `IsEnabled = Connected && !RuntimeSaving`。

## 回归

- 离屏像素回归（`NativeSystemSettingsPageChecks`）：
  1. 视觉树不再含「系统设置与运行诊断」／「SYSTEM / CONTROL ROOM」文本（页面不自绘头部）；
  2. 视觉树不再含「用量与成本看板」／「返回项目」按钮（动作归壳）；
  3. gated save 期间 `RuntimeSavingChanged` 序列 == [true, false]（壳禁用的数据源）；
  4. 渲染 1440 PNG 留档；替换原 `save.TranslatePoint().Y < 150` 断言（runtimeSave 脱离视觉树后该断言恒真，不保留恒真断言）；
  5. 删除原页面「用量与成本看板／返回项目」点击接线断言（按钮已删；导航接线壳侧已由 NavigateAsync→ShowUsage/ShowHome 承担，带 ConfirmLeaveAsync）。
- 交互回归（真机，P4 格补跑）：设置页真机截图只有壳一条头部；保存中壳按钮禁用。
- 全量门禁（npm run check / dotnet Release / --render 56 项）在 P7 收口统一跑。

## 自审记录

- 疑点：删头部后 `PageHeading` 的 Fits 断言（checks:68/103）——只遍历剩余卡片头，不受影响。
- 疑点：`runtimeSave.Click += SaveRuntime` 保留（离屏检查 `Click(save)` 依赖该订阅驱动保存路径）。
- 疑点：viewCache 缓存的 SettingsView 带着旧订阅——处理器有 ContentHost 守卫，陈旧事件不改 UI。
- 疑点：settings-global 页失去页内返回/用量入口——壳按钮即唯一入口且语义等价（都走带离开确认的导航）。
- 已知边界：壳三行胶水（订阅+处理器）无 MainWindow 级离屏测试（现有检查从不构造 MainWindow）；由真机 P4 格覆盖，避免为本 PR 新建重量级壳夹具。
