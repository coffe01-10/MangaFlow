# -*- coding: utf-8 -*-
import subprocess

def git(*args):
    result = subprocess.run(["git", *args], capture_output=True)
    return (result.stdout or b"").decode("utf-8", "replace").strip()

print("1) dump in pushed origin/master tree:")
print("  ", git("ls-tree", "-r", "origin/master", "--", ".lead-tmp/hang427.dmp") or "ABSENT (good)")

print("2) which refs still hold the pre-rewrite commits (local only):")
for ref in ("refs/original/refs/heads/master", "refs/heads/backup/pre-dump-strip"):
    print("  ", ref, "->", git("rev-parse", "--short", ref) or "gone")

print("3) any remote branch containing the original dump-carrying commit 0cbde1e:")
print("  ", git("branch", "-r", "--contains", "0cbde1e") or "none (good)")

print("4) pushed history range:")
print(git("log", "--oneline", "bdc083b..origin/master"))

print("5) size of the local object store (blob still referenced by backup ref):")
print("  ", git("count-objects", "-vH"))
