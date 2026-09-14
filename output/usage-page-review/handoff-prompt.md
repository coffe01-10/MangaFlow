请修复 MangaFlow WPF「用量与成本看板」的既有查询/导出差距，保留本轮 UI 布局。

仓库 D:\自媒体\漫画工作流；基线 d11d55b143c87c7dc0263b9b0000fb70c88c63ef。开始前检查工作区，系统设置页和用量页有未提交修改；不得覆盖或回滚。

重点文件：
- apps/desktop/native/Views/UsageView.cs
- apps/desktop/native/Views/UsageLayout.cs、UsageTrend.cs、UsageTheme.xaml（保留新 UI）
- apps/desktop/native/Services/UsageFilter.cs
- 对照 apps/web/components/usage/usage-dashboard.tsx、usage-format.ts
- 只读核对 apps/api/app/api/routes/usage.py

以下是源码核查结果，未声称真实服务复现：

1. 通道筛选没有进入汇总请求
   UsageFilter.SummaryPath 只发送 from/to/project_id/provider/model_id，没有 channel；AttemptsPath 发送 channel。当前后端 get_usage_summary 已支持 channel，网页也把 channel 传入汇总。因此选择 CLI 时调用明细会变化，但统计卡/趋势/预算仍可能混合其他通道。
   验收：两种通道各有独立用量与金额；选择 CLI 后汇总与分页均带 channel=CLI，显示的数据集一致。更新旧测试中“summary 不含 channel”的过时断言；不要修改后端来迎合旧客户端契约。

2. CSV 未包含账单对账记录
   UsageView.ExportCsv 只遍历 summary.groups，没有导出 summary.billed；只有账单时也只写一个估算表头。网页 buildUsageCsv 有账单记录第二区块。另有金额 ToString() 使用当前文化而非固定格式，需在逗号小数文化下验证。
   验收：只有账单、估算+账单、多币种、中文、逗号/换行/双引号和公式前缀输入。导出每种币种独立行，账单事实不与估算相加，未知不写成零；确保 CSV 列数量和小数格式稳定。先抽取纯序列化函数测试，再在用户指定输出位置验证真实保存；不要覆写用户现有文件。

3. 供应商/模型选项不是独立维度来源
   LoadAsync 从当前已筛选 summary.groups 增量 AddChoices，选项只增不清；没有像网页 facets 那样独立查询，也没有按供应商过滤模型选项。可能缺少其他日期/项目的选项或留下不相关模型。
   验收：修改时间/项目/供应商后维度选项正确、稳定，供应商切换清除不兼容模型；项目或维度读取失败要可见并能独立重试，不可默认为“没有其他选项”。

4. 初始明细请求失败会阻断已成功汇总
   LoadAsync 对 summaryTask 和 attemptsTask 使用 Task.WhenAll，任一个失败即不渲染另一部分结果。网页允许统计和明细分区反馈错误。分页错误现在也只写页面上方 summaryLine，滚动到底部时不易发现。
   验收：汇总成功/明细失败及反向组合；保留成功分区，失败分区提供有效重试。保留 UsageAttemptFeed 的固定筛选快照、去重和过时响应隔离，不得让旧分页覆盖新筛选。

范围：优先客户端查询与序列化；若真需后端改动，先给证据与独立方案。禁止改数据库事务、Worker、队列、供应商执行。不要运行真实付费生成、读取凭据或删除用户数据；临时文件及时清理。

验证：
- 新增上述失败回归；
- --usage-page；
- NativeBehaviorChecks 中筛选与分页防并发/过时响应测试；
- NativeConsistencyChecks 的预算/Decimal/模型分解检查。
报告精确改动、测试和 NOT RUN 范围，不能把 HTTP mock 或离屏截图当成真实后端全链路验收。

