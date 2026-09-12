using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Reflection;
using MangaFlow.Native;
using MangaFlow.Native.Services;

// #411 / #446 / #449-1 的回归检查（进程与文件层，无 WPF 视觉树依赖）：
// - #411：StopAsync 的优雅停止超时必须按决策表升级为整树 Kill 并复位
//   process/started（否则后端永久卡死，每次重连都再等 40 秒）；
// - #446：Preferences.Save 对真实不可写路径（目录占位 / 只读文件 / 根是文件）
//   不得抛出——它被 async-void 的 OnClosing / HideDock / ShowDock 调用，
//   逃逸会击穿进程并跳过优雅停服；
// - #449-1：启动挂起（READY 等待超时）必须映射到自己的启动超时文案，
//   不得复用「提交…避免重复提交」的提交冲突语义（此刻没有任何提交）。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：建议在
// NativeInteractionChecks.RunIsolated 的 await 链追加 `NativeIssue411Checks.Run();`
// （本检查不依赖 WPF，也可在 Program.cs 直接调用）。
internal static class NativeIssue411Checks
{
    public static void Run()
    {
        StopEscalationDecisionTable();
        // 必须走线程池：StopAsync 的 await 续体会回到捕获的 SynchronizationContext——
        // 在 RunIsolated 的 Dispatcher 上下文里直接 GetResult() 会把续体锁死在
        // 被阻塞的调度线程上（死锁）。Task.Run 使续体留在无线程亲和的池线程。
        Task.Run(StopAsyncEscalatesAndResets).GetAwaiter().GetResult();
        PreferencesSaveSurvivesUnwritablePaths();
        StartupTimeoutCopy();
        Console.WriteLine(
            "PASS: #411 stop-timeout escalates to a tree kill and resets backend state; "
            + "#446 preferences save failures never crash; #449 boot-hang copy never mentions submits");
    }

    private static void StopEscalationDecisionTable()
    {
        Require(NativeBackend.PlanStopEscalation(started: true, timedOut: true)
            == NativeBackend.StopEscalation.KillTree, "已启动且优雅停止超时：必须升级为整树 Kill");
        Require(NativeBackend.PlanStopEscalation(started: true, timedOut: false)
            == NativeBackend.StopEscalation.None, "优雅停止已完成：不得再 Kill");
        Require(NativeBackend.PlanStopEscalation(started: false, timedOut: true)
            == NativeBackend.StopEscalation.None, "未启动的进程：任何情况都不得 Kill");
        Require(NativeBackend.PlanStopEscalation(started: false, timedOut: false)
            == NativeBackend.StopEscalation.None, "空闲后端：保持原状");
    }

    // 真实顽固进程：ping 完全不理会 stdin 生命管道，优雅关闭必然超时，
    // 逼出升级路径（而非模拟异常）。1 秒窗口替代 40 秒默认值以控制时长。
    private static async Task StopAsyncEscalatesAndResets()
    {
        var backend = new NativeBackend("",
            Path.Combine(Path.GetTempPath(), "mangaflow-issue411-" + Guid.NewGuid().ToString("N")));
        var type = typeof(NativeBackend);
        var processField = type.GetField("process", BindingFlags.Instance | BindingFlags.NonPublic)!;
        var startedField = type.GetField("started", BindingFlags.Instance | BindingFlags.NonPublic)!;
        var stubborn = Process.Start(new ProcessStartInfo("ping.exe", "-n 60 127.0.0.1")
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
        })!;
        var pid = stubborn.Id;  // StopAsync 会 Dispose 该 Process 对象，先取 PID
        try
        {
            processField.SetValue(backend, stubborn);
            startedField.SetValue(backend, true);
            var watch = Stopwatch.StartNew();
            await backend.StopAsync(TimeSpan.FromSeconds(1));
            watch.Stop();
            Require(watch.Elapsed < TimeSpan.FromSeconds(20), "升级停止远超优雅窗口（" + watch.Elapsed + "）");
            Require(processField.GetValue(backend) is null && startedField.GetValue(backend) is false,
                "超时的停止必须复位 process/started，否则重连永远无法重新拉起服务");
            var exited = false;
            try { using var gone = Process.GetProcessById(pid); exited = gone.HasExited; }
            catch (ArgumentException) { exited = true; }
            Require(exited, "顽固的后端进程在升级停止后仍然存活");
        }
        finally
        {
            // StopAsync 已 Dispose 该 Process 对象（HasExited 从此抛异常），
            // 兜底清理改走 PID：仍在运行才补一刀。
            try
            {
                using var leftover = Process.GetProcessById(pid);
                if (!leftover.HasExited) leftover.Kill();
            }
            catch (ArgumentException) { }  // 已退出：无需兜底
            stubborn.Dispose();
        }
    }

    private static void PreferencesSaveSurvivesUnwritablePaths()
    {
        var root = Path.Combine(Path.GetTempPath(), "mangaflow-issue446-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            // 目标被同名目录占用：File.Move(overwrite:true) 失败。
            Directory.CreateDirectory(Path.Combine(root, "window.json"));
            new Preferences { Width = 777 }.Save(root);
            Require(Directory.GetFiles(root, "*.tmp").Length == 0, "失败的保存不得留下临时文件");

            // 目标只读：覆盖式 Move 被拒绝。
            var destination = Path.Combine(root, "window.json");
            Directory.Delete(destination);
            File.WriteAllText(destination, "{}");
            File.SetAttributes(destination, FileAttributes.ReadOnly);
            try { new Preferences { Width = 888 }.Save(root); }
            finally { File.SetAttributes(destination, FileAttributes.Normal); }

            // 根路径本身是文件：Directory.CreateDirectory 失败。
            var fileRoot = Path.Combine(root, "not-a-directory");
            File.WriteAllText(fileRoot, "x");
            new Preferences { Width = 999 }.Save(fileRoot);
        }
        finally { Directory.Delete(root, true); }
    }

    private static void StartupTimeoutCopy()
    {
        var boot = NativeBackend.WrapStartupFailure(new TimeoutException());
        Require(boot is InvalidOperationException && boot.Message == NativeBackend.StartupTimeoutText,
            "启动挂起必须被包成带专属文案的异常");
        Require(!boot.Message.Contains("提交"), "启动超时文案不得出现提交语义");
        var passthrough = new HttpRequestException("连接中断");
        Require(ReferenceEquals(NativeBackend.WrapStartupFailure(passthrough), passthrough),
            "非超时的启动失败必须保留原始异常");

        // 外壳映射：真实请求超时仍保留提交冲突文案；启动超时文案原样直达错误位。
        var errorText = typeof(MainWindow).GetMethod("ErrorText", BindingFlags.Static | BindingFlags.NonPublic)!;
        Require(((string)errorText.Invoke(null, [new TimeoutException()])!).Contains("提交"),
            "请求超时的提交冲突文案不得被误改");
        Require((string)errorText.Invoke(null, [boot])! == NativeBackend.StartupTimeoutText,
            "启动超时文案必须原样穿过 ErrorText 到达错误位");
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }
}
