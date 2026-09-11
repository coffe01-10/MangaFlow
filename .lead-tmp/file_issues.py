import subprocess, json, sys

issues = [
{
"title": "[P2][Web+Native] 审批栅栏 PAUSED 的运行在两端 UI 都无法取消，且 PAUSED 无状态标签，同 scope 被 409 锁死",
"body": """**Found:** web 端审查轮 2026-09-11（master 4aa1797）。

## 机制

- 后端把命中审批栅栏的 run 置为 PAUSED（`apps/api/app/services/workflow_engine/reconciliation.py:380-383、396-399、459-460`），而 `cancel_run` 明确接受 PAUSED（`lifecycle.py:461-466`）。
- web 取消按钮只在 `displayedRun?.status === "RUNNING"` 渲染（`apps/web/components/workflow-studio.tsx:783`）；桌面同款问题（`apps/desktop/native/Views/WorkflowView.cs:1290`，其契约注释 :1322-1323 写明"cancel_run 是唯一停止途径"）。
- `planning.py:86-95` 的重复运行守卫把 PAUSED 算活跃：同 scope 再起 run 被 409，文案"请先取消或等待完成"——指示用户做 UI 上做不到的事。
- `components/project-workspace/labels.ts:130-138` 的 `workflowRunStatusLabels` 缺 `PAUSED`，页脚裸显英文。
- `lib/api.ts:2045` 的 `retryWorkflowRun` 无任何调用方。
- generator.page 栅栏无可选图片模型时"确认继续"永久禁用，run 彻底卡死。

## 修复面

web：取消按钮条件加 PAUSED；补 PAUSED 标签；考虑接通 retry 入口或删除死方法。桌面：WorkflowView 同款条件修正（注意与 #342 修复分支的防抖身份改动协调，先合并再改）。检查：run 置 PAUSED 后取消按钮存在且 cancel 生效；409 文案可达的路径消失。"""
},
{
"title": "[P4][Web] 设置页 409 后不失效缓存，draft.version 停在旧值导致失败循环",
"body": """**Found:** web 端审查轮 2026-09-11。

## 机制

`apps/web/app/settings/page.tsx:47-63`（运行设置）与 `apps/web/app/projects/[id]/settings/page.tsx:57-75`（项目设置）的 PATCH 带乐观版本号；后端 409（`runtime_settings.py:187-188`、`routes/projects.py:509`）后 onError 只展示消息、不 invalidate 对应 query，`draft.version` 保持旧值，重试永远 409，直到窗口聚焦触发 refetch。桌面+web 同开的场景（7f4f4f5 关心的）必现。workflow-studio.tsx:322-324 已有正确范式（409 时 invalidate）。

## 修复面

两处 save 的 onError 对 409 invalidate 对应 query；组件测试断言 409 后重新拉取。"""
},
{
"title": "[P4][Web] ui_poll_interval_seconds 在 web 端是死配置：可编辑提交但零消费方",
"body": """**Found:** web 端审查轮 2026-09-11。

## 机制

`apps/web/app/settings/page.tsx:59、98` 可编辑并提交 `ui_poll_interval_seconds`，但 web 所有轮询硬编码（`lib/task-status.ts` 与各 `use-*-workspace.ts` 的 2000/2500/3000/4000ms）；桌面已全量接入（`apps/desktop/native/Services/PollInterval.cs`，注释明确"web 硬编码、桌面走设置值"）。设置项标签暗示驱动本 UI，实为仅桌面生效。

## 修复面

二选一：把该设置接入 web 轮询 helper（单一 hook 消费点），或改标签/帮助文本注明仅桌面生效并加注释钉住分歧。"""
},
{
"title": "[P4][Web] 深链：?stress=100 生产可达替换整个分镜编辑器；?page= 跨章静默回退无反馈",
"body": """**Found:** web 端审查轮 2026-09-11。

## 机制

1. `apps/web/components/project-workspace/storyboard-section.tsx:21-24、58`：`?stress=100` 在任何构建（含生产）把真实分镜编辑器整体替换为合成压测画布（只读不写，但 URL 即可达，无 NODE_ENV 门禁）。
2. `components/project-workspace.tsx:79、440` 与 `use-generation-workspace.ts:63`：`?page=` 指向他章页面时静默回退到本章第 1 页（`storyboard-editor/index.tsx:540-551` 会一直等 pages 包含目标而最终 no-op），无任何提示。

## 修复面

stress 参数限 `process.env.NODE_ENV === "development"`；`?page=` 解析归属章节或提示"目标页不在本章"。"""
},
{
"title": "[P4][Web] 场景工作区列表单页取 200 后静默截断",
"body": """**Found:** web 端审查轮 2026-09-11。

## 机制

`apps/web/components/project-workspace/scene-workspace.tsx:241-249`：`limit: 200` 无翻页循环；`api.sceneAssetsAll`（`lib/api.ts:1666-1676`）正是为消除这类截断而存在（包/资产列表同族问题此前已按缺陷修复）。>200 条时尾部资产不可见且计数只显示 200。

## 修复面

改用 `sceneAssetsAll` 或加翻页/超限提示。"""
},
{
"title": "[P4][Web] 剧本编辑表单的取消/X 按钮绕过脏确认，静默丢弃已修改草稿",
"body": """**Found:** web 端审查轮 2026-09-11。

## 机制

`apps/web/components/script-editor.tsx:198、222`：该文件对其他所有退出路径（切换编辑目标 :144-147、切章 :53-57、锚点导航 :91-102、beforeunload :85-89）都做脏草稿确认，唯独场景/情节拍编辑卡片自己的取消/X 直接 `setEditingScene(null); setSceneDraft(null)`。与 #341（桌面 F5 丢稿）同类保护的一致性漏洞。

## 修复面

取消按钮先走 `confirmDiscardDraft()`；组件测试断言脏状态下取消触发确认。"""
},
{
"title": "[P4][Native] ScriptView.Activate 双倍加载；故事板 preserve 刷新不清冲突条；ConfirmLeaveAsync 无无模态测试缝",
"body": """**Found:** #339/#341 修复轮的范围外发现（2026-09-11，分支 fix/native-storyboard-refresh 审查时确认）。

1. `ScriptView.Activate`（`ScriptView.cs:96-99`）：`SelectChapter(selected.Id)` 触发 SelectionChanged 时 `chapterId` 仍是旧值，处理器内先行 `chapterId=id; LoadScriptAsync()`，随后 Activate 再次 `LoadScriptAsync()`——每次激活双倍加载（8 个请求）。StoryboardView.Activate 有同问题的注释与修复（先赋值再拨选择器），ScriptView 未同步。
2. `StoryboardView.SelectPageAsync` 的 preserve 分支不清 `conflictBar`：409 后走 preserve 重载（含新的 RefreshAsync 路径）时冲突横幅滞留，直到下一次保存成功/弃稿。
3. `ScriptView.ConfirmLeaveAsync` 无无模态测试缝（StoryboardView 有 `LeaveConfirmOverride` 模式），headless 检查其离开确认会被 MessageBox 卡死。

## 修复面

ScriptView.Activate 先赋值再拨选择器（对齐 StoryboardView）；preserve 分支按状态重估 conflictBar 可见性；加 `LeaveConfirmOverride` 同款缝。"""
},
{
"title": "[P4][Desktop tooling] 残余小项：static-build tee 在 dist 缺失时先于锁失败；dist 读方无共享锁；X-Powered-By 未关；README D7 迁移数过期",
"body": """**Found:** #345-#350 修复轮的范围外发现（2026-09-11）。

1. `apps/desktop/scripts/build-frontend-static.sh` 的 `tee dist/static-build.log` 在 dist/ 不存在时失败（先于锁获取；锁内 `mkdir -p` 不覆盖它）。
2. dist/ 的读方（e2e 的 `_web_dist_dir`、D5 serving）未加共享锁——#350 只锁了写方，读窗口缩小但未消除。
3. `apps/web` 响应仍带 `X-Powered-By: Next.js`（`poweredByHeader:false` 可关）。
4. `apps/desktop/README.md` D7 行仍留"含 28 个迁移"历史参考值（#345 只修了 D5 行的 31）。

## 修复面

逐项小修；读方共享锁需评估死锁面（写方独占锁 + 读方共享锁的锁序）。"""
},
]

for issue in issues:
    payload = json.dumps(issue, ensure_ascii=False)
    result = subprocess.run(
        ["gh", "issue", "create", "--title", issue["title"], "--body-file", "-"],
        input=issue["body"], capture_output=True, text=True, encoding="utf-8"
    )
    print(result.stdout.strip() or result.stderr.strip())
