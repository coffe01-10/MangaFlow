using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using MangaFlow.Native;
using MangaFlow.Native.Services;

// #276 pins two previously untested security-relevant behaviors:
// 1) the native-host READY line fails closed on every malformed shape, with
//    the loopback trust rule asserted at the parse site itself;
// 2) the MainWindow poll flips the disconnect banner exactly when the owned
//    backend process is gone — and never while it is alive.
internal static class NativeBackendChecks
{
    private const string Prefix = "MANGAFLOW_NATIVE_READY ";

    public static async Task Run()
    {
        ReadyLineParsing();
        await DisconnectBanner();
        Console.WriteLine("PASS: READY-line parse fails closed (prefix/payload/origin rule); disconnect banner follows backend liveness");
    }

    private static void ReadyLineParsing()
    {
        Require(
            NativeBackend.ParseReadyOrigin(Prefix + """{"api_origin":"http://127.0.0.1:8901"}""") == "http://127.0.0.1:8901",
            "a valid READY line was rejected");
        var malformed = new (string? Line, string Name)[]
        {
            (null, "empty stdout"),
            ("", "no payload"),
            ("MANGAFLOW_NATIVE_READY", "prefix without payload"),
            (Prefix + "not-json", "non-JSON payload"),
            (Prefix + """{"health":"http://127.0.0.1:1"}""", "missing api_origin"),
            (Prefix + """{"api_origin":null}""", "null api_origin"),
            (Prefix + """{"api_origin":"https://127.0.0.1:8901"}""", "non-http scheme"),
            (Prefix + """{"api_origin":"http://localhost:8901"}""", "localhost host"),
            (Prefix + """{"api_origin":"http://example.com"}""", "foreign host"),
            // Note: the default-port form "http://127.0.0.1" (no explicit port) is
            // deliberately NOT in this list — Uri reports it as port 80, and the
            // parse-site rule mirrors ApiClient's constructor exactly, so it is
            // trusted there too.
            (Prefix + """{"api_origin":"http://127.0.0.1:8901/path"}""", "path suffix"),
            (Prefix + """{"api_origin":"http://127.0.0.1:8901?x=1"}""", "query suffix"),
            (Prefix + """{"api_origin":"http://u:p@127.0.0.1:8901"}""", "userinfo"),
            ("""{"api_origin":"http://127.0.0.1:8901"}""", "missing prefix"),
            ("mangaflow_native_ready " + """{"api_origin":"http://127.0.0.1:8901"}""", "case-mangled prefix"),
        };
        foreach (var (line, name) in malformed)
        {
            try { NativeBackend.ParseReadyOrigin(line); throw new Exception("accepted malformed READY line: " + name); }
            catch (InvalidOperationException) { }
        }
    }

    private static async Task DisconnectBanner()
    {
        var root = Path.Combine(Path.GetTempPath(), "mangaflow-backend-banner-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var shell = new MainWindow("", root);
            var state = (WorkspaceState)shell.DataContext;
            var shellType = typeof(MainWindow);
            var backendField = shellType.GetField("backend", BindingFlags.Instance | BindingFlags.NonPublic);
            var processField = backendField?.FieldType.GetField("process", BindingFlags.Instance | BindingFlags.NonPublic);
            var poll = shellType.GetMethod("Poll", BindingFlags.Instance | BindingFlags.NonPublic);
            Require(backendField != null && processField != null && poll != null,
                "反射入口缺失：MainWindow backend/process/Poll");
            var backend = backendField!.GetValue(shell)!;

            // Live backend: a connected session must stay untouched by the poll.
            state.Connected = true;
            state.ConnectionLabel = "● 本地服务已连接";
            processField!.SetValue(backend, Process.GetCurrentProcess());
            poll!.Invoke(shell, new object?[] { null, EventArgs.Empty });
            Require(state.Connected && state.ConnectionLabel == "● 本地服务已连接",
                "a poll tick with a live backend disturbed the connected session");

            // Backend process exited: one tick must flip the banner and surface guidance.
            var exited = Process.Start(new ProcessStartInfo("cmd.exe", "/c exit")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
            });
            exited.WaitForExit();
            processField.SetValue(backend, exited);
            poll.Invoke(shell, new object?[] { null, EventArgs.Empty });
            Require(!state.Connected, "an exited backend did not disconnect the session");
            Require(state.ConnectionLabel == "本地服务已断开", "disconnect banner label diverged");
            Require(state.Error.Contains("本地服务已退出") && state.Error.Contains("重新连接"),
                "disconnect banner lost the reconnect guidance");
            exited.Dispose();

            // Backend reference dropped entirely: still fail closed.
            state.Connected = true;
            state.ConnectionLabel = "● 本地服务已连接";
            processField.SetValue(backend, null);
            poll.Invoke(shell, new object?[] { null, EventArgs.Empty });
            Require(!state.Connected && state.ConnectionLabel == "本地服务已断开",
                "a null backend process did not fail closed");
        }
        finally { Directory.Delete(root, true); }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }
}
