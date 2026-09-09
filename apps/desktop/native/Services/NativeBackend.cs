using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace MangaFlow.Native.Services;

public sealed class NativeBackend(string repository, string userData)
{
    private Process? process;
    private bool started;
    private readonly SemaphoreSlim stopLock = new(1, 1);
    private readonly StringBuilder errors = new();
    public bool IsRunning => process is { HasExited: false };

    /// <summary>Host executable the next StartAsync will load (chosen at start).</summary>
    public string HostPath { get; private set; } = "";
    /// <summary>SHA-256 of that host file at start; empty when unreadable.</summary>
    public string HostSha256 { get; private set; } = "";

    private static string Sha256File(string path)
    {
        try
        {
            using var stream = File.OpenRead(path);
            return Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(stream));
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            return "";
        }
    }

    public async Task<string> StartAsync(CancellationToken cancellation)
    {
        if (process != null) throw new InvalidOperationException("旧服务尚未停止，请等待后重试");
        var host = Path.Combine(AppContext.BaseDirectory, "native-host.exe");
        if (!File.Exists(host)) host = Path.Combine(repository, "apps", "desktop", "shell-core", "target", "debug", "native-host.exe");
        HostPath = host;
        HostSha256 = Sha256File(host);
        var python = Environment.GetEnvironmentVariable("MANGAFLOW_DESKTOP_PYTHON")
            ?? Path.Combine(repository, ".venv-desktop", "Scripts", "python.exe");
        if (!File.Exists(host) || !File.Exists(python))
            throw new FileNotFoundException("缺少本地服务程序或 Python 环境。请运行 start-native.ps1 构建后再启动。");
        Directory.CreateDirectory(userData);
        var info = new ProcessStartInfo(host)
        {
            WorkingDirectory = repository, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
        };
        info.Environment["MANGAFLOW_DESKTOP_PYTHON"] = python;
        info.Environment["MANGAFLOW_DESKTOP_HELPER"] = Path.Combine(repository, "apps", "desktop", "sidecar", "mangaflow_desktop_helper.py");
        info.Environment["MANGAFLOW_DESKTOP_API_ROOT"] = Path.Combine(repository, "apps", "api");
        info.Environment["MANGAFLOW_DESKTOP_USER_DATA"] = userData;
        // Inherited development flags must not silently start a fake provider.
        info.Environment.Remove("MANGAFLOW_NATIVE_TEST_STUB");
        errors.Clear();
        process = new Process { StartInfo = info };
        process.ErrorDataReceived += (_, e) =>
        {
            if (e.Data == null) return;
            lock (errors)
            {
                errors.AppendLine(e.Data);
                if (errors.Length > 6000) errors.Remove(0, errors.Length - 6000);
            }
        };
        try
        {
            started = process.Start();
            process.BeginErrorReadLine();
            var line = await process.StandardOutput.ReadLineAsync(cancellation).AsTask()
                .WaitAsync(TimeSpan.FromSeconds(35), cancellation);
            try
            {
                return ParseReadyOrigin(line);
            }
            catch (InvalidOperationException error)
            {
                if (process.HasExited) await process.WaitForExitAsync(CancellationToken.None);
                string reason;
                lock (errors) reason = errors.ToString();
                throw new InvalidOperationException(error.Message + "\n" + reason);
            }
        }
        catch
        {
            await StopAsync();
            throw;
        }
    }

    /// Parse the native-host READY line into the API origin. #276: the parse
    /// site itself asserts the loopback http rule — ApiClient re-validates in
    /// its constructor, but a caller that skips that rule must still never
    /// receive a foreign origin. Every malformed shape (no prefix, non-JSON
    /// payload, missing/null api_origin, non-loopback origin) fails closed as
    /// InvalidOperationException.
    internal static string ParseReadyOrigin(string? line)
    {
        const string prefix = "MANGAFLOW_NATIVE_READY ";
        if (line is null || !line.StartsWith(prefix, StringComparison.Ordinal))
            throw new InvalidOperationException("本地服务未能启动。");
        try
        {
            using var ready = JsonDocument.Parse(line[prefix.Length..]);
            // GetProperty on a non-object root throws its own raw
            // InvalidOperationException — normalize it into the same friendly
            // wrap as the other malformed shapes.
            if (ready.RootElement.ValueKind != JsonValueKind.Object)
                throw new InvalidOperationException("本地服务启动信息格式异常。");
            var origin = ready.RootElement.GetProperty("api_origin").GetString()
                ?? throw new InvalidOperationException("服务未返回连接地址");
            return IsTrustedLoopback(origin)
                ? origin
                : throw new InvalidOperationException($"服务返回了不可信的连接地址：{origin}");
        }
        catch (Exception error) when (error is JsonException or KeyNotFoundException)
        {
            throw new InvalidOperationException("本地服务启动信息格式异常。", error);
        }
    }

    // Mirrors ApiClient's constructor trust rule (scheme/host/port/path/query/
    // fragment/userinfo) without throwing on arbitrary input.
    private static bool IsTrustedLoopback(string origin)
    {
        if (!Uri.TryCreate(origin, UriKind.Absolute, out var uri)) return false;
        return uri.Scheme == "http" && uri.Host == "127.0.0.1" && uri.Port > 0
            && uri.AbsolutePath == "/" && uri.Query.Length == 0
            && uri.Fragment.Length == 0 && uri.UserInfo.Length == 0;
    }

    public async Task StopAsync()
    {
        await stopLock.WaitAsync();
        try
        {
            if (process == null) return;
            try
            {
                // No PID-based kill: only close our private lifetime pipe.
                if (started)
                {
                    if (!process.HasExited) process.StandardInput.Close();
                    await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(40));
                }
            }
            catch (InvalidOperationException) when (!started) { }
            process.Dispose();
            process = null;
            started = false;
        }
        finally { stopLock.Release(); }
    }
}
