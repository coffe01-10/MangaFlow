import subprocess, sys

def check(ref, path):
    data = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True).stdout
    try:
        text = data.decode("utf-8")
        ok = "valid utf-8"
        moji = text.count("\ufffd") + text.count("锛")
        idx = text.find("#469")
        sample = repr(text[max(0, idx - 100):idx + 200]) if idx >= 0 else "(no #469)"
        print(f"{ref} {path}: {ok}, {len(data)} bytes, mojibake={moji}")
        print("  ", sample[:400])
    except UnicodeDecodeError as e:
        print(f"{ref} {path}: NOT utf-8 -> {e}")

check("fix/native-w1e-settings", "apps/desktop/native/Views/ProjectSettingsView.cs")
check("fix/native-w1e-settings", "apps/desktop/native/Views/SettingsView.cs")
check("fix/native-w1a-apiclient", "apps/desktop/native/Services/ApiClient.cs")
check("fix/native-w1b-scriptview", "apps/desktop/native/Views/ScriptView.cs")
check("fix/native-w1c-shell", "apps/desktop/native/Views/HomeView.cs")
check("fix/native-w1d-workflow", "apps/desktop/native/Views/WorkflowView.cs")
