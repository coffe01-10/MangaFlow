# Frontend Polish Ledger — batch-20260904-1428

审计来源：6 个并行审计代理（UI Bug / Visual / UX / API Contract / A11y / Code Quality）+ 主 agent 真实浏览器探索。
Status: TODO / FIXING / DONE / VERIFIED / LEDGER-ONLY（记录不修，需后端或超出 polish 范围）

## Batch A — 功能/UX P1-P2（行为修复）

| ID | 页面/组件 | 文件:行 | 严重度 | 问题 | 状态 |
|----|-----------|---------|--------|------|------|
| A1 | 工作台侧栏 | workspace-chrome.tsx:83 + use-workspace-queries.ts:25-34 | P1 | 侧栏项目摘要随分区变化（"1 章"/"1 章 · 0 页已规划"/"漫画生产工作区"），由 query enabled 标志而非数据驱动 | DONE |
| A2 | 底部任务条 | use-jobs-workspace.ts:55-60 | P1 | jobs 查询仅在 assets/jobs/generate 启用；source/script/storyboard/library 上任务条灯不亮、轮询停止、显示冻结缓存 | DONE |
| A3 | 剧本编辑器 | script-editor.tsx + script-section.tsx | P1 | 场景/情节拍编辑中侧栏导航无守卫，草稿静默丢失（storyboard 有完整守卫，script 没有） | DONE |
| A4 | 分镜章节切换 | storyboard-section.tsx:65 | P1 | 章节 select 切换绕过 dirty 守卫，未保存分镜/对白丢失 | DONE |
| A5 | 资产重命名 | shared.tsx:48-58, use-assets-workspace.ts:165-171, assets-section.tsx:257-259 | P1 | 重命名失败静默：编辑器立即关闭、错误无处渲染、名称"回滚"无解释 | DONE |
| A6 | 参考资产分区 | assets-section.tsx:282,215,232,170-178 | P2 | 加载中闪现假空状态（"尚无人物"等在 isLoading 时渲染） | DONE |
| A7 | 系统设置 | app/settings/page.tsx:75,98 | P2 | providers/diagnostics 查询失败被掩盖为永久"读取中/正在检测" | DONE |
| A8 | 项目设置 | projects/[id]/settings/page.tsx:32-40,134 | P2 | project.isError → 永久 spinner，无重试 | DONE |
| A9 | 章节删除/撤回 | use-source-workspace.ts:71-87, source-section.tsx | P2 | deleteChapter/restoreChapter 无 onError 无横幅 | DONE |
| A10 | 风格分析 | use-assets-workspace.ts:314 | P2 | 创建风格后 analyzeStyle fire-and-forget 无 .catch，失败永久 DRAFT | DONE |
| A11 | 工作流恢复/取消 | workflow-studio.tsx:707,714 | P2 | restoreWorkflowVersion 无确认无错误处理（覆盖草稿）；cancelRun 同样 | DONE |
| A12 | 章节修订加载竞态 | use-source-workspace.ts:113-130 | P2 | 连续点击两个章节"修改"，旧响应覆盖新表单 | DONE |
| A13 | 任务选择不重置 | jobs-section.tsx:85 | P2 | 视图切换/单条归档后 selectedJobIds 残留 | DONE |
| A14 | 局部编辑分辨率 | local-edit-workspace.tsx:102,443,547 | P2 | 模型切换后 resolution 状态与 select 显示不一致 | DONE |
| A15 | 用量导出列表 | library-section.tsx:105 | P3 | exportsQuery 失败静默空白 | DONE |
| A16 | 工作区致命错误 | project-workspace.tsx:256-258 | P3 | "项目无法打开"无重试/返回 | DONE |

## Batch B — 视觉 P1-P2（CSS）

