# W2J mutation A: disable final-path resolution entirely -> junction/short-name alias
# checks must fail; KAT and lexical-fallback must still pass (they do not depend on it).
import io

app = r"D:\自媒体\漫画工作流-wt-d" + r"\apps\desktop\native\App.xaml.cs"
with io.open(app, encoding="utf-8") as handle:
    text = handle.read()
anchor = """    private static string? TryResolveFinalPath(string fullPath)
    {
"""
replacement = """    private static string? TryResolveFinalPath(string fullPath)
    {
        return null; // MUTATION
"""
assert text.count(anchor) == 1
with io.open(app, "w", encoding="utf-8", newline="") as handle:
    handle.write(text.replace(anchor, replacement))
print("mutated: canonicalization disabled")

prog = r"D:\自媒体\漫画工作流-wt-d" + r"\apps\desktop\native-tests\Program.cs"
with io.open(prog, encoding="utf-8") as handle:
    text = handle.read()
anchor = '// Client-side regression checks: API safety, models, preferences, navigation, director rules.\n'
flag = 'if (args.Contains("--issue410")) { NativeIssue410Checks.Run(); Console.WriteLine("issue410-only done"); return 0; }\n'
assert text.count(anchor) == 1
with io.open(prog, "w", encoding="utf-8", newline="") as handle:
    handle.write(text.replace(anchor, anchor + flag))
print("mutated: Program.cs fast entry")
