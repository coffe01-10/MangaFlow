# -*- coding: utf-8 -*-
import subprocess

for ref in ["0cbde1e", "6db261b", "9e4e039", "bdc083b", "HEAD", "origin/master"]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--long", ref, "--", ".lead-tmp/hang427.dmp"],
        capture_output=True)
    out = result.stdout.decode("utf-8", "replace").strip()
    print(ref, "->", out if out else "ABSENT")
