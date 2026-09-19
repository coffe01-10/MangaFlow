import ast

for path in (r"D:\自媒体\漫画工作流\scripts\nui67_side_by_side.py", r"D:\自媒体\漫画工作流\scripts\nui67_seed.py"):
    ast.parse(open(path, encoding="utf-8").read())
    print("syntax OK:", path.rsplit("\\", 1)[-1])
