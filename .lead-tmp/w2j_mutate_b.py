# W2J mutation B: restore canonicalization, remove the start-native.ps1 refusal block.
import io

app = r"D:\自媒体\漫画工作流-wt-d" + r"\apps\desktop\native\App.xaml.cs"
with io.open(app, encoding="utf-8") as handle:
    text = handle.read()
mutation = """    private static string? TryResolveFinalPath(string fullPath)
    {
        return null; // MUTATION
"""
original = """    private static string? TryResolveFinalPath(string fullPath)
    {
"""
assert text.count(mutation) == 1
with io.open(app, "w", encoding="utf-8", newline="") as handle:
    handle.write(text.replace(mutation, original))
print("restored: canonicalization")

script = r"D:\自媒体\漫画工作流-wt-d" + r"\apps\desktop\scripts\start-native.ps1"
with io.open(script, encoding="utf-8") as handle:
    text = handle.read()
start = text.index("# #410: the Tauri shell owns")
end = text.index("Push-Location $nativeRepo")
block = text[start:end]
assert "Refusing -UserData" in block
with io.open(script, "w", encoding="utf-8", newline="") as handle:
    handle.write(text[:start] + text[end:])
print("mutated: refusal block removed")
