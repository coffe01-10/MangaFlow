# 用量与成本看板：本轮 UI 还原

日期：2026-09-14。基线 d11d55b143c87c7dc0263b9b0000fb70c88c63ef。
本轮开始时系统设置页的改动仍未提交，已保留；本轮新增和修改范围为 UsageView、UsageLayout、UsageTrend、UsageTheme 及对应测试入口。

对照网页 settings/usage/page.tsx 和 components/usage：
- 固定顶部标题与设置/项目导航；筛选项带可见标签，操作按钮成组排列。
- 四张统计卡：调用总览、估算支出、账单支出、Token 与生图。窄窗口四列→两列→单列。
- 原生 Canvas 趋势支持估算金额 / Token / 生图三种度量，颜色图例、悬停数据、矩形展开按钮和图表数据表。金额按币种独立刻度，账单不进入估算；Token 按供应商分组；缺失日期填无调用的零，已调用但未测量的数据保持未知。
- 模型分解、调用明细和账单记录改为真正的原生列布局与表头，表格内部横向滚动，不再靠等宽空格拼接。多币种金额分行，调用明细显示 Token、图片、换路标记和详情入口。
- 预算表单补齐标签，改为可换行布局；重建表单时正确解除旧父节点，避免 WPF 单父节点异常。
- 未改后端、查询契约、CSV 文件写入逻辑或供应商调用。

## 验证

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll --usage-page output/usage-page-review/screenshots
```

最终构建成功：0 errors，仍有 warnings。
专项测试通过：1440/1240/760/360 DIP 的卡片/标题/表格边界，三种趋势切换，多币种隔离、日历空档和未知值，预算编辑/保存，分页失败保留数据及重试追加，通道请求、非法日期阻止请求，空/错/重载状态，导航回调，详情记录绑定，已有 CSV 公式前缀保护。
同时复用 NativeConsistencyChecks 的 UsageBudgetChecks 与 UsageRenderChecks，保持原有预算、Decimal 字符串和模型分解语义回归。

screenshots/ 是最终离屏 WPF 绘制截图，数据来自 HTTP fixture。没有读取用户凭据或实际用量数据。
未运行：真实后端持久化/集成、CSV 保存对话框及文件写入、详情模态窗口交互、实体高 DPI 窗口和动画帧率验收。不能据此声明全功能一比一完成。

发现的旧查询和导出问题已按用户要求交接，见 handoff-prompt.md。

