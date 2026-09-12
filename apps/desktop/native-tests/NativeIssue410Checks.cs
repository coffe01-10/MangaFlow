using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using MangaFlow.Native;

// #410 的回归检查（路径规范化 + 跨客户端互锁，无 WPF 视觉树依赖）：
// - WPF 单实例互斥键曾按【词法】用户数据路径哈希：同一目录的 subst 盘 /
//   junction / 8.3 短名拼法各得一个互斥体 → 两个 WPF 客户端 → 两个原生宿主
//   → 两个 sidecar API 服务器写同一个 SQLite。App.CanonicalizeUserDataPath
//   必须把别名解析到最终路径（GetFinalPathNameByHandle），任何解析失败回退
//   到词法全路径；
// - SingleInstanceKey 的哈希方案被钉死（KAT）：静默换方案会让升级后的新进程
//   与仍在运行的旧实例互不相认，出现双实例；
// - scripts/start-native.ps1 必须拒绝指向外壳（Tauri）应用本地数据目录
//   %LOCALAPPDATA%\com.mangaflow.desktop 内部的 -UserData —— 否则同样产出
//   「两个服务器共写一个库」。
//
// 【需 lead 注册】本文件按任务约束未接入运行入口：本检查不依赖 WPF，可在
// Program.cs 默认链直接调用 NativeIssue410Checks.Run()（junction / 脚本子进程
// 两段仅在 Windows 执行，POSIX 上以明确 SKIP 消息跳过）。
internal static class NativeIssue410Checks
{
    // 固定向量：方案 = SHA256(UTF8(canonical.TrimEnd('\\').ToUpperInvariant()))
    // 的 UPPER-HEX 前 24 字符。该路径并不需要真实存在 —— SingleInstanceKey 是
    // 纯函数，POSIX 上同样可钉。
    private const string KatPath = @"C:\Users\mf\AppData\Local\MangaFlow\Native";
    private const string KatKey = "BEEA5EA9FCB6532EA691DBEF";

    public static void Run()
    {
        KeySchemeStaysStable();
        if (!OperatingSystem.IsWindows())
        {
            Console.WriteLine("SKIP: #410 junction / script-overlap checks require Windows; key-scheme KAT ran above");
            return;
        }
        CanonicalizationResolvesDirectoryAliases();
        CanonicalizationFallsBackToLexical();
        StartupWiresCanonicalKey(FindRepoRoot()
            ?? throw new Exception("#410 找不到仓库根（App.xaml.cs 所在地）"));
        StartNativeScriptRefusesShellDataOverlap();
        Console.WriteLine(
            "PASS: #410 mutex keys canonicalize junction/short-name aliases with lexical fallback; "
            + "key scheme pinned; start-native.ps1 refuses -UserData inside the shell data dir");
    }

    // OnStartup 本体无法无头运行（MessageBox/MainWindow）；接线若被回退成
    // 「词法路径直接取键」，上面的纯函数检查全都会继续通过，因此这里钉住调用点。
    private static void StartupWiresCanonicalKey(string repo)
    {
        var appSource = File.ReadAllText(Path.Combine(repo, "apps", "desktop", "native", "App.xaml.cs"));
        Require(appSource.Contains("SingleInstanceKey(CanonicalizeUserDataPath("),
            "#410 OnStartup 必须先 CanonicalizeUserDataPath 再取单实例键（发现接线回退）");
    }

