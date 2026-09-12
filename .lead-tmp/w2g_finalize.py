import io

root = r"D:\自媒体\漫画工作流-wt-a"

# 1) NativeIssue429Checks.cs: remove instrumentation, harden waits.
path = root + r"\apps\desktop\native-tests\NativeIssue429Checks.cs"
with io.open(path, encoding="utf-8") as handle:
    s = handle.read()

instrumented = """            selector.SelectedItem = Item("ch-1");   // B→A：A 的 pages 响应被门控挂起
            // 计数含初始 A 载入的一次；==2 即「门控中的第二次 A 读取确已发出」。
            for (var probe = 0; probe < 8; probe++)
            {
                await Settle(500);
                Console.WriteLine($"DIAG429 probe{probe} readsA={fixture.ChapterAPagesReads} readsB={fixture.StoryboardReadsPageB} dirty={(bool)typeof(StoryboardView).GetField("dirty", All)!.GetValue(view)!} chapterId={(string)typeof(StoryboardView).GetField("chapterId", All)!.GetValue(view)!} selTag={(selector.SelectedItem as ComboBoxItem)?.Tag} pagesVersion={(int)typeof(StoryboardView).GetField("pagesLoadVersion", All)!.GetValue(view)!} pageId={PageId(view)} windows={string.Join("|", Application.Current.Windows.Cast<Window>().Select(w => w.Title))}");
            }
            await Until(() => fixture.ChapterAPagesReads == 2);
"""
restored = """            selector.SelectedItem = Item("ch-1");   // B→A：A 的 pages 响应被门控挂起
            // 计数含初始 A 载入的一次；>=2 即「门控中的第二次 A 读取确已发出」。
            // （lead：等值判定在高负载链内曾 flaky——读取计数短暂越过目标值会让
            // 条件永假直到超时；改为 >= 并给切换类等待 15s 预算。）
            await Until(() => fixture.ChapterAPagesReads >= 2, 15, "B→A 切换的第二次 A 章 pages 读取未发出（#429-3 前置）");
"""
assert s.count(instrumented) == 1, "instrumented block not found"
s = s.replace(instrumented, restored)

old_until = """    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(5, timeout.Token);
    }
"""
new_until = """    private static Task Until(Func<bool> condition) => Until(condition, 5, "等待条件超时");

    // lead：链内跑在数十个检查之后，调度器上残留 11+ 个宿主窗口的定时器——5s 预算
    // 在高负载下曾 flaky（TaskCanceledException 裸抛难以定位）。带预算与说明文案，
    // 超时转成可读异常；切换/重载类等待按 15s 起步。
    private static async Task Until(Func<bool> condition, int seconds, string message)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(seconds));
        try { while (!condition()) await Task.Delay(5, timeout.Token); }
        catch (OperationCanceledException) { throw new Exception($"等待超时（{seconds}s）：{message}"); }
    }
"""
assert s.count(old_until) == 1, "Until not found"
s = s.replace(old_until, new_until)

# longer budgets for the chapter-switch waits in check ③ (and its initial load)
s = s.replace(
    "            await Until(() => view.PanelCountForTest == 1);\n            Require(PageId(view) == \"pg-a\"",
    "            await Until(() => view.PanelCountForTest == 1, 15, \"初始章 A 未载入（#429-3 前置）\");\n            Require(PageId(view) == \"pg-a\"",
    1,
)
s = s.replace(
    "            await Until(() => PageId(view) == \"pg-b\");\n            var storyboardReadsA = fixture.StoryboardReadsPageA;",
    "            await Until(() => PageId(view) == \"pg-b\", 15, \"A→B 切换未完成（#429-3 前置）\");\n            var storyboardReadsA = fixture.StoryboardReadsPageA;",
    1,
)
s = s.replace(
    "            await Until(() => PageId(view) == \"pg-b\" && fixture.StoryboardReadsPageB >= 2);",
    "            await Until(() => PageId(view) == \"pg-b\" && fixture.StoryboardReadsPageB >= 2, 15, \"切回 B 未完成（#429-3 前置）\");",
    1,
)
with io.open(path, "w", encoding="utf-8", newline="") as handle:
    handle.write(s)
print("429-checks hardened")

# 2) Program.cs: drop both temp entries.
prog = root + r"\apps\desktop\native-tests\Program.cs"
with io.open(prog, encoding="utf-8") as handle:
    s = handle.read()
anchor = '// Client-side regression checks: API safety, models, preferences, navigation, director rules.\n'
end = s.index('if (args.Contains("--references"))')
block = s[s.index(anchor) + len(anchor):end]
assert "--issue429" in block and "--issue428429" in block
s = s.replace(anchor + block, anchor)
with io.open(prog, "w", encoding="utf-8", newline="") as handle:
    handle.write(s)
print("program temp entries removed")
