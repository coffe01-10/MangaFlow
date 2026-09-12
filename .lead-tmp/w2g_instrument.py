import io

path = r"D:\自媒体\漫画工作流-wt-a" + r"\apps\desktop\native-tests\NativeIssue429Checks.cs"
with io.open(path, encoding="utf-8") as handle:
    s = handle.read()
old = """            selector.SelectedItem = Item("ch-1");   // B→A：A 的 pages 响应被门控挂起
            // 计数含初始 A 载入的一次；==2 即「门控中的第二次 A 读取确已发出」。
            await Until(() => fixture.ChapterAPagesReads == 2);
"""
new = """            selector.SelectedItem = Item("ch-1");   // B→A：A 的 pages 响应被门控挂起
            // 计数含初始 A 载入的一次；==2 即「门控中的第二次 A 读取确已发出」。
            for (var probe = 0; probe < 8; probe++)
            {
                await Settle(500);
                Console.WriteLine($"DIAG429 probe{probe} readsA={fixture.ChapterAPagesReads} readsB={fixture.StoryboardReadsPageB} dirty={(bool)typeof(StoryboardView).GetField("dirty", All)!.GetValue(view)!} chapterId={(string)typeof(StoryboardView).GetField("chapterId", All)!.GetValue(view)!} selTag={(selector.SelectedItem as ComboBoxItem)?.Tag} pagesVersion={(int)typeof(StoryboardView).GetField("pagesLoadVersion", All)!.GetValue(view)!} pageId={PageId(view)} windows={string.Join("|", Application.Current.Windows.Cast<Window>().Select(w => w.Title))}");
            }
            await Until(() => fixture.ChapterAPagesReads == 2);
"""
assert s.count(old) == 1
with io.open(path, "w", encoding="utf-8", newline="") as handle:
    handle.write(s.replace(old, new))
print("instrumented")
