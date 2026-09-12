import io
import sys

root = r"D:\自媒体\漫画工作流-wt-a"

# temp focused entries for mutation runs
prog = root + r"\apps\desktop\native-tests\Program.cs"
with io.open(prog, encoding="utf-8") as handle:
    s = handle.read()
anchor = '// Client-side regression checks: API safety, models, preferences, navigation, director rules.\n'
flags = (
    'if (args.Contains("--issue428")) { NativeIssue428Checks.Run(); Console.WriteLine("issue428-only done"); return 0; }\n'
    'if (args.Contains("--issue429")) { NativeIssue429Checks.Run(); Console.WriteLine("issue429-only done"); return 0; }\n'
)
if "--issue428" not in s:
    s = s.replace(anchor, anchor + flags)
    with io.open(prog, "w", encoding="utf-8", newline="") as handle:
        handle.write(s)
    print("entries-added")

mutations = {
    # A1: reconnect same-id branch disabled (old fall-through: no confirm, full reload)
    "A1": [
        (
            r"apps\desktop\native\MainWindow.xaml.cs",
            "        if (state.CurrentProject?.Id == item.Id)",
            "        if (false && state.CurrentProject?.Id == item.Id)",
        ),
    ],
    # A2: preserve dispatch degenerates to full Activate
    "A2": [
        (
            r"apps\desktop\native\MainWindow.xaml.cs",
            "        if (!preserveDrafts)",
            "        if (true)",
        ),
    ],
    # B1: RefreshAsync draft guard removed
    "B1": [
        (
            r"apps\desktop\native\Views\GenerateView.cs",
            "        if (DirectorDraftActive) return Task.CompletedTask;",
            "        ",
        ),
    ],
    # B2: chapter-switch leave confirm removed
    "B2": [
        (
            r"apps\desktop\native\Views\GenerateView.cs",
            "                if (!await ConfirmLeaveAsync()) { SelectChapter(chapterId); return; }\n",
            "",
        ),
    ],
    # C1: LoadPagesAsync epoch guard removed (cancellation check kept)
    "C1": [
        (
            r"apps\desktop\native\Views\StoryboardView.cs",
            "            if (requestVersion != pagesLoadVersion || lifetime.Token.IsCancellationRequested) return;",
            "            if (lifetime.Token.IsCancellationRequested) return;",
        ),
    ],
    # C2: LocalEditWindow close guard removed
    "C2": [
        (
            r"apps\desktop\native\Views\LocalEditWindow.cs",
            "            if (HasUnsavedDraft && !ConfirmCloseDraft()) args.Cancel = true;\n            return;",
            "            return;",
        ),
    ],
    # C3: localized HttpRequestException catch removed (generic catch shows raw English)
    "C3": [
        (
            r"apps\desktop\native\Views\LocalEditWindow.cs",
            """        catch (HttpRequestException error)
        {
            // #449-3: 源图读取走 ImageStore 的独立 HttpClient，不经过 ApiClient
            // 的 ThrowResponseError detail 映射——EnsureSuccessStatusCode 抛出的
            // 英文原文（HttpRequestException.Message）不得直接上屏。按应用的
            // 错误契约（中文标签 + 状态码，ApiClient 同型）映射；连接层失败没有
            // 状态码时给统一中文兜底。
            notice.Text = DescribeImageLoadFailure((int?)error.StatusCode);
        }
""",
            "",
        ),
    ],
}

key = sys.argv[1]
for rel, old, new in mutations[key]:
    path = root + "\\" + rel
    with io.open(path, encoding="utf-8") as handle:
        s = handle.read()
    if s.count(old) != 1:
        sys.exit("MUTATION %s ABORT: %s found %d" % (key, rel, s.count(old)))
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(s.replace(old, new))
    print("mutated", key, rel)
