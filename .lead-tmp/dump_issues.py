import subprocess, json, io, sys

out = subprocess.run(["gh", "issue", "list", "--state", "open", "--limit", "50",
                      "--json", "number,title,body"],
                     capture_output=True, text=True, encoding="utf-8")
issues = json.loads(out.stdout)
with io.open(r"D:\自媒体\漫画工作流\.lead-tmp\issues-dump.txt", "w", encoding="utf-8") as f:
    f.write(f"{len(issues)} issues\n")
    for i in issues:
        f.write("=" * 100 + "\n")
        f.write(f"#{i['number']} {i['title']}\n")
        f.write((i.get("body") or "")[:3000] + "\n")
print("ok", len(issues))