| ID | 位置 | 文件:行 | 严重度 | 问题 | 状态 |
|----|------|---------|--------|------|------|
| B1 | 全局排版 | globals.css:1445,1585-1588 | P1 | `small{12px!important}` 地板使其大于同容器 span/strong（10px/8px/7px），层级倒挂（metric strip、queue dock、generate 卡、章节行、side-card 头等多处） | DONE |
| B2 | ink 按钮 hover | globals.css:99 vs 1621-1626 | P1 | `.button.ink` hover 丢失朱红 4px 硬阴影换成灰色细影，与 .generate-one(!important 保留) 行为不一致 | DONE |
| B3 | 原作页对齐 | globals.css:429-435 | P1 | 章节登记表 full-bleed 无边框 vs 上方导入卡有边框+内边距，左缘错位 7px | DONE |
| B4 | 弱对比文本 | globals.css 多处 | P1 | ~25 个硬编码浅灰 <4.5:1（#756f65 4.41 / #888277 3.37 / #928c82 2.96 等）收敛到 --muted/--muted-strong | DONE |
| B5 | 方形图标按钮 | globals.css:1585 | P2 | 40px min-height 地板压扁 6 个方形按钮成矩形（icon-button/dialogue-delete/queue-dock-hide/project-nav-toggle/workspace-nav-collapse/inspector-close） | DONE |
| B6 | 供应商对话框按钮 | globals.css:1052 | P2 | .provider-dialog-actions 按钮无边框无背景（裸文本 40px 高） | DONE |
| B7 | 侧栏滚动条 | globals.css:236 等 | P2 | .workspace-steps 原生滚动条与水墨风格冲突；.canvas-viewport/.usage-table-scroll 同样未样式化 | DONE |
| B8 | usage KPI 卡 hover | globals.css:1782 | P2 | .usage-kpi-card 无 hover（同类 control-card 有） | DONE |
| B9 | storyboard 模块密度 | globals.css:1585 vs workflow-studio.module.css | P2 | 全局 40px/12px 地板覆盖 workflow-studio 紧凑深色画布设计（31-34px/7-8px） | DONE |
| B10 | 朱红小标签 | globals.css:275,539,347,1153,2014,1239 | P2 | vermillion 8-12px 标签文本 3.88:1 | DONE |
| B11 | 禁用不透明度 | globals.css 多处 | P3 | 8 种 disabled opacity（.35/.4/.42/.45/.48/.5/.55） | DONE |
| B12 | 状态徽章形状 | globals.css:90,1829 等 | P3 | pill vs 2px 方形两套 | LEDGER-ONLY |
| B13 | sticky 偏移 | globals.css:536,1741,951 | P3 | 5 个魔法数（58/78/72/92/66） | LEDGER-ONLY |
| B14 | 表格三套 padding | globals.css:1816,1819,2090 | P3 | usage-table vs usage-trend-table vs director-diff | LEDGER-ONLY |
| B15 | eyebrow 排版发散 | globals.css:166,955,1797,1850,868,1223,191,1107 | P2 | 同角色 kicker 8-12px/5 种字重字距 | LEDGER-ONLY（视觉语言层面，本批不动） |

## Batch C — A11y + 小缺陷

