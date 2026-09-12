# -*- coding: utf-8 -*-
import subprocess

LIMIT = 10 * 1024 * 1024

for ref in ("0cbde1e", "6db261b", "9e4e039"):
    result = subprocess.run(["git", "ls-tree", "-r", "--long", ref], capture_output=True)
    text = result.stdout.decode("utf-8", "replace")
    big = []
    total = 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        size = int(parts[3])
        total += size
        if size > LIMIT:
            big.append((size, path))
    print("===", ref, "total bytes:", total)
    if big:
        for size, path in sorted(big, reverse=True):
            print("   BIG %10.1f MB  %s" % (size / 1048576.0, path))
    else:
        print("   (no files above 10 MB)")
