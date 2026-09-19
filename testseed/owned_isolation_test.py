"""Isolation test for OwnedProcess log-handle inheritance."""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\自媒体\漫画工作流\scripts")
import os

os.chdir(r"D:\自媒体\漫画工作流")
from nui67_side_by_side import OwnedProcess, REPO

log = REPO / "testseed" / "owned_test.log"
if log.exists():
    log.unlink()
proc = OwnedProcess(
    "mangaflow-owned-test", "cmd.exe /c echo hello-stdout & echo hello-stderr 1>&2 & timeout /t 3",
    dict(os.environ), log,
)
time.sleep(4)
proc.terminate_tree()
time.sleep(1)
data = log.read_bytes() if log.exists() else b"<missing>"
print("log bytes:", len(data), "content:", data[:200])
