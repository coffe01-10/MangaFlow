using System.Collections.Concurrent;
using System.IO;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using MangaFlow.Native.Services;

/// <summary>
/// NUI-8 缺陷 #4：真机验收中约 60 次快速请求后，工作流保存持续报
/// "An error occurred while sending the request"，而 sidecar /health 正常。
///
/// 机制：sidecar 的 uvicorn 以默认 timeout_keep_alive=5s 空闲回收 keep-alive
/// 连接；客户端连接池对被回收的 socket 无感，下一次请求复用即失败。GET 可重放，
/// SocketsHttpHandler 会透明换新连接；而 PATCH/POST 的 JSON body 一旦写上网络就
/// 不可重放（Program.cs 的既有针「interrupted mutation never automatically
/// retries」是有意为之，避免二次写），所以只有"保存"这一步稳定报错。
///
/// 两种仪器：
/// 1) RunOffline：进程内 TCP 服务器精确模拟「服务端空闲回收」，用生产 ApiClient
///    （不注入 fake handler）复现并在修复后断言零失败；进 CI 门禁。
/// 2) RunLive：对真实 sidecar 重放验收负载（快速突发 + 跨越 5s 空闲窗口），
///    全量异常链落盘，用于实机定因与修复后复测。
/// </summary>
internal static class NativeKeepAliveChecks
{
    public static void RunOffline(string evidenceDir)
    {
        Directory.CreateDirectory(evidenceDir);
        var report = new Report();

        // 120ms 服务端空闲回收 + 400ms 客户端等待：等价于真机 5s/14s 的时间比，
        // 让「连接已被服务端回收」这一状态在两次请求之间确定成立，而非竞态。
        using (var server = new KeepAliveServer(idleMilliseconds: 250))
        {
            var origin = $"http://127.0.0.1:{server.Port}";
            using var api = new ApiClient(origin);

            // 基线：连续快速请求复用同一连接，两侧都不回收，必须全过。
            for (var i = 0; i < 10; i++)
                Mutate(api, "burst", report);

            // 目标场景：每次请求后跨过服务端空闲回收窗口再保存。
            for (var i = 0; i < 10; i++)
            {
                Thread.Sleep(400);
                Mutate(api, "idle-gap-save", report);
            }

            // 对照：同一时序但发 GET（可重放）。GET 通过而 PATCH 失败即证明是
            // 不可重放 body 导致，而非网络或端口耗尽。
            for (var i = 0; i < 10; i++)
            {
                Thread.Sleep(400);
                Read(api, "idle-gap-read", report);
            }

            report.Extra["server_accepted_sockets"] = server.AcceptedSockets;
            report.Extra["server_saw_half_close"] = server.SawCleanClose;
        }

        report.Write(evidenceDir, "keepalive-offline");
        Console.WriteLine($"[keepalive/offline] burst={report.Fmt("burst")} " +
                          $"idle-gap-save={report.Fmt("idle-gap-save")} " +
                          $"idle-gap-read={report.Fmt("idle-gap-read")}");
        if (!report.AllSucceeded)
        {
            Console.WriteLine($"[keepalive/offline] FAILED first chain:\n{report.FirstFailure}");
            throw new Exception("缺陷 #4 复现：空闲回收后的不可重放请求失败");
        }
    }

