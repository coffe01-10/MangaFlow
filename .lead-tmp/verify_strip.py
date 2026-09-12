# -*- coding: utf-8 -*-
import subprocess

def git(*args):
    result = subprocess.run(["git", *args], capture_output=True)
    return (result.stdout or b"").decode("utf-8", "replace").strip()

print("new master chain:")
print(git("log", "--oneline", "-4", "master"))
print()
for ref in ("master", "master~1", "master~2"):
    tree = git("ls-tree", "-r", "--long", ref)
    dump = [l for l in tree.split("\n") if "hang427.dmp" in l]
    print(ref, "dump ->", dump if dump else "ABSENT")
    print("   files in commit:", len([l for l in tree.split("\n") if l.strip()]))
print()
# spot-check key files survive with real content
for path in ("apps/desktop/native-tests/NativeScriptPageChecks.cs",
             "apps/desktop/native/Views/ScriptPageUi.cs",
             "apps/desktop/native/Views/StoryboardLayout.cs",
             "output/storyboard-page-review/README.md"):
    size = git("cat-file", "-s", "master:" + path)
    print("master:%s -> %s bytes" % (path, size or "MISSING"))
