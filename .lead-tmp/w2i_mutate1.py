# W2I mutation round 1: revert the three real fixes, restore old SequenceEqual for #486-2.
# Expect: #486-2 dashboard check still PASSES (premise of the original audit is false),
# #486-1 dead-batch check FAILS (real defect), media checks unreachable this round.
import io, sys

root = r"D:\自媒体\漫画工作流-wt-c"
edits = [
    # (relative path, old snippet, new snippet)
    (
        r"apps\desktop\native\Views\StyleProductionCard.cs",
        "        await VerifyPendingBatchAsync();\n",
        "",
    ),
    (
        r"apps\desktop\native\WorkspaceState.cs",
        """        if (DashboardProjects.Count != visible.Count
            || DashboardProjects.Zip(visible).Any(pair => !pair.First.SameDisplay(pair.Second)))
""",
        "        if (!DashboardProjects.SequenceEqual(visible))\n",
    ),
    (
        r"apps\desktop\native\Controls\Ui.cs",
        """            if (!response.IsSuccessStatusCode)
            {
                // #449-2：原 EnsureSuccessStatusCode 只会抛生成的英文行，后端 404/409
                // 的 detail（如素材归档冲突）到不了任何图片界面；改走 MediaErrors 映射。
                var text = await response.Content.ReadAsStringAsync(cancellation).ConfigureAwait(false);
                throw new InvalidOperationException(MediaErrors.DescribeResponse(response, text));
            }
""",
        "            response.EnsureSuccessStatusCode();\n",
    ),
    (
        r"apps\desktop\native\Controls\Ui.cs",
        """        catch (OperationCanceledException)
        {
            await Dispatcher.BeginInvoke(() => ShowFailure("图片加载超时，请重试。"));
        }
        catch (Exception ex)
        {
            var message = MediaErrors.Localize(ex);
            await Dispatcher.BeginInvoke(() => ShowFailure(message));
        }
""",
        "        catch (Exception) { }\n",
    ),
]

for rel, old, new in edits:
    path = root + "\\" + rel
    with io.open(path, encoding="utf-8") as handle:
        text = handle.read()
    if text.count(old) != 1:
        sys.exit("MUTATE ABORT: %s expected 1 occurrence, found %d" % (rel, text.count(old)))
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace(old, new))
    print("mutated:", rel)

# fast entry flag
prog = root + r"\apps\desktop\native-tests\Program.cs"
with io.open(prog, encoding="utf-8") as handle:
    text = handle.read()
anchor = '// Client-side regression checks: API safety, models, preferences, navigation, director rules.\n'
flag = 'if (args.Contains("--issue486")) { NativeIssue486Checks.Run(Path.Combine(Path.GetTempPath(), "mangaflow-issue486-lead")); Console.WriteLine("issue486-only done"); return 0; }\n'
assert text.count(anchor) == 1
with io.open(prog, "w", encoding="utf-8", newline="") as handle:
    handle.write(text.replace(anchor, anchor + flag))
print("mutated: Program.cs fast entry")
