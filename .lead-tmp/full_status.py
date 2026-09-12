# -*- coding: utf-8 -*-
import subprocess

def run(*args):
    result = subprocess.run(list(args), capture_output=True)
    return (result.stdout or b"").decode("utf-8", "replace"), (result.stderr or b"").decode("utf-8", "replace")

out, err = run("git", "status", "--porcelain=v1", "--untracked-files=no")
print("=== TRACKED CHANGES ===")
print(out if out.strip() else "(none)")

out, err = run("git", "status", "--porcelain=v1", "--untracked-files=all", "--", ".lead-tmp")
lines = [l for l in out.split("\n") if l.strip()]
print("=== .lead-tmp UNTRACKED (first 20) ===")
print("\n".join(lines[:20]))
print("... total .lead-tmp entries:", len(lines))

out, err = run("git", "diff", "--name-only", "HEAD")
print("=== DIFF vs HEAD (first 20) ===")
print("\n".join(out.split("\n")[:20]))
print("total diff entries:", len([l for l in out.split("\n") if l.strip()]))

out, err = run("git", "diff", "--cached", "--name-only", "HEAD")
print("=== STAGED vs HEAD (first 20) ===")
print("\n".join(out.split("\n")[:20]))
print("total staged entries:", len([l for l in out.split("\n") if l.strip()]))
