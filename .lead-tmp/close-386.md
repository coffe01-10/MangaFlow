**已修复（round 3 集成批次，组长直修）**：`NativeInteractionChecks.cs` 调度链改为 `await NativeWorkflowRunChecks.Run();`（并留注释说明该处 Task 未 await 的吞故障机制与其余 void 签名调用无害的原因）。

- 提交：42daac5。
- 连带：修复代理复活该节时发现并修复了节内两个既有缺陷（7699a87）——AsInt 对加宽装箱 double 的恒假断言、Run() 无 FAIL 输出；以及桌面 workflowSelector 切换路径防抖计时器未停导致的同版本二次 PATCH（#342 残留，加 `autosave?.Stop()`）。另组长迁移 NativeAssetsLoopChecks 的参考卡查找到 Button 缩略图结构（c6ef82e）——旧查找在参考页重做后必失败，此前被本 issue 的吞故障机制掩盖。
- 验证：`dotnet ... --render` 54 项全过，其中 workflow inspector 一节的 PASS 行首次真实出现；异常栈经 NativeInteractionChecks:53 正常传播（本轮曾靠它定位 assets-loop 失败点）。
- NOT RUN：真实 WER 场景、其他 fire-and-forget 调用面扫描（本链其余未 await 调用已逐一核对签名均为 void）。
