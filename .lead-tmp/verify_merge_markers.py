# -*- coding: utf-8 -*-
import io
import subprocess

def git(*args):
    result = subprocess.run(["git", *args], capture_output=True)
    return (result.stdout or b"").decode("utf-8", "replace").strip()

print("=== log ===")
print(git("log", "--oneline", "-5"))
print("=== status ===")
status = git("status", "--porcelain=v1", "--untracked-files=no")
print(status if status else "(clean)")

markers = {
    r"apps\desktop\native\Views\ScriptView.cs": [
        "ActivatePreservingDrafts",   # W2G
        "editingFormsOpen",           # pre-existing + W2G guard
        "ScriptPageUi",               # agent layout helper
    ],
    r"apps\desktop\native\Views\StoryboardView.cs": [
        "ActivatePreservingDrafts",   # W2G
        "pagesLoadVersion",           # W2G epoch
        "StoryboardLayout",           # agent layout helper
    ],
    r"apps\desktop\native\Views\GenerateView.cs": ["ActivatePreservingDrafts", "DirectorDraftActive", "LeaveConfirmOverride"],
    r"apps\desktop\native-tests\NativeInteractionChecks.cs": [
        "NativeIssue428Checks.Run()", "NativeIssue429Checks.Run()", "NativeIssue485Checks.Run()",
        "NativeIssue486Checks.Run(output)", "NativeIssue410Checks.Run()",
    ],
    r"apps\desktop\native-tests\Program.cs": ["--script", "--storyboard", "--references"],
    r"apps\desktop\native\Views\LocalEditWindow.cs": ["DescribeImageLoadFailure", "CloseConfirmOverride"],
    r"apps\desktop\native\Views\MainWindow.xaml.cs": ["ActivateView(view, context, preserveDrafts)", "preserveDrafts"],
}
for path, needles in markers.items():
    text = io.open(path, encoding="utf-8", errors="replace").read()
    hits = {n: text.count(n) for n in needles}
    ok = all(count > 0 for count in hits.values())
    print(("OK  " if ok else "MISSING "), path)
    for name, count in hits.items():
        print("      %-45s x%d" % (name, count))
