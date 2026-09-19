import ast
import sys

path = r"D:\自媒体\漫画工作流\scripts\nui67_seed.py"
source = open(path, encoding="utf-8").read()
ast.parse(source)
checks = ["并排对照主项目", "学校天台", "林晚", "status=\"CANONICAL\"", "status=\"ACTIVE\""]
missing = [c for c in checks if c not in source]
print("syntax OK; missing:", missing or "none")