    public static void RunLive(string origin, string evidenceDir)
    {
        Directory.CreateDirectory(evidenceDir);
        var report = new Report();
        using var api = new ApiClient(origin);

        var workflowId = FirstWorkflow(api);
        if (workflowId is null)
        {
            report.Note = "no workflow in the seeded dataset; live replay needs NUI67-DS1";
            // 并发形态：真实验收时缩略图与任务轮询并行，保存是在几十个并发 GET 中间发出的。
        // 串行复现器证明空闲回收不是根因，所以这一相是定因的关键补充。
        for (var round = 0; round < 8; round++)
        {
            var load = new List<Task>();
            for (var i = 0; i < 40; i++)
                load.Add(Task.Run(() =>
                {
                    try { api.SendAsync("projects").Wait(); Report.OK(report, "concurrent"); }
                    catch (Exception exception) { Report.Fail(report, "concurrent", exception); }
                }));
            load.Add(Task.Run(() => Save(api, workflowId, "concurrent-save", report)));
            Task.WaitAll(load.ToArray());
        }

        report.Write(evidenceDir, "keepalive-live");
            Console.WriteLine("[keepalive/live] SKIPPED: no workflow fixture");
            return;
        }

        for (var i = 0; i < 60; i++)
            Save(api, workflowId, "burst", report);
        for (var round = 0; round < 6; round++)
        {
            Thread.Sleep(6500);  // 越过 uvicorn 默认 5s timeout_keep_alive
            Save(api, workflowId, "idle-gap-save", report);
        }
        for (var round = 0; round < 6; round++)
        {
            Thread.Sleep(6500);
            api.SendAsync($"workflows/{workflowId}").Wait();
            Report.OK(report, "idle-gap-read");
        }

        // 并发形态：真实验收时缩略图与任务轮询并行，保存是在几十个并发 GET 中间发出的。
        // 串行复现器证明空闲回收不是根因，所以这一相是定因的关键补充。
        for (var round = 0; round < 8; round++)
        {
            var load = new List<Task>();
            for (var i = 0; i < 40; i++)
                load.Add(Task.Run(() =>
                {
                    try { api.SendAsync("projects").Wait(); Report.OK(report, "concurrent"); }
                    catch (Exception exception) { Report.Fail(report, "concurrent", exception); }
                }));
            load.Add(Task.Run(() => Save(api, workflowId, "concurrent-save", report)));
            Task.WaitAll(load.ToArray());
        }

        report.Write(evidenceDir, "keepalive-live");
        Console.WriteLine($"[keepalive/live] burst={report.Fmt("burst")} " +
                          $"idle-gap-save={report.Fmt("idle-gap-save")} " +
                          $"idle-gap-read={report.Fmt("idle-gap-read")} " +
                          $"concurrent={report.Fmt("concurrent")} concurrent-save={report.Fmt("concurrent-save")}");
        if (!report.AllSucceeded) Console.WriteLine($"[keepalive/live] FAILED first chain:\n{report.FirstFailure}");
    }

    private static string? FirstWorkflow(ApiClient api)
    {
        try
        {
            var projects = api.SendAsync("projects").Result;
            foreach (var project in projects.EnumerateArray())
            {
                var id = project.GetProperty("id").GetString();
                if (string.IsNullOrEmpty(id)) continue;
                var workflows = api.SendAsync($"projects/{id}/workflows").Result;
                foreach (var workflow in workflows.EnumerateArray())
                    return workflow.GetProperty("id").GetString();
            }
        }
        catch (Exception exception) { Console.WriteLine($"[keepalive] fixture read failed: {chain(exception)}"); }
        return null;
    }

    private static void Mutate(ApiClient api, string phase, Report report)
    {
        try
        {
            api.SendAsync("projects", HttpMethod.Patch, new { slug = "keepalive-probe" }).Wait();
            Report.OK(report, phase);
        }
        catch (Exception exception) { Report.Fail(report, phase, exception); }
    }

    private static void Read(ApiClient api, string phase, Report report)
    {
        try { api.SendAsync("projects").Wait(); Report.OK(report, phase); }
        catch (Exception exception) { Report.Fail(report, phase, exception); }
    }

    private static void Save(ApiClient api, string workflowId, string phase, Report report)
    {
        var payload = new
        {
            nodes = Array.Empty<object>(),
            edges = Array.Empty<object>(),
            viewport = new { x = 0, y = 0, zoom = 1 },
        };
        try
        {
            var current = api.SendAsync($"workflows/{workflowId}").Result;
            var version = current.TryGetProperty("version", out var value) && value.ValueKind == JsonValueKind.Number
                ? value.GetInt32() : 1;
            api.SendAsync($"workflows/{workflowId}", HttpMethod.Patch,
                new { version, draft_graph = payload }).Wait();
            Report.OK(report, phase);
        }
        catch (Exception exception) { Report.Fail(report, phase, exception); }
    }

    private static string chain(Exception exception)
    {
        var builder = new StringBuilder();
        for (Exception? cursor = exception; cursor != null; cursor = cursor.InnerException)
            builder.AppendLine($"{cursor.GetType().Name}: {cursor.Message}");
        return builder.ToString();
    }

    /// <summary>最小 HTTP/1.1 keep-alive 服务器：响应后等待下一个请求，超过
    /// idleMilliseconds 无字节即由服务端关闭连接——精确复刻 uvicorn 的空闲回收。</summary>
    private sealed class KeepAliveServer : IDisposable
    {
        private readonly TcpListener listener;
        private readonly CancellationTokenSource stop = new();
        private readonly int idleMilliseconds;
        private readonly ConcurrentBag<Task> sessions = new();

        public int Port { get; }
        public int AcceptedSockets;
        public int SawCleanClose;

        public KeepAliveServer(int idleMilliseconds)
        {
            this.idleMilliseconds = idleMilliseconds;
            listener = new TcpListener(System.Net.IPAddress.Loopback, 0);
            listener.Start();
            Port = ((System.Net.IPEndPoint)listener.LocalEndpoint).Port;
            _ = Task.Run(AcceptAsync);
        }

