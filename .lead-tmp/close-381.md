**已修复（round 3 集成批次）**：`workflow-studio.tsx` 审批条在 `run.node_type === "generator.page" && imageModels.length === 0` 时渲染指引文案"未配置可用图像模型：请先在设置中启用图像模型，或取消本次运行。"+ `前往设置`（/settings）链接；不再渲染只有一个占位项的空选择器。按钮禁用逻辑保留但从此有可见原因，取消按钮本就在页脚（PAUSED 态）。

- 提交：0ab3d86。
- 验证：vitest 新增两条——空 imageModels 时指引可见、链接 href=/settings、不再渲染空选择器；有模型时无指引、选择器含模型项、未选禁用/选中解禁回归。apps/web 全量 484 项过（代理）。
- NOT RUN：真实设置页联动（模型启用后回流本页）实机验收。
