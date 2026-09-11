**Found:** 第三轮修复代理（wt-e，#380 任务）复活检查时发现，组长已亲自核实签名与调用点（2026-09-11）。

## 机制

`apps/desktop/native-tests/NativeInteractionChecks.cs` 的调度链里：

```csharp
await NativeBackendChecks.Run(); await NativeWorkflowChecks.Run();
NativeWorkflowConnectionChecks.Run(); NativeWorkflowRunChecks.Run();  // ← 未 await
```

`NativeWorkflowRunChecks.Run()` 的签名是 `public static async Task Run()`——唯一一个未 await 的 Task 返回调用。故障 Task 的异常成为未观察异常，**静默吞掉，套件照常绿**。同链其余未 await 的三个（NativeWorkflowConnectionChecks/NativeStoryboardEditChecks/NativeConsistencyChecks）均为 `void` 签名，同步抛错会被外层 try/catch 捕获，无此问题。

后果：#380 修复前，NativeWorkflowRunChecks 的全部断言（含轮询契约钉住）从未真正执行过；其内部真实缺陷（AsInt 对加宽装箱的 double 恒断言失败——代理已随 #380 分支修复）被双重掩盖。`--render` 门禁对该节是**空转绿灯**。

## 修复面

调度链改为 `await NativeWorkflowRunChecks.Run();`（一词修复）；建议顺手全链 grep 一遍其余 `Run(` 调用的 await 配对，钉住不再出现未 await 的 Task 调用（可用警告注释或检查）。