        private async Task AcceptAsync()
        {
            while (!stop.IsCancellationRequested)
            {
                TcpClient client;
                try { client = await listener.AcceptTcpClientAsync(stop.Token); }
                catch { return; }
                Interlocked.Increment(ref AcceptedSockets);
                sessions.Add(ServeAsync(client));
            }
        }

        private async Task ServeAsync(TcpClient client)
        {
            using (client)
            {
                var stream = client.GetStream();
                var buffer = new byte[8192];
                var pending = new List<byte>();
                while (!stop.IsCancellationRequested)
                {
                    using var idle = new CancellationTokenSource(idleMilliseconds);
                    using var linked = CancellationTokenSource.CreateLinkedTokenSource(idle.Token, stop.Token);
                    int read;
                    try
                    {
                        do
                        {
                            read = await stream.ReadAsync(buffer, linked.Token);
                            if (read == 0) { Interlocked.Increment(ref SawCleanClose); return; }
                            pending.AddRange(buffer.AsMemory(0, read).ToArray());
                        }
                        while (!HasHeaderEnd(pending));
                    }
                    catch
                    {
                        return;  // 空闲超时：由服务端主动关闭，即被测缺陷的触发条件
                    }

                    var text = Encoding.ASCII.GetString(pending.ToArray());
                    var bodyLength = ContentLength(text);
                    var headerEnd = HeaderEndIndex(pending);
                    while (pending.Count - headerEnd < bodyLength)
                    {
                        read = await stream.ReadAsync(buffer, stop.Token);
                        if (read == 0) return;
                        pending.AddRange(buffer.AsMemory(0, read).ToArray());
                    }
                    pending.RemoveRange(0, headerEnd + bodyLength);

                    var body = "{\"ok\":true}";
                    var response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" +
                                   $"Content-Length: {Encoding.ASCII.GetByteCount(body)}\r\n" +
                                   "Connection: keep-alive\r\n\r\n" + body;
                    await stream.WriteAsync(Encoding.ASCII.GetBytes(response), stop.Token);
                }
            }
        }

        private static bool HasHeaderEnd(List<byte> bytes) => HeaderEndIndex(bytes) >= 0;

        private static int HeaderEndIndex(List<byte> bytes)
        {
            for (var i = 0; i + 3 < bytes.Count; i++)
                if (bytes[i] == '\r' && bytes[i + 1] == '\n' && bytes[i + 2] == '\r' && bytes[i + 3] == '\n')
                    return i + 4;
            return -1;
        }

        private static int ContentLength(string text)
        {
            foreach (var line in text.Split("\r\n"))
            {
                if (!line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase)) continue;
                return int.Parse(line.Split(':', 2)[1].Trim());
            }
            return 0;
        }

        public void Dispose()
        {
            stop.Cancel();
            listener.Stop();
            foreach (var session in sessions)
            {
                try { session.Wait(TimeSpan.FromSeconds(2)); } catch { }
            }
        }
    }

    private sealed class Report
    {
        private readonly ConcurrentDictionary<string, int[]> buckets = new();
        private readonly ConcurrentQueue<string> failures = new();
        public Dictionary<string, object> Extra { get; } = new();
        public string? Note { get; set; }
        public string FirstFailure => failures.FirstOrDefault() ?? "";
        public bool AllSucceeded => failures.IsEmpty;

        public int[] Bucket(string phase) =>
            buckets.GetOrAdd(phase, _ => new int[2]);

        public string Fmt(string phase)
        {
            var counts = Bucket(phase);
            return $"{counts[0]}ok/{counts[1]}failed";
        }

        public static void OK(Report report, string phase) => report.Bucket(phase)[0]++;

        public static void Fail(Report report, string phase, Exception exception)
        {
            report.Bucket(phase)[1]++;
            if (report.failures.IsEmpty || report.failures.Count < 4)
                report.failures.Enqueue($"[{phase}] {chain0(exception)}");
        }

        private static string chain0(Exception exception)
        {
            var builder = new StringBuilder();
            for (Exception? cursor = exception; cursor != null; cursor = cursor.InnerException)
                builder.Append($"{cursor.GetType().Name}: {cursor.Message} <- ");
            return builder.ToString();
        }

        public void Write(string evidenceDir, string name)
        {
            var payload = new
            {
                name,
                at = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"),
                note = Note,
                buckets = buckets.ToDictionary(pair => pair.Key, pair =>
                    new { ok = pair.Value[0], failed = pair.Value[1] }),
                failures = failures.ToArray(),
                extra = Extra,
            };
            var path = Path.Combine(evidenceDir, $"{name}-{DateTime.Now:yyyyMMdd-HHmmss}.json");
            File.WriteAllText(path, JsonSerializer.Serialize(payload,
                new JsonSerializerOptions { WriteIndented = true }));
            Console.WriteLine($"[keepalive] evidence -> {path}");
        }
    }
}
