**Found:** 第七轮终扫（a08dae8..a7b7e55）（2026-09-11）。

## 机制

`apps/web/components/workflow-studio.tsx:814`：`imageModels.length === 0` 指引分支的来源是 `models.data ?? []`（363-365 行）。models 查询（staleTime 30s）后台重试失败后 `data` 为 undefined → imageModels 为空 → 渲染"未配置可用图像模型：请先在设置中启用图像模型，或取消本次运行"——把可重试的瞬时错误（API 不可达/5xx）归因为配置问题，且隐藏了选择器。#381 的本意是空目录指引，不覆盖错误态。

## 修复面

分支区分 `models.isError && models.data === undefined`（渲染"模型目录读取失败"语义 + 重试入口）与真正的空目录（维持 #381 指引）。vitest 钉两条：models reject → 错误文案 + 重试入口可见、不出现"未配置"文案；models 成功空数组 → 维持 #381 行为回归。
