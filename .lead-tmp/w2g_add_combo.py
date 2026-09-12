import io

prog = r"D:\自媒体\漫画工作流-wt-a" + r"\apps\desktop\native-tests\Program.cs"
with io.open(prog, encoding="utf-8") as handle:
    s = handle.read()
old = 'if (args.Contains("--issue429")) { NativeIssue429Checks.Run(); Console.WriteLine("issue429-only done"); return 0; }\n'
combo = '''if (args.Contains("--issue428429")) {
    var t = new Thread(() => {
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        app.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("/MangaFlow.Native;component/Theme.xaml", UriKind.Relative) });
        NativeIssue428Checks.Run();
        NativeIssue429Checks.Run();
        Console.WriteLine("428+429 done");
    });
    t.SetApartmentState(ApartmentState.STA);
    t.Start();
    t.Join();
    return 0;
}
'''
assert old in s and "--issue428429" not in s
with io.open(prog, "w", encoding="utf-8", newline="") as handle:
    handle.write(s.replace(old, old + combo))
print("combo-entry-added")