    private static void KeySchemeStaysStable()
    {
        Require(App.SingleInstanceKey(KatPath) == KatKey,
            "#410 单实例键方案被改动（KAT 不再命中）；静默换方案会孤儿化所有在跑实例");
        Require(App.SingleInstanceKey(KatPath + Path.DirectorySeparatorChar) == KatKey,
            "#410 尾部分隔符不得改变单实例键");
        Require(App.SingleInstanceKey(KatPath.ToLowerInvariant()) == KatKey,
            "#410 大小写差异不得改变单实例键（ToUpperInvariant 语义）");
        // 独立重推导一次文档化方案，防止 KAT 常量本身抄错。
        var expected = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
            KatPath.TrimEnd(Path.DirectorySeparatorChar).ToUpperInvariant())))[..24];
        Require(expected == KatKey, "#410 KAT 向量与文档化方案不符");
    }

    // 真实 junction（%TEMP% 下 mklink /J，无需管理员）+ 真实 8.3 短名（若卷启用）。
    private static void CanonicalizationResolvesDirectoryAliases()
    {
        var root = Path.Combine(Path.GetTempPath(), "mangaflow-issue410-" + Guid.NewGuid().ToString("N"));
        var target = Path.Combine(root, "target-data");
        var other = Path.Combine(root, "other-data");
        var link = Path.Combine(root, "link-to-target");
        Directory.CreateDirectory(target);
        Directory.CreateDirectory(other);
        try
        {
            var mklink = Process.Start(new ProcessStartInfo("cmd.exe",
                "/c mklink /J \"" + link + "\" \"" + target + "\"")
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
            })!;
            var mklinkText = mklink.StandardOutput.ReadToEnd() + mklink.StandardError.ReadToEnd();
            mklink.WaitForExit();
            if (mklink.ExitCode != 0 || !Directory.Exists(link))
                throw new Exception("#410 测试前置失败：mklink /J 建不成 junction：" + mklinkText.Trim());
            Require(!Path.GetFullPath(link).Equals(Path.GetFullPath(target), StringComparison.OrdinalIgnoreCase),
                "#410 测试前置失败：junction 别名与目标必须词法不同");

            var targetCanonical = App.CanonicalizeUserDataPath(target);
            var linkCanonical = App.CanonicalizeUserDataPath(link);
            Require(linkCanonical.Equals(targetCanonical, StringComparison.OrdinalIgnoreCase),
                "#410 同一目录的 junction 别名必须规范化到同一最终路径：" + linkCanonical + " vs " + targetCanonical);
            Require(App.SingleInstanceKey(App.CanonicalizeUserDataPath(link))
                == App.SingleInstanceKey(App.CanonicalizeUserDataPath(target)),
                "#410 同一目录的两种拼法必须得到同一个互斥键（否则双 WPF 客户端、双 sidecar 服务器）");
            Require(App.CanonicalizeUserDataPath(target.ToUpperInvariant())
                .Equals(targetCanonical, StringComparison.OrdinalIgnoreCase),
                "#410 已存在目录的大小写变体必须规范化到盘上真实拼写");
            Require(App.SingleInstanceKey(App.CanonicalizeUserDataPath(other))
                != App.SingleInstanceKey(App.CanonicalizeUserDataPath(target)),
                "#410 两个真实不同的目录必须得到不同的互斥键");

            // 8.3 短名：仅当该卷真的生成了短名才断言（禁用短名的卷上明确跳过）。
            var shortPath = TryGetShortPath(target);
            if (shortPath == null || shortPath.Equals(target, StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine("SKIP: #410 8.3 short-name probe: this volume does not generate short names");
            }
            else
            {
                Require(App.CanonicalizeUserDataPath(shortPath).Equals(targetCanonical, StringComparison.OrdinalIgnoreCase),
                    "#410 同一目录的 8.3 短名拼法必须规范化到同一最终路径：" + shortPath);
            }
        }
        finally
        {
            // rmdir 对 junction 只删链接本身，绝不动目标目录。
            Process.Start(new ProcessStartInfo("cmd.exe", "/c rmdir \"" + link + "\"")
            {
                UseShellExecute = false, CreateNoWindow = true,
            })!.WaitForExit();
            Directory.Delete(root, true);
        }
    }

    private static void CanonicalizationFallsBackToLexical()
    {
        // 连盘符根都不存在的路径：逐层解析必然全败 → 必须原样回退词法全路径。
        var missingRoot = FindUnusedDriveRoot();
        if (missingRoot == null)
        {
            Console.WriteLine("SKIP: #410 lexical-fallback probe: no spare drive letter found");
            return;
        }
        var ghost = Path.Combine(missingRoot, "mangaflow", "issue410", "user-data");
        Require(App.CanonicalizeUserDataPath(ghost) == Path.GetFullPath(ghost),
            "#410 完全不可解析的路径必须回退到词法全路径（不得抛异常、不得返回空）");

        // 尚未创建的叶子挂在【真实】父目录下：必须继承父目录的规范化形态，
        // 这样目录首次创建前后（以及创建前后两次启动）键保持稳定。
        var parent = Path.Combine(Path.GetTempPath(), "mangaflow-issue410-real-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(parent);
        try
        {
            var leaf = Path.Combine(parent, "created-later");
            Require(App.CanonicalizeUserDataPath(leaf).Equals(
                App.CanonicalizeUserDataPath(parent) + Path.DirectorySeparatorChar + "created-later",
                StringComparison.OrdinalIgnoreCase),
                "#410 未创建的叶子必须沿用其真实父目录的最终路径加上未变尾部");
        }
        finally { Directory.Delete(parent); }
    }

    // 两个真实子进程（Windows PowerShell 5 兼容性顺带被实测）：
    // - 拒绝路径：-UserData 落在伪造的 %LOCALAPPDATA%\com.mangaflow.desktop
    //   内 → 脚本必须在任何构建之前非零退出并给出拒绝文案；
    // - 对照路径：目录在外部 → 校验放行（随后被 cargo shim 以非零退出截断，
    //   证明脚本继续执行且未出现拒绝文案）。
    private static void StartNativeScriptRefusesShellDataOverlap()
    {
        var repo = FindRepoRoot() ?? throw new Exception("#410 找不到仓库根（start-native.ps1 所在地）");
        var script = Path.Combine(repo, "apps", "desktop", "scripts", "start-native.ps1");
        if (!File.Exists(script)) throw new Exception("#410 缺少 " + script);

        var root = Path.Combine(Path.GetTempPath(), "mangaflow-issue410-script-" + Guid.NewGuid().ToString("N"));
        var fakeLocal = Path.Combine(root, "LocalAppData");
        var shellDir = Path.Combine(fakeLocal, "com.mangaflow.desktop");
        var shims = Path.Combine(root, "shims");
        Directory.CreateDirectory(shellDir);
        Directory.CreateDirectory(shims);
        File.WriteAllText(Path.Combine(shims, "cargo.cmd"), "@exit /b 1\r\n");
        try
        {
            var inside = RunScript(script, Path.Combine(shellDir, "Native"), fakeLocal, shims);
            Require(inside.ExitCode != 0 && inside.Output.Contains("Refusing -UserData"),
                "#410 start-native.ps1 必须拒绝指向外壳数据目录内部的 -UserData；exit=" + inside.ExitCode
                + " output=" + inside.Output.Trim());
            var outside = RunScript(script, Path.Combine(root, "own-user-data"), fakeLocal, shims);
            Require(outside.ExitCode != 0 && !outside.Output.Contains("Refusing -UserData"),
                "#410 start-native.ps1 不得拒绝外壳数据目录之外的 -UserData；exit=" + outside.ExitCode
                + " output=" + outside.Output.Trim());
        }
        finally { Directory.Delete(root, true); }
    }

    private static (int ExitCode, string Output) RunScript(
        string script, string userData, string fakeLocalAppData, string shims)
    {
        var info = new ProcessStartInfo("powershell.exe",
            "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -UserData \"" + userData + "\"")
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
        };
        info.EnvironmentVariables["LOCALAPPDATA"] = fakeLocalAppData;
        info.EnvironmentVariables["PATH"] = shims + ";" + info.EnvironmentVariables["PATH"];
        using var process = Process.Start(info)!;
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        if (!process.WaitForExit(60000))
        {
            try { process.Kill(); } catch (InvalidOperationException) { }
            throw new Exception("#410 start-native.ps1 子进程超时（60 秒）");
        }
        return (process.ExitCode, stdout.Result + stderr.Result);
    }

    private static string? FindUnusedDriveRoot()
    {
        foreach (var letter in new[] { 'A', 'B', 'Y', 'Z' })
            if (!Directory.Exists(letter + @":\")) return letter + @":\";
        return null;
    }

    private static string? TryGetShortPath(string longPath)
    {
        try
        {
            var buffer = new StringBuilder(260);
            var length = GetShortPathNameW(longPath, buffer, (uint)buffer.Capacity);
            if (length == 0) return null;
            if (length >= (uint)buffer.Capacity)
            {
                buffer = new StringBuilder((int)length + 1);
                length = GetShortPathNameW(longPath, buffer, (uint)buffer.Capacity);
                if (length == 0 || length >= (uint)buffer.Capacity) return null;
            }
            return buffer.ToString();
        }
        catch { return null; }
    }

    private static string? FindRepoRoot()
    {
        // 从测试程序集出发（常规接线）与从当前目录出发（临时/独立运行）各探测一次。
        foreach (var start in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            for (var dir = new DirectoryInfo(start); dir != null; dir = dir.Parent)
                if (File.Exists(Path.Combine(dir.FullName, "apps", "desktop", "scripts", "start-native.ps1")))
                    return dir.FullName;
        }
        return null;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern uint GetShortPathNameW(string longPath, StringBuilder buffer, uint length);

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }
}
