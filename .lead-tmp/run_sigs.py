import subprocess, re

files = [
    "NativeIssue442Checks.cs", "NativeIssue439Checks.cs",
    "NativeIssue484Checks.cs", "NativeIssue426Checks.cs",
    "NativeIssue411Checks.cs", "NativeIssue470Checks.cs",
    "NativeIssue427Checks.cs", "NativeIssue469Checks.cs",
]
for f in files:
    path = f"apps/desktop/native-tests/{f}"
    for ref in ["fix/native-w1a-apiclient", "fix/native-w1b-scriptview",
                "fix/native-w1c-shell", "fix/native-w1d-workflow", "fix/native-w1e-settings"]:
        data = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True).stdout
        if data:
            text = data.decode("utf-8", errors="replace")
            head = text[:1200]
            sigs = re.findall(r"(internal|public)\s+static\s+(async\s+)?\w+\s+Run\w*\([^)]*\)", text)
            print(f"== {f} @ {ref}")
            for s in sigs:
                print("   sig:", s[0], "static", "Run", s[1] or "")
            hint = [l.strip() for l in text.splitlines() if "lead" in l.lower() or "注册" in l][:4]
            for h in hint:
                print("   hint:", h[:160])
            break
