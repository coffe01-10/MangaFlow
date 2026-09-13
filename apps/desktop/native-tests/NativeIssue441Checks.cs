using System.IO;

// #441 回归检查（纯源码扫描，无 WPF/网络依赖）：
// ApiCache/QueryEntry（React-Query 式去重 + 前缀失效缓存）从未被写入——entries
// 恒为空，全部 Cache.Invalidate 调用都是装饰性 no-op，数据新鲜度实际由每个
// mutator 的显式重载保证。#441 已删除这层死抽象，本检查防止它悄悄回潮：
// ① apps/desktop/native 下任何源文件不得再引用 ApiCache / Cache.Invalidate
//    （字段、DI、测试缝、包装方法等任何形式都会命中字符串扫描）；
// ② 代表性 mutator→reload 配对必须存活：JobsView 的单任务与批量操作成功后
//    必须显式 await LoadAsync()——删掉重载（误以为缓存会兜底）同样失败。
// 运行方式：native-tests 以 --issue441 独立运行，且已接入
// NativeInteractionChecks.RunIsolated 的调用链（默认 --render 链的一部分）。
internal static class NativeIssue441Checks
{
    public static void Run()
    {
        var repo = FindRepoRoot() ?? throw new Exception("#441 找不到仓库根（apps/desktop/native 所在地）");
        var nativeRoot = Path.Combine(repo, "apps", "desktop", "native");
        if (!Directory.Exists(nativeRoot)) throw new Exception("#441 缺少 " + nativeRoot);
        NoCacheReferencesRemain(nativeRoot);
        MutatorsStillReloadExplicitly(nativeRoot);
        Console.WriteLine("PASS: #441 native sources reference no ApiCache/Cache.Invalidate; JobsView mutators keep their explicit post-mutation reloads");
    }

    // ① 字符串级扫描：编译期引用（类型名/调用）必然以字面量出现在源文本里。
    // 跳过 bin/obj——本地陈旧的构建产物里可能仍带着旧类型的元数据字符串，
    // 它们不是源码，git 也不跟踪。
    private static void NoCacheReferencesRemain(string nativeRoot)
    {
        foreach (var file in Directory.EnumerateFiles(nativeRoot, "*", SearchOption.AllDirectories))
        {
            var segments = new DirectoryInfo(Path.GetDirectoryName(file)!).FullName
                .Split([Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar]);
            if (segments.Contains("bin") || segments.Contains("obj")) continue;
            string text;
            try { text = File.ReadAllText(file); }
            catch (Exception) { continue; }   // 二进制资源等不可读文件不可能承载 C# 引用
            Require(!text.Contains("ApiCache"), "#441 " + Relative(nativeRoot, file) + " 仍引用 ApiCache");
            Require(!text.Contains("Cache.Invalidate"), "#441 " + Relative(nativeRoot, file) + " 仍调用 Cache.Invalidate");
        }
    }

    // ② 按方法切片钉住「mutation 成功 → 显式重载」的顺序配对：切片里必须同时
    // 出现请求文本与 await LoadAsync()。删除重载或把重载挪出成功路径都会失败。
    private static void MutatorsStillReloadExplicitly(string nativeRoot)
    {
        var jobs = File.ReadAllText(Path.Combine(nativeRoot, "Views", "JobsView.cs"));

        var single = MethodBody(jobs,
            "private async Task JobAction(JobItem job, string action, string label, bool useDelete = false)",
            "private async void ArchiveAllCompleted");
        Require(single.Contains("jobs/{job.Id}/{action}"), "#441 JobsView.JobAction 不再包含单任务变更请求（切片锚点失效请同步本检查）");
        Require(single.Contains("await LoadAsync();"), "#441 JobsView.JobAction 变更成功后必须显式重载列表（#441 之后没有缓存兜底）");

        var bulk = MethodBody(jobs,
            "private async Task BulkAction(string action, object? payload, Func<JsonElement, string> message)",
            "private void UpdateActionAvailability");
        Require(bulk.Contains("projects/{projectId}/jobs/{action}"), "#441 JobsView.BulkAction 不再包含批量变更请求（切片锚点失效请同步本检查）");
        Require(bulk.Contains("await LoadAsync();"), "#441 JobsView.BulkAction 变更成功后必须显式重载列表（#441 之后没有缓存兜底）");
    }

    private static string MethodBody(string source, string startMarker, string endMarker)
    {
        var start = source.IndexOf(startMarker, StringComparison.Ordinal);
        Require(start >= 0, "#441 源切片找不到起始锚点：" + startMarker);
        var end = source.IndexOf(endMarker, start, StringComparison.Ordinal);
        Require(end > start, "#441 源切片找不到结束锚点：" + endMarker);
        return source[start..end];
    }

    private static string Relative(string root, string file) => file[root.Length..].TrimStart(Path.DirectorySeparatorChar);

    // NativeIssue410Checks 同款：从测试程序集目录与当前目录各向上探测一次，
    // 以 apps/desktop/native/MangaFlow.Native.csproj 为仓库根标志。
    private static string? FindRepoRoot()
    {
        foreach (var start in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            for (var dir = new DirectoryInfo(start); dir != null; dir = dir.Parent)
                if (File.Exists(Path.Combine(dir.FullName, "apps", "desktop", "native", "MangaFlow.Native.csproj")))
                    return dir.FullName;
        }
        return null;
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }
}
