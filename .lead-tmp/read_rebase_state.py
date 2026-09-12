# -*- coding: utf-8 -*-
import io
import subprocess
import os

for name in ("orig-head", "onto", "head-name", "msgnum", "end"):
    path = os.path.join(".git", "rebase-merge", name)
    try:
        print(name, "=", io.open(path, encoding="utf-8").read().strip())
    except Exception as error:
        print(name, "ERR", error)

result = subprocess.run(["git", "log", "--oneline", "-1", "HEAD"], capture_output=True)
print("detached HEAD ->", result.stdout.decode("utf-8", "replace").strip(), result.stderr.decode("utf-8", "replace").strip()[:200])

result = subprocess.run(["git", "rev-parse", "master", "HEAD"], capture_output=True)
print("master / HEAD sha ->", result.stdout.decode("utf-8", "replace").split())
