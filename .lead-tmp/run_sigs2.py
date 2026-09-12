import subprocess, re

files = [
    "NativeIssue442Checks.cs", "NativeIssue439Checks.cs",
    "NativeIssue484Checks.cs", "NativeIssue426Checks.cs",
    "NativeIssue411Checks.cs", "NativeIssue470Checks.cs",
    "NativeIssue427Checks.cs", "NativeIssue469Checks.cs",
]
refs = ["fix/native-w1a-apiclient", "fix/native-w1b-scriptview",
        "fix/native-w1c-shell", "fix/native-w1d-workflow", "fix/native-w1e-settings"]
for f in files:
    path = f"apps/desktop/native-tests/{f}"
    for ref in refs:
        data = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True).stdout
        if data:
            text = data.decode("utf-8", errors="replace")
            for m in re.finditer(r"(?:internal|public)\s+static\s+(?:async\s+)?(?:Task|void)\s+(Run\w*)\s*\(([^)]*)\)", text):
                print(f"{f}: {m.group(1)}({m.group(2)})")
            break
