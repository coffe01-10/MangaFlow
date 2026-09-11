## 概要

第四轮审查-修复循环（审查区间 a08dae8..002613a 的唯一发现，P3，#388）：审批条「确认继续」按钮谓词收紧。

## 用户可见行为

曾为 generator.page 审批条选择过图片模型的用户，在供应商/模型被删除后（目录重取为空或换血）：

- 之前：空目录指引文案与**可点的**「确认继续」同屏；点击会把已失效的模型别名提交给审批接口，后端 resolve_model 拒绝后仅得一条错误提示。
- 之后：按钮与指引状态一致——选中别名不在当前可见目录（含别名豁免后的行）即禁用；目录换血时禁用直到重新选择。空目录原 #381 行为（指引 + 禁用 + 取消出路）不变，有模型且选择有效时解禁不变。

## 实现与测试

- `workflow-studio.tsx`：谓词 `!drawModel` → `drawModel === "" || !imageModels.some((m) => m.logical_alias === drawModel)`，附机制注释。
- `workflow-studio.test.tsx`：新增两条回归（曾选模型→目录清空→指引可见+禁用；曾选模型被删但目录非空→禁用直到重选 image.b 后解禁）。renderStudio 返回 QueryClient 以便确定性触发 models 重取（jsdom 下 visibilitychange 不点火 React Query 重取）；既有 SPA 卸载用例同步解构 `{ view }`。

## 验证

- `npx vitest run components/workflow-studio.test.tsx`：13/13 过（含 2 条新增）。
- `npm run check`：见 PR 门禁（本 PR 的完整运行，exit 0 后合并）。

## NOT RUN

真实供应商删除实机联动、Playwright e2e。

Closes #388.
