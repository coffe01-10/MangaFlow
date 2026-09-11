**已修复（round 4，PR 随后引用）**：按钮谓词收紧为"选中别名必须仍存在于当前 imageModels"（`drawModel === "" || !imageModels.some((m) => m.logical_alias === drawModel)`），空目录与残留别名两种形态同被覆盖；同屏矛盾消除——指引可见时按钮必禁用，目录换血时禁用直到重选。

- 提交：ca43448。
- 验证：vitest 13/13（新增"曾选模型→目录清空→指引+禁用"与"被删但目录非空→禁用直到重选"两条；renderStudio 改返 QueryClient 以确定性触发 models 重取）；npm run check exit 0。
- NOT RUN：真实供应商删除实机联动、e2e。
