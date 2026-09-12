# -*- coding: utf-8 -*-
import io

checks = [
    (r"D:\自媒体\漫画工作流\apps\web\components\storyboard-editor\index.tsx", "const refresh = () => {", 5),
    (r"D:\自媒体\漫画工作流\apps\web\components\project-workspace.tsx", "generation-workbench", 4),
    (r"D:\自媒体\漫画工作流\apps\web\components\project-workspace\assets-section.tsx", "resetOutfitForm", 4),
    (r"D:\自媒体\漫画工作流\apps\web\components\project-workspace\character-package-workspace.tsx", "spec:${pkg.id}", 3),
    (r"D:\自媒体\漫画工作流\apps\web\components\project-workspace\scene-modal.tsx", "provider-dialog-backdrop", 4),
    (r"D:\自媒体\漫画工作流\apps\web\components\workflow-studio.tsx", "Promise.all", 4),
    (r"D:\自媒体\漫画工作流\apps\web\components\project-workspace\use-assets-workspace.ts", '["outfits", id]', 4),
]
for path, needle, ctx in checks:
    lines = io.open(path, encoding="utf-8").read().split("\n")
    hits = [i for i, line in enumerate(lines) if needle in line]
    for hit in hits[:3]:
        print("---", path.split("\\")[-1], hit + 1)
        print("\n".join(lines[max(0, hit - 1): hit + ctx]))
