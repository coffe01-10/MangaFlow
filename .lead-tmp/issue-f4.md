**Found:** 第三轮只读审查，组长已亲自读代码核实（2026-09-11）。与 #380 同一 web 修复批次处理。

## 机制

`apps/web/components/project-workspace.tsx:335-343` 的深链缺页横幅判定：

```ts
const deepLinkPageMissing = Boolean(
  needsPages
    && deepLinkPageId
    && dismissedDeepLinkPageId !== deepLinkPageId
    && !pages.isLoading
    && pages.data !== undefined
    && pages.data.length > 0        // ← 缺陷前置
    && !pages.data.some((page) => page.id === deepLinkPageId),
);
```

`pages.data.length > 0` 使**当前选中章节一页都没有**时横幅永不出现：深链目标页不在本章（本章零页必然不在），生成台与分镜编辑器照旧静默落到本章第 1 页（#368 要防的无声错页），但提示被抑制。`pages.data !== undefined` 已足以判定"数据就绪"，`length > 0` 不是防误报所需——零页章节时"目标页不在当前章节"为真且值得提示。

## 修复面

去掉 `pages.data.length > 0` 前置，并在组件测试中钉住两条路径：有页章节缺目标页 → 横幅出现；零页章节 + 深链 → 横幅同样出现（回归钉）。无现有深链横幅测试（已核查 apps/web 下 *.test.ts(x) 无覆盖），需新建/扩展 colocated 测试。