| ID | 位置 | 文件:行 | 严重度 | 问题 | 状态 |
|----|------|---------|--------|------|------|
| C1 | 分镜编辑表单焦点 | globals.css:552 | P1 | outline:none 无替代，键盘用户完全无焦点指示（.select-wrap select:207、workflow-studio.module.css:17,86 同样） | DONE |
| C2 | 任务行键盘 | jobs-section.tsx:73 | P2 | article onClick 无 role/tabIndex（内有按钮可替代） | DONE |
| C3 | 对白卡槽 | panel-inspector.tsx:118 | P2 | 可点击 div 无键盘路径 | DONE |
| C4 | React Flow 控件 | workflow-studio.tsx:687-688 | P2 | Controls/MiniMap 英文 aria-label | DONE |
| C5 | ConfirmDialog 焦点还原 | confirm-dialog.tsx:26-36 | P2 | 关闭后焦点落 body；无 Tab 陷阱 | DONE |
| C6 | usage 抽屉 Tab 陷阱 | usage-attempt-drawer.tsx:25-36 | P2 | Tab 可走到遮罩后页面 | DONE |
| C7 | 原作 textarea 名称 | source-section.tsx:47 | P2 | 仅 placeholder 无 aria-label；workflow-studio:715 两个 select 同样 | DONE |
| C8 | 失败通知 role | jobs-section.tsx:86, scene-workspace.tsx:569-576, workflow-studio.tsx:649 | P2 | 失败用 role=status（礼貌），应为 alert；scene notice 无任何 role | DONE |
| C9 | Invalid Date | connection-panel.tsx:286, app/settings/page.tsx:30-33 | P3 | 未守卫的 Date 解析 | DONE |
| C10 | 进度条钳制 | page.tsx:49,66 | P3 | selected>page_count 时 width>100% | DONE |
| C11 | 空项目卡文案 | page.tsx:33-45,230-232 | P3 | 有项目时仍显示"建立第一部漫画" | DONE |
| C12 | 校验列表 key | workflow-studio.tsx:690 | P3 | message 作 key 可重复 | DONE |
| C13 | job-row status-* | jobs-section.tsx:73 + globals.css | P2 | 插值类无任何 CSS 规则，各状态行外观相同 | DONE |
| C14 | 状态标签漂移 | workflow-studio.tsx:96-104 vs labels.ts:73-97 | P2 | 同一枚举两套中文（"等待"vs"等待中"） | DONE |
| C15 | 错误码映射 | jobs-section.tsx:77, usage-attempt-drawer.tsx:98 | P3 | 原始英文错误码直出，无 errorCodeLabels | DONE |
| C16 | 进度条语义 | jobs-section.tsx:76 | P3 | 无 role=progressbar | DONE |
| C17 | 非空断言/定时器 | workflow-studio.tsx:707,260,269 | P3 | workflowRef.current! 竞态；两个未清理 timer | DONE |
| C18 | 用量自定义区间 | usage-dashboard.tsx:48-52 | P3 | 缺日期静默查全部 | LEDGER-ONLY |
| C19 | director clarify 层 | director-workspace.tsx:293-317 | P2 | role=dialog 无模态行为无宣告 | DONE |
| C20 | 局部编辑蒙版键盘 | local-edit-workspace.tsx:485-492 | P2 | pointer-only 绘制 | LEDGER-ONLY（大改动） |
| C21 | 分镜快捷键文档 | storyboard 编辑器 | P3 | Tab/箭头/回车/Delete 无可见说明 | TODO（toolbar hint 一行） |
| C22 | noSelection 对比度 | workflow-studio.module.css:87 | P2 | 3.13:1 | DONE |
| C23 | status-chip.muted | globals.css:95 | P3 | ≈4.19:1 | DONE |

## Batch D — 死代码清理

| ID | 内容 | 状态 |
|----|------|------|
| D1 | workflow-editor 家族 20 文件（含假运行模拟器 + localStorage 持久化双胞胎），无路由引用；保留 workflow-studio 的 legacy 导入读取器 | DONE |
| D2 | globals.css 36 条已验证死规则（page-plan-*, panel-proof*, health-pill, verification-grid, character-sheet-builder 等） | DONE |

## LEDGER-ONLY（后端/契约/超大改动，本 PR 不动）

- favoriteCandidate 响应缺 page 上下文 → version_state 恒 CURRENT（generation.py:140-153 需后端补 page）
- 409 结构化 blockers[] 被 request() 拍平成单字符串（api.ts:1374-1387）
- adopt-reference 后端 no-op（uploads.py:319-333）
- TERMINAL_TASK_STATUSES 漏 NEEDS_REVIEW，三套终态清单（task-status.ts:20 vs jobs.py:31）
- 资产候选 version_state 恒 LEGACY_UNKNOWN（helpers.py:62-88）
- api.ts 6 个死客户端函数 + 后端 11 条无 UI 覆盖路由
- usageSummary from/to vs usageAttempts since/until 参数名不对称
- ModelCapability.catalog_id/connection_id 可空性未反映
- ui_poll_interval_seconds 实为毫秒（命名谎言，改契约会破坏）
- globals.css 三层重写全量合并（~150 死声明）— 仅做定点清理
- 轮询间隔魔数统一（POLL_FAST/DEFAULT_MS）— 低风险但触碰 7 文件，本批不做
- 5 种时间戳格式器统一 — 同上
- errorMessage() 提取器 18 处重复 — 同上
- 图标 20 种尺寸 → token 化 — 视觉语言层面
