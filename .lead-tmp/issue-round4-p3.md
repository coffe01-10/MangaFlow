**Found:** 第四轮只读审查（区间 a08dae8..002613a），组长已亲自核实三处代码点属实（2026-09-11）。

## 机制

`apps/web/components/workflow-studio.tsx` 审批条（#381 引入的空目录指引分支旁）：

1. `drawModel` 是组件级 `useState`，唯一写入点是 select 的 onChange——**没有任何重置路径**。
2. `imageModels` 经 `creatorVisibleModels(models, { logicalAliases: [drawModel] })` 计算；别名豁免只对**仍存在于 models.data 的行**生效（model-visibility.ts:20-23 的过滤器输入是当前行集合），供应商连接被删或目录重验后该行整体消失时豁免无效——`imageModels.length === 0` 而 `drawModel` 仍是残留的非空旧别名。
3. 按钮谓词仍是 `!drawModel`：残留别名使其为假——指引文案（"未配置可用图像模型…"）与**可点的**「确认继续」同屏自相矛盾；点击后 `approveNode` 以 `image_model_alias: <失效别名>` 提交，后端 `resolve_model` 拒绝（lifecycle.py:247-254），报错可恢复但契约缺口真实。

同构半边：目录里该模型行消失但其他模型仍在时，select 回落占位项而按钮仍可点。

## 修复面

谓词收紧为"选中别名必须仍存在于当前 imageModels"（空目录与残留别名同被覆盖）；回归测试钉住"曾选模型→目录清空→指引+禁用"与"曾选模型被删但目录非空→禁用直到重选"两条路径（现有 #381 测试都从空 drawModel 起步，覆盖不到）。
