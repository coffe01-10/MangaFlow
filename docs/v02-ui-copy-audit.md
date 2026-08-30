# MangaFlow V02-50 UI 文案清单与精炼审计报告

- 任务：Issue #[0.2.0][V02-50] Audit and streamline user-facing UI copy
- 基线 SHA：`2613d978f64a2faee0282c6250501a453c13df0f`
- 分支：`codex/v02-50-ui-copy-audit`
- 性质：L1 文档审计，不修改任何现有 UI、TS/TSX、CSS、Python 或业务代码
- 输入依赖：`docs/v02-provider-settings-ui-audit.md` (Issue #40)、`docs/v02-provider-neutrality-audit.md` (Issue #39)、`docs/roadmap.md`

---

## 1. 执行摘要

在对 MangaFlow 0.1.0 MVP 代码基线（覆盖 `apps/web/app/`、`apps/web/components/`、`apps/web/lib/` 及相关后端路由和错误）进行完整审查后，发现当前 UI 文案普遍存在以下八类核心问题：

1. **状态被多重重复表达（提示过载）**：分镜编辑中每次保存都弹出包含当前版本号和受影响候选数量的完整句子；工作流画布中状态条、节点头和 Toast 三处同时表达同一状态。
2. **向普通用户解释内部实现机制（系统自述）**：如“系统先无损分段，再根据文字和剧本长度动态计算页数”、“凭据仅保存在服务端，本项目不会把密钥发往浏览器”、“等待 Worker 生成”等，给用户呈现了算法流程和架构细节，却未指引下一步行动。
3. **英文枚举与内部技术术语直接展示**：
   - 候选版本状态直接显示 `CURRENT`、`STALE`、`LEGACY_UNKNOWN`；
   - 人物出镜状态直接显示 `VISIBLE`、`OFFSCREEN`、`MENTIONED`；
   - 质检结果直接显示 `PASS`、`ACCEPTABLE`、`MATCH`；
   - 任务状态显示 `WAITING_APPROVAL`、`RUNNING`；
   - 供应商风险等级直接显示 `OFFICIAL`、`GATEWAY`、`THIRD_PARTY`、`LOW`。
4. **长段免责声明与宣传式文案长期占据工作台黄金空间**：如首页“从文字开始，把故事画出来”、“面向连续漫画生产的结构化工作流”、“原作导入...均已接入真实工作流（honesty-note）”，以及项目设置页中的长篇副标，严重稀释了有效信息。
5. **空状态缺少行动入口或恢复动作**：部分空列表仅写“尚无数据”或“当前筛选条件下没有供应商”，没有提供“创建”、“重置筛选”或指引前置环节的按钮。
6. **成功提示重复描述加密、缓存、路由等技术细节**：如“API Key 已加密保存；浏览器不会读取明文”、“已保存不可变版本”等，缺乏用户视角的精炼反馈。
7. **错误信息只描述失败事实，缺乏可执行的恢复路径**：如“请求数据不符合要求”、“生成失败”，没有告知用户缺少何种资产或应如何修复。
8. **供应商特权与品牌专属措辞遗留**：仍保留独立的“VERTEX AI / LEGACY”卡片、硬编码模型名称（Gemini 3.5 Flash / NB 2 / NB Pro）以及“Vertex 保留为原生连接”等不符合中立供应商规范的措辞。

本审计报告建立了完整的文案审查清单、统一术语表、状态规范与实施切片，旨在删除一切无效“嘀咕”，同时在最靠近交互动作的确认层中严格保留真实计费、数据外发和不可逆操作等关键风险提示。

---

## 2. 审计范围与方法

### 2.1 审计范围

- **前端页面与组件**：
  - 全局布局与导航：`apps/web/app/layout.tsx`、`apps/web/components/shell.tsx`
  - 首页与工作台概览：`apps/web/app/page.tsx`
  - 使用帮助与排障：`apps/web/app/help/page.tsx`
  - 系统设置与供应商平台：`apps/web/app/settings/page.tsx`、`apps/web/components/provider-management.tsx`
  - 项目设置与删除确认：`apps/web/app/projects/[id]/settings/page.tsx`
  - 项目主工作区框架与快捷栏：`apps/web/components/project-workspace.tsx`、`workspace-chrome.tsx`、`labels.ts`、`display.ts`、`shared.tsx`
  - 原作与修订：`components/project-workspace/source-section.tsx`
  - 漫画剧本：`components/project-workspace/script-section.tsx`、`components/script-editor.tsx`
  - 分页与分镜：`components/project-workspace/storyboard-section.tsx`、`components/storyboard-editor.tsx`
  - 参考资产（人物/服装/风格/原始素材）：`components/project-workspace/assets-section.tsx`、`components/asset-production-panel.tsx`
  - 单页生成、候选与门禁：`components/project-workspace/generate-section.tsx`、`components/production-readiness.tsx`
  - 质量检查：`components/project-workspace/inspection-panel.tsx`
  - 任务中心：`components/project-workspace/jobs-section.tsx`
  - 生成素材库与导出：`components/project-workspace/library-section.tsx`
  - 流程编排（工作流工作室）：`apps/web/components/workflow-studio.tsx`
- **前端工具与状态定义**：
  - `apps/web/lib/api.ts`
  - `apps/web/lib/generation-rules.ts`
  - `apps/web/lib/task-status.ts`
  - `apps/web/lib/workflow-draft-save.ts`
- **后端用户可见错误与异常**：
  - `apps/api/app/api/routes/` 下的 `projects.py`、`providers.py`、`generation.py`、`assets.py`、`scripts.py`、`storyboards.py`、`workflows.py`、`inspections.py`、`export.py`
  - `apps/api/app/request_limits.py` 与 `apps/api/app/services/content_workflow.py`

### 2.2 审计方法与分类标准

所有审查项均以基线 `2613d978f64a2faee0282c6250501a453c13df0f` 的精确 `文件:行号` 为锚点，并分为以下五种操作分类：

- **KEEP**：原样保留。语义清晰、准确表达状态且位于合理层级。
- **REFINE**：精炼但保留语义。删除啰嗦自述、精简修饰词、映射英文枚举为标准中文，或补充恢复动作。
- **DELETE**：删除。无效嘀咕、自吹自擂、重复标题、暴露后端底层机制但对用户决策无益的冗余说明。
- **MOVE**：移出主路径。将次要说明移至 Tooltip、帮助侧栏或二级确认层，释放主界面空间。
- **ADD**：补充文案。缺少必要的安全风险提示、空状态引导或字段校验指引。

---

## 3. 文案分类总表

| ID | 文件:行号 | 页面/区域 | 当前文案 | 分类 | 建议文案 | 理由 | 风险等级 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C-001 | `apps/web/app/page.tsx:27-34` | 首页 / 顶栏状态徽标 | `Vertex 已验证` / `Vertex 离线` / `{healthy} 个 AI 连接健康` | REFINE | `{healthy} 个连接可用` / `全部连接离线` | 消除 Vertex 品牌特权，统一为连接状态 | 低 |
| C-002 | `apps/web/app/page.tsx:45-46` | 首页 / 空项目卡片 | `建立第一部漫画`<br>`设置项目、模型与工作模式` | REFINE | `新建漫画项目`<br>`从剧本到连续分镜页面` | 精炼动作，消除繁杂说明 | 低 |
| C-003 | `apps/web/app/page.tsx:129` | 首页 / 创建项目抽屉 | `图片模型不设默认主次；进入工作区后必须显式选择，以保持项目画风一致。` | DELETE | *(删除)* | 抽屉内长篇说教，工作区已有明确选模提示 | 低 |
| C-004 | `apps/web/app/page.tsx:139` | 首页 / 创建项目抽屉 | `凭据仅保存在服务端，本项目不会把密钥发往浏览器。` | MOVE | 移至系统设置凭据说明或全局安全指南 | 创建项目阶段不必每次重复技术自述 | 低 |
| C-005 | `apps/web/app/page.tsx:181-184` | 首页 / Hero 区域 | `从文字开始，把故事画出来。`<br>`面向连续漫画生产的结构化工作流。角色、服装、分镜与对白，每一步都可确认、可锁定、可修复。` | REFINE | 主标题：`漫画生产台`<br>副标题：`从小说剧本到分镜画面的结构化工作流` | 消除口号式宣传语，使界面更具生产工具感 | 低 |
| C-006 | `apps/web/app/page.tsx:209-223` | 首页 / Vertex 独立侧栏卡 | `VERTEX AI / LEGACY` 专属卡片及 Gemini 3.5 Flash / NB 2 / NB Pro 等硬编码模型信息 | REFINE | 改造为 `AI 供应商与模型状态` 统一卡片，展示已连接供应商与模型概览 | 落实供应商平权，消除 Vertex 特权卡 | 中 |
| C-007 | `apps/web/app/page.tsx:232` | 首页 / 生产闭环下方 | `原作导入、动态分页、逐页抽卡、收藏采用与批次素材库均已接入真实工作流。` | DELETE | *(删除)* | 典型的自我吹嘘式（honesty-note）嘀咕，对用户无用 | 低 |
| C-008 | `apps/web/app/help/page.tsx:20` | 使用帮助 / 顶部说明 | `每个入口都拥有独立网址，可以刷新、收藏和通过浏览器前进后退。` | DELETE | *(删除)* | 向用户解释 Web 路由基本特性，毫无意义 | 低 |
| C-009 | `apps/web/app/help/page.tsx:22` | 使用帮助 / 排障卡片 | `VERTEX AI / 故障排查`：凭据显示断开时怎么办？ | REFINE | `供应商连接故障排查`：连接不可用时如何处理？ | 泛化为通用供应商排障指南 | 低 |
| C-010 | `apps/web/app/settings/page.tsx:67` | 系统设置 / 顶栏 | `SYSTEM / CONTROL ROOM` · `系统设置与运行诊断` | REFINE | `系统设置与运行诊断` | 移除无意义英文小标 | 低 |
| C-011 | `apps/web/app/settings/page.tsx:81-94` | 系统设置 / Vertex 独立卡 | `VERTEX AI / PROVIDER` 独立四按钮卡片 | MOVE | 整合并入统一 `ProviderManagement` 平台组件，终态删除协议专属 renderer | 消除双重配置入口 | 中 |
| C-012 | `apps/web/app/settings/page.tsx:92` | 系统设置 / 验证中 | `正在执行显式验证，请勿关闭页面…` | REFINE | `正在测试连接…` | 移除开发者用语“显式验证”及不必要的恐慌警告 | 低 |
| C-013 | `apps/web/app/settings/page.tsx:110` | 系统设置 / 存储说明 | `凭据路径、私钥、令牌和 Redis 地址不会通过此接口返回。` | DELETE | *(删除)* | 向前端解释 API 过滤实现，属技术自述 | 低 |
| C-014 | `apps/web/components/provider-management.tsx:60` | 供应商平台 / 密钥保存 | `API Key 已加密保存；浏览器不会读取明文` | REFINE | `密钥已保存` | 成功提示只说结果，不谈底层加密实现 | 低 |
| C-015 | `apps/web/components/provider-management.tsx:80` | 供应商平台 / 模型同步 | `已同步 N 个模型；名称推断结果需验证后才参与自动路由` | REFINE | `已同步 N 个模型。推断能力测试通过后可用于自动路由。` | 语言精炼，动作导向 | 低 |
| C-016 | `apps/web/components/provider-management.tsx:99` | 供应商平台 / 探测结果 | `${probe.probe_type}：${probe.status} · ${probe.latency_ms ?? "—"} ms` | REFINE | `连接测试通过 · ${latency} ms` / `测试失败：${error_message}` | 隐藏内部探测枚举 | 低 |
| C-017 | `apps/web/components/provider-management.tsx:128` | 供应商平台 / 手动添加模型 | `手动模型已加入；请先测试能力再用于自动路由` | REFINE | `模型已添加。测试通过前不会进入自动路由。` | 清晰明确规则 | 低 |
| C-018 | `apps/web/components/provider-management.tsx:176` | 供应商平台 / 图片测试 | `window.confirm("图片能力测试会产生一次 1K 调用费用，是否继续？")` | REFINE | 对话框模态：`图片测试将调用外部模型并产生费用。确认继续？` | 替换原生 confirm 为规范模态框 | 高 |
| C-019 | `apps/web/components/provider-management.tsx:178` | 供应商平台 / 不支持操作 | `协议不支持` (disabled 按钮) | DELETE | *(不渲染该按钮)* | 不支持的能力不应渲染为置灰按钮霸占空间 | 低 |
| C-020 | `apps/web/components/provider-management.tsx:252` | 供应商平台 / 标签直出 | `{provider.category} · {provider.risk_label}` (如 `GATEWAY` · `LOW`) | REFINE | 映射为中文：`网关 · 低风险` / `官方 · 经过验证` | 消除英文枚举直出 | 低 |
| C-021 | `apps/web/components/provider-management.tsx:310` | 供应商平台 / 工具栏说明 | `OpenAI / Anthropic；Vertex 保留为原生连接。` | DELETE | *(移出工具栏)* | 避免在窄桌面挤占搜索筛选控件 | 低 |
| C-022 | `apps/web/components/project-workspace/labels.ts:20` | 项目工作区 / 侧栏导航项 | `["generate", "单页生成", "抽卡、收藏、采用", "05", Sparkles]` | REFINE | `["generate", "单页生成", "候选、收藏、采用", "05", Sparkles]` | 统一消除“抽卡”同义词 | 低 |
| C-023 | `apps/web/components/project-workspace/labels.ts:45` | 项目工作区 / 生成类型 | `PAGE: "页面抽卡"` | REFINE | `PAGE: "单页候选生成"` | 统一规范术语 | 低 |
| C-024 | `apps/web/components/project-workspace/source-section.tsx:41` | 原作导入 / 标题 | `SOURCE / 原作` · `完整导入，不压缩故事` | REFINE | `原作与修订` · `导入章节原文` | 消除口号式副标 | 低 |
| C-025 | `apps/web/components/project-workspace/source-section.tsx:44` | 原作导入 / 输入框占位符 | `粘贴完整章节。系统先无损分段，再根据文字和剧本长度动态计算页数。` | REFINE | `粘贴或上传章节文本（支持 TXT、Markdown）` | 移除对分段计算算法的自我解释 | 低 |
| C-026 | `apps/web/components/project-workspace/source-section.tsx:45` | 原作导入 / 提示 | `不会限制总页数 · 单页硬上限 180 个中文字符` | REFINE | `支持长章节导入；每页最多 180 字` | 保留服务端真实硬上限，避免把阻断条件误写成建议 | 低 |
| C-027 | `apps/web/components/project-workspace/source-section.tsx:52` | 原作导入 / 删除章节 | `window.confirm("删除后会暂时隐藏该章节，可立即撤回。继续吗？")` | REFINE | 确认弹窗：`删除章节“{title}”？剧本与分镜将一并移入回收站。` | 明确删除范围与后果 | 中 |
| C-028 | `apps/web/components/project-workspace/script-section.tsx:31` | 漫画剧本 / 标题 | `SCREENPLAY / 漫画剧本` · `先写场景与情节拍，再进入分页` | REFINE | `漫画剧本` · `场景与情节拍编排` | 消除步骤说教 | 低 |
| C-029 | `apps/web/components/project-workspace/script-section.tsx:32` | 漫画剧本 / 空状态说明 | `点击“生成漫画剧本”，默认文字模型会逐段补充可视化动作、场景、对白、旁白、情绪和翻页悬念，不会压缩原文。` | REFINE | `基于章节原文自动生成场景划分、情节拍动作与对白。` | 删除冗长的生成特性自我陈述 | 低 |
| C-030 | `apps/web/components/project-workspace/storyboard-section.tsx:44` | 分页分镜 / 标题 | `PAGE CAPACITY / 动态分页` · `内容有多少，页面就有多少` | REFINE | `分页与分镜` · `页面规划与分镜脚本` | 消除口号式副标 | 低 |
| C-031 | `apps/web/components/project-workspace/storyboard-section.tsx:46` | 分页分镜 / 空状态 | `先完成漫画剧本；系统按场景切换、动作复杂度、对白和气泡容量拆页。` | REFINE | `尚未生成分镜。请先在“漫画剧本”完成剧本生成。` | 删除对拆页算法的自述，给出清晰入口引导 | 低 |
| C-032 | `apps/web/components/project-workspace/assets-section.tsx:172` | 角色资产 / 编辑区说明 | `剧本统一使用主要姓名；固定特征和禁止改变项会进入每次生图提示。` | REFINE | `固定特征与禁止改变项将作为提示词负面约束。` | 精简直接 | 低 |
| C-033 | `apps/web/components/project-workspace/assets-section.tsx:173` | 角色资产 / 选模器标题 | `项目视觉模型（必须显式选择，并在各生成页面保持一致）` | REFINE | `生图模型` | 移除括弧内的长篇大论 | 低 |
| C-034 | `apps/web/components/project-workspace/assets-section.tsx:199` | 服装资产 / 操作指引 | `上传服装参考后会自动加入当前档案；也可以在下方素材卡中加入、移除，再点击保存绑定。` | DELETE | *(删除)* | 界面操作显而易见，属于冗余指引 | 低 |
| C-035 | `apps/web/components/project-workspace/assets-section.tsx:213` | 风格资产 / 创建提示 | `这里的模式只用于下面正在创建的新档案，并会记住本项目上次选择` | DELETE | *(删除)* | 解释内部记忆机制的无用废话 | 低 |
| C-036 | `apps/web/components/project-workspace/assets-section.tsx:220` | 风格资产 / 档案切换提示 | `下方开关修改的是该档案本身，不会改变上方新档案表单。` | DELETE | *(删除)* | 解释 UI 状态隔离的自述 | 低 |
| C-037 | `apps/web/components/project-workspace/assets-section.tsx:229` | 原始素材 / 上传提示 | `人物图会和选中的主要姓名绑定，不会只依赖文件名猜测身份。` | REFINE | `上传图片将绑定为【${name}】的参考素材` | 消除自我标榜与吐槽式表达 | 低 |
| C-038 | `apps/web/components/project-workspace/assets-section.tsx:255` | 原始素材 / 用途解释 | `{ CHARACTER_REFERENCE: "绑定主要姓名与绰号，用于保持脸、发型和体型一致。"... }` | DELETE | *(删除)* | 列表中重复解释常识 | 低 |
| C-039 | `apps/web/components/project-workspace/assets-section.tsx:269` | 原始素材 / 删除素材 | `window.confirm("删除该素材及其候选记录，并解除已有绑定？")` | REFINE | 确认弹窗：`删除素材“{name}”？已绑定的角色/服装将解除关联。` | 规范危险操作确认层 | 中 |
| C-040 | `apps/web/components/project-workspace/generate-section.tsx:99` | 单页生成 / 标题 | `DRAW / 单页抽卡` · `每次只生成 1 页` | REFINE | `单页生成` · `生成与采纳页面候选` | 统一术语，删除小标 | 低 |
| C-041 | `apps/web/components/project-workspace/generate-section.tsx:141` | 单页生成 / 批次信息 | `每个候选记录实际供应商与模型 · 收藏不等于采用` | DELETE | *(删除)* | 嘀咕式内部规则陈述 | 低 |
| C-042 | `apps/web/components/project-workspace/generate-section.tsx:142` | 单页生成 / 候选卡片版本 | `{candidate.version_state}` (如 `CURRENT`/`STALE`) | REFINE | 映射为中文徽章：`当前版本` / `分镜已更新(待复查)` / `历史版本` | 消除英文枚举直出 | 低 |
| C-043 | `apps/web/components/project-workspace/generate-section.tsx:142` | 单页生成 / 暂选候选 | `window.confirm("请确认页面文字已人工校对。暂选后还需要完成视觉检查，才能进入下一页或导出。是否继续？")` | REFINE | 确认弹窗：`暂选候选：请确认文字对白无误。暂选后仍需通过视觉检查才能导出。` | 保留暂选与最终生产就绪的差异 | 高 |
| C-044 | `apps/web/components/project-workspace/generate-section.tsx:142` | 单页生成 / 删除候选 | `window.confirm("删除这个候选？收藏状态也会一并移除。")` | REFINE | 确认弹窗：`从候选列表隐藏 P.{num} #{ordinal}？生成文件和任务记录仍会保留。` | 对齐当前软删除语义，不声称物理清除图片与审计记录 | 中 |
| C-045 | `apps/web/components/project-workspace/generate-section.tsx:154` | 单页生成 / 门禁状态 | `{pageProduction?.ready ? "READY" : pageProduction?.state ?? "LOADING"}` | REFINE | 映射为中文：`生产就绪` / `待质检` / `待采用` / `待修复` | 消除技术枚举直出 | 低 |
| C-046 | `apps/web/components/project-workspace/inspection-panel.tsx:25` | 质检面板 / 标题 | `AI QUALITY CHECK` · `候选视觉检查`<br>`检查说话人归属、角色、服装、道具和连续性；文字由人工校对。` | REFINE | `视觉一致性检查` | 移除英文大标和冗长副标 | 低 |
| C-047 | `apps/web/components/project-workspace/inspection-panel.tsx:31` | 质检面板 / 结果标签 | `{inspection.outcome}` (如 `PASS` / `FAIL`) | REFINE | 映射为中文：`通过` / `符合预期` / `存在差异(需修复)` | 消除英文枚举直出 | 低 |
| C-048 | `apps/web/components/project-workspace/jobs-section.tsx:75` | 任务中心 / 任务状态 | `{job.status}` (如 `RUNNING` / `FAILED`) | REFINE | 映射为中文：`运行中` / `已完成` / `失败` / `等待确认` | 消除英文枚举直出 | 低 |
| C-049 | `apps/web/components/project-workspace/jobs-section.tsx:77` | 任务中心 / 费用估算 | `· 估算值不等于供应商账单` | KEEP | 保留为费用提示 Tooltip 或辅助文本 | 属于必要的费用风险文案，保留但精炼 | 高 |
| C-050 | `apps/web/components/project-workspace/jobs-section.tsx:78` | 任务中心 / 彻底删除任务 | `window.confirm("仅无候选、生成记录、工作流或任务依赖的失败任务可以彻底删除。继续吗？")` | REFINE | 确认弹窗：`彻底删除任务记录？相关生成的临时日志将被清理。` | 移除内部表依赖关系的啰嗦解释 | 中 |
| C-051 | `apps/web/components/project-workspace/library-section.tsx:81` | 素材库 / 标题 | `LIBRARY / 批次素材库` · `保存每一次值得比较的结果` | REFINE | `生成素材库` · `历史生成候选与导出` | 消除口号式副标 | 低 |
| C-052 | `apps/web/components/project-workspace/library-section.tsx:98` | 素材库 / 撤回暂选 | `window.confirm("撤回暂选后，候选图片和生成记录仍会保留，后续页面将标记为待复查。是否继续？")` | REFINE | 确认弹窗：`撤回当前采用版本？本页生产门禁将重置为“待采用”。` | 准确传达门禁影响 | 高 |
| C-053 | `apps/web/components/project-workspace/shared.tsx:75` | 候选卡片 / 加载占位 | `等待 Worker 生成` | REFINE | `正在生成画面…` | 消除后端架构专有名词“Worker” | 低 |
| C-054 | `apps/web/components/production-readiness.tsx:69` | 生产准备 / 标题 | `PRODUCTION CHECK / 页面生产准备` · `服务器统一判断这页能不能正式生成` | REFINE | `页面生产准备` | 消除自述式副标 | 低 |
| C-055 | `apps/web/components/production-readiness.tsx:91` | 生产准备 / 人物要求 | `只有 VISIBLE 人物要求参考图` | REFINE | `仅对实际出镜人物校验参考图` | 消除英文枚举混杂 | 低 |
| C-056 | `apps/web/components/production-readiness.tsx:105` | 生产准备 / 执行器状态 | `{readiness.worker.executor} · {readiness.worker.queue_mode}` (如 `SpawnWorker · AUTO`) | REFINE | `执行器就绪` / `队列正常` | 技术堆栈不向普通用户直出 | 低 |
| C-057 | `apps/web/components/script-editor.tsx:103` | 剧本编辑器 / 提示条 | `导演修订模式 / 场景与情节拍可直接修改；来源区间保持只读，避免剧情丢失。` | DELETE | *(删除)* | 无实质行动的自述提示条 | 低 |
| C-058 | `apps/web/components/storyboard-editor.tsx:120` | 分镜编辑器 / 保存提示 | `已保存 · 当前 V${version} · 将使 ${count} 个候选过期` | REFINE | `分镜已保存` (若版本升级则右上角徽标更新，不再每次弹长 Toast) | 消除过度频繁的候选过期长警报 | 低 |
| C-059 | `apps/web/components/storyboard-editor.tsx:244` | 分镜编辑器 / 状态栏 | `来自漫画剧本：{scene_ids.length} 个场景 · {beat_ids.length} 个情节拍；修改不会删除已有候选。` | REFINE | `第 {page_number} 页 · {chars}/180 字 · {bubbles}/8 气泡` | 精简冗余文本，保留容量指示 | 低 |
| C-060 | `apps/web/components/storyboard-editor.tsx:261` | 分镜编辑器 / 角色状态说明 | `只有“实际出镜”会要求人物与服装参考；画外音和被提及人物不会阻塞生图。` | MOVE | 移至表单字段 Tooltip `(?)` 中 | 释放主编辑区空间 | 低 |
| C-061 | `apps/web/components/workflow-studio.tsx:569` | 工作流工作室 / 旧草稿提示 | `发现升级前保存在当前浏览器的工作流草稿...它不影响现在的服务端草稿...` | REFINE | `检测到本地历史草稿`：提供【另存为新流程】与【忽略】 | 精简自述长句 | 低 |
| C-062 | `apps/web/app/projects/[id]/settings/page.tsx:76` | 项目设置 / Hero 副标 | `控制每一次生成，不是控制你的故事。`<br>`这里仅保存当前项目的制作策略。图片模型仍在每个候选生成前单独选择，不设置主次。` | REFINE | `项目制作策略与参数配置` | 消除口号与说教 | 低 |
| C-063 | `apps/web/app/projects/[id]/settings/page.tsx:97` | 项目设置 / 文字校对说明 | `采用候选前必须明确确认页面文字，不再运行 OCR 或自动文字修复。` | REFINE | `文字对白在采用候选时由人工核对确认。` | 移除技术历史变迁说明（“不再运行 OCR...”） | 低 |
| C-064 | `apps/web/app/projects/[id]/settings/page.tsx:106` | 项目设置 / 选模下拉选项 | `兼容默认 · Gemini 3.5 Flash` | REFINE | `Gemini 3.5 Flash (Google Vertex)` / `按模型目录规范名称` | 消除特权“兼容默认”措辞 | 低 |
| C-065 | `apps/web/app/projects/[id]/settings/page.tsx:112` | 项目设置 / 删除项目 | `输入项目名称 + window.confirm` 双重确认 | REFINE | 模态框：`输入“{project.name}”确认删除。项目将被归档。` | 移除多余的二次 window.confirm 弹窗 | 高 |

---

## 4. “必须删除”的文案清单与审查原则

在产品文案治理中，以下九类文案必须无条件删除或彻底精炼：

1. **重复标题与同义复述**：
   - 例：`apps/web/components/production-readiness.tsx:69` 标题为“页面生产准备”，副标题紧接着写“服务器统一判断这页能不能正式生成”。
   - **处理**：删除副标题，标题已完全表达含义。
2. **“这是一个……”“这里可以……”式产品自述与教学碎念**：
   - 例：`apps/web/components/project-workspace/assets-section.tsx:199` “上传服装参考后会自动加入当前档案；也可以在下方素材卡中加入、移除，再点击保存绑定。”
   - **处理**：删除。界面已有清晰的拖拽区域和按钮列表，不需要多余的文字解说。
3. **向用户解释后端实现算法与底层架构**：
   - 例：`apps/web/components/project-workspace/source-section.tsx:44` “粘贴完整章节。系统先无损分段，再根据文字和剧本长度动态计算页数。”
   - 例：`apps/web/components/project-workspace/shared.tsx:75` “等待 Worker 生成”。
   - **处理**：删除实现原理解释，替换为对用户友好的行动或状态文本（如“正在生成画面…”）。
4. **无法改变用户决策的免责声明式长文本**：
   - 例：`apps/web/app/page.tsx:232` “原作导入、动态分页、逐页抽卡、收藏采用与批次素材库均已接入真实工作流。”（honesty-note）。
   - **处理**：彻底删除。
5. **过度表达技术安全机制的自夸文案**：
   - 例：`apps/web/components/provider-management.tsx:60` “API Key 已加密保存；浏览器不会读取明文”。
   - 例：`apps/web/app/settings/page.tsx:110` “凭据路径、私钥、令牌和 Redis 地址不会通过此接口返回。”
   - **处理**：简化为标准状态“密钥已保存”，删除多余的技术自述。
6. **英文枚举与技术字段直接泄露到 UI**：
   - 例：`candidate.version_state`（`CURRENT`/`STALE`/`LEGACY_UNKNOWN`）、`inspection.outcome`（`PASS`/`FAIL`）、`job.status`（`RUNNING`/`WAITING_APPROVAL`）。
   - **处理**：一律通过统一纯函数词典转换为专业中文。
7. **置灰按钮占用空间且带有无意义文案**：
   - 例：`apps/web/components/provider-management.tsx:178` Anthropic 连接下图片测试按钮显示为 disabled 且文字为“协议不支持”。
   - **处理**：不满足能力时不渲染该按钮，不使用 disabled 占位。
8. **空泛无恢复方案的错误提示**：
   - 例：`apps/web/lib/api.ts:790` “请求数据不符合要求”。
   - **处理**：精炼为包含字段或具体对象的错误提示，并指引下一步。
9. **宣传式、浮夸或供应商特权词汇**：
   - 例：“从文字开始，把故事画出来”、“强大智能”、“兼容默认 · Gemini”。
   - **处理**：一律删除宣传形容词，统一为中立、严谨的生产工具措辞。

---

## 5. “必须保留”的风险文案清单

任何“去废话”操作都**不得**删除或弱化以下八类关键风险提示。但必须遵循原则：**将风险提示放置在最靠近用户触发动作的确认层或行内，而非页面顶部长期占据空间**。

### 5.1 真实调用与费用风险

- **图片生成与模型测试计费**：
  - *触发点*：设置页图片冒烟测试、概念图生成、风格测试图生成、单页候选生成。
  - *保留规范*：模态框明确提示“将调用外部模型并产生 1 次 [1K/2K/4K] 图片费用。确认继续？”
  - *成本看板/任务中心*：保留“估算费用仅供参考，以供应商实际账单为准”。

### 5.2 数据出境与外部供应商风险

- **数据外发**：
  - *触发点*：首次配置第三方网关或自定义 API 连接。
  - *保留规范*：“提示词与参考图片将发送至该供应商服务器进行处理。”

### 5.3 凭据与安全边界

- **凭据写权限与脱敏**：
  - *触发点*：服务端未配置凭据主密钥（`credential_writable === false`）。
  - *保留规范*：禁用输入框并明确提示“服务端未配置凭据主密钥，密钥仅供只读”。

### 5.4 删除与停用的不可逆后果

- **删除密钥/自定义供应商**：
  - *保留规范*：“删除密钥【{label}】后密文无法恢复；正在使用该密钥的生成任务将失败。”
  - *保留规范*：“删除供应商将一并移除其所有连接与已同步模型。”
- **删除素材与角色**：
  - *保留规范*：“删除素材将解除已有关联，已有生成候选的参考溯源将标记为已删除。”
- **停用供应商**：
  - *保留规范*：“停用后，该供应商将无法用于自动路由或手动生成。”

### 5.5 生产门禁与采用后果

- **候选采纳与覆盖**：
  - *保留规范*：“采用新候选将替换当前页面成品，并使后续依赖该页的连续性检查进入【待复查】状态。”
  - *旧版本沿用*：“旧候选基于 V{old} 分镜，确认沿用后需重新通过视觉一致性检查才能导出。”

### 5.6 生产导出门禁拦截

- **整章导出拦截**：
  - *保留规范*：“当前章节存在 {N} 页未通过生产门禁（缺少人工校对或视觉质检未通过），无法导出成品。”

### 5.7 本地数据与生命周期安全

- **项目归档与删除**：
  - *保留规范*：“项目将从工作台归档。本地生成文件将保留以供恢复。”

---

## 6. 统一术语表

为确保 MangaFlow 全站产品文案的一致性，制定以下术语标准。后续所有 UI 开发与测试断言必须以此表为准：

| 术语 ID | 推荐中文 | 禁用/不推荐同义词 | 英文枚举/内部代码 | 使用场景与定义 | 允许显示英文? |
| --- | --- | --- | --- | --- | --- |
| T-01 | **供应商** | 渠道、服务商、AI平台 | `Provider`, `AIProvider` | 提供模型服务的外部厂商或本地 CLI 通道（如 OpenAI、Anthropic、Vertex AI、Codex CLI） | 否 |
| T-02 | **连接** | 节点、通道、接入点 | `Connection`, `ProviderConnection` | 供应商下的具体 API 配置或环境凭据实例 | 否 |
| T-03 | **模型** | 引擎、AI、算法 | `Model`, `AIModel` | 具备特定能力的具体 AI 模型实体 | 否 (模型ID除外) |
| T-04 | **模型目录** | 模型列表、可用模型库 | `Model Catalog` | 系统已配置或已发现的所有模型清单 | 否 |
| T-05 | **可展示模型** | 前端启用模型、显式模型 | `Display Models` | 用户在创作界面（生成台、工作流、项目设置）中勾选可见的模型集合（V02-12） | 否 |
| T-06 | **已验证** | 冒烟成功、已测试通过 | `VERIFIED` | 通过连通性与能力测试，具备自动路由资格的模型置信度状态 | 否 |
| T-07 | **待验证** | 未测试、推断模型 | `MANUAL`, `DECLARED`, `INFERRED` | 手工添加或目录推断得到、尚未通过测试的模型（推断型显示为“推断/待验证”） | 否 |
| T-08 | **候选** | 抽卡结果、生成图、草图 | `Candidate`, `PageCandidate` | 单次生图任务产生的单张画面成果 | 否 |
| T-09 | **暂选 / 采用** | 选定、确认、通过 | `SelectCandidate`, `Adopt` | 暂选是人工校对后选中候选但仍待视觉检查；采用/生产就绪仅用于全部门禁通过后的页面状态，两者不得混写 | 否 |
| T-10 | **页面** | 页、Page | `Page`, `MangaPage` | 漫画成品的基本物理页单元 | 否 |
| T-11 | **格子** | 分镜框、Panel、格 | `Panel`, `StoryboardPanel` | 页面内的单个漫画画面分镜分块 | 否 |
| T-12 | **场景资产** | 地点、环境参考、场景 | `SceneAsset` | 表达地点、光照、天气、季节的一级视觉参考资产（V02-20） | 否 |
| T-13 | **角色模型包** | 人物设定集、角色档案 | `CharacterModelPackage` | 整合角色外貌、四视图、表情、服装规则的完整资产包（V02-22） | 否 |
| T-14 | **局部重抽卡** | 局部重绘、局部重做、Inpaint | `Partial Redraw`, `Local Regeneration` | 针对单格、选区或特定元素进行的局部修改抽卡，保留父候选血缘（V02-42） | 否 |
| T-15 | **自动路由** | 智能分发、自动选模 | `Auto Routing` | 系统根据模型能力、健康度与已验证状态自动匹配最适合的文字/质检模型 | 否 |
| T-16 | **工作流** | 流程、DAG、编排 | `Workflow`, `WorkflowGraph` | 面向批量生产的可视化节点执行流水线 | 否 |
| T-17 | **生产门禁** | 就绪检查、发布检查、质检拦截 | `Production Gate`, `Readiness` | 确保页面具备完整剧本溯源、参考图、文字确认和视觉质检的放行规则 | 否 |

---

## 7. 状态文案规范

全站所有异步操作、网络请求与交互反馈统一按照下述 14 种标准状态规范展示：

| 状态类型 | 标准标题 | 一句正文规范 | 推荐主操作 | 推荐次操作 | 交互载体 |
| --- | --- | --- | --- | --- | --- |
| **加载中** | `正在加载…` | `正在读取【{对象}】，请稍候` | *(禁用操作)* | *(无)* | Skeleton / 行内 Spinner |
| **操作成功** | `【{对象}】已保存 / 已更新` | *(单句说明结果，不谈加密/缓存实现)* | *(自动消失)* | *(无)* | Toast / Status 文本 |
| **完全空状态** | `还没有【{对象}】` | `完成【{前置动作}】后即可在此处查看与管理` | `新建 / 导入【{对象}】` | `查看指引` | 空白卡片 + 图标 + 主按钮 |
| **筛选无结果** | `未找到匹配结果` | `没有符合当前搜索或筛选条件的【{对象}】` | `清除筛选` | `重新搜索` | 行内提示 + 清除按钮 |
| **验证中** | `正在测试连接…` | `正在向供应商验证凭据与模型能力` | *(禁用操作)* | `取消` | 行内 Busy / Status |
| **连接不可用** | `供应商连接失败` | `上游返回【{错误码}】：{脱敏错误信息}` | `重新测试` | `检查配置` | 行内 Alert / 表单错误 |
| **权限/认证不足** | `凭据无效或已过期` | `供应商鉴权失败 (401)，请核对 API Key 或服务账号` | `更新凭据` | `查看文档` | 字段行内错误 |
| **配置缺失** | `缺少必要配置` | `请先填写【{字段名}】后再继续` | `前往配置` | *(无)* | 字段下方提示 / 引导条 |
| **限流 / 冷却中** | `请求过于频繁` | `已触发供应商限流，冷却至 {HH:MM:SS}` | `稍后重试` | `切换连接` | 状态芯片 + 倒计时 |
| **付费调用确认** | `可能产生模型调用费用` | `将向【{供应商/连接}】发起一次 {规格} 图片生成` | `确认继续` | `取消` | 对话框模态层 (Modal) |
| **部分成功** | `部分操作完成` | `已成功处理 {M} 项，{N} 项失败：{失败摘要}` | `重试失败项` | `查看详情` | Alert 提示条 |
| **保存冲突 (409)** | `内容已在别处更新` | `当前数据已被其他操作修改，请重新加载` | `重新加载最新` | `放弃本地草稿` | 模态 / 浮动警报条 |
| **后台任务运行中** | `任务正在运行` | `正在执行【{任务类型}】({尝试次数}/{最大次数})` | `查看进度` | `取消任务` | 底部快捷栏 (QueueDock) |
| **未验证边界** | `NOT RUN / 未验证` | `当前离线环境未连接真实供应商或数据库` | `查看验收说明` | *(无)* | 诊断面板 / 验收标记 |

---

## 8. 页面级文案清单

### 8.1 首页 / Dashboard (`apps/web/app/page.tsx`)
- **Hero 区域**：
  - 移除口号式宣传语“从文字开始，把故事画出来…”，保留简洁生产台标识。
  - 统计指标条：`活跃项目`、`漫画页面 (已采用/总数)`、`待复查页面`、`可用模型`。
- **新建项目抽屉**：
  - 工作方式选项精炼：
    - `半自动`：自动完成准备步骤，保留关键节点人工确认（推荐）。
    - `导演模式`：每个生成与编排阶段均等待人工确认。
    - `自动规划`：自动推进文字与分镜规划，画面逐页确认。
  - 删除底部“凭据仅保存在服务端…”长句。
- **连接状态卡片**：
  - 移除 Vertex 专属侧栏卡与硬编码模型信息，改为通用连接健康度概览。

### 8.2 项目设置 (`apps/web/app/projects/[id]/settings/page.tsx`)
- **策略说明**：
  - 移除“控制每一次生成，不是控制你的故事”等口号。
  - 模型策略精简为：“文字与检查任务默认路由；图片模型在生成时逐页选择。”
- **危险区域**：
  - 删除项目：保留输入完整项目名称的确认机制，移除多余的二次 `window.confirm`。

### 8.3 供应商设置 (`apps/web/app/settings/page.tsx` & `provider-management.tsx`)
- *注：该模块与 Issue #40 / Grok V02-11A 保持严格一致，本审计确认以下文案规范*：
  - 搜索占位符：`搜索供应商、协议或模型 ID`
  - 协议分类映射：`OFFICIAL` → `官方`，`GATEWAY` → `网关`，`THIRD_PARTY` → `第三方`
  - 置信度映射：`MANUAL`/`DECLARED` → `待验证`，`INFERRED` → `推断/待验证`，`PARTIAL` → `部分验证`，`VERIFIED` → `已验证`
  - 统一连接验证动作：拆分模式下为【测试连接】与【同步模型】，禁止同一动作内重复调用 discover。

### 8.4 原作导入与修订 (`source-section.tsx`)
- **操作文案**：
  - 主操作：【导入章节原文】、【保存新修订】、【选择 TXT / MD】。
  - 删除算法原理解释，但保留真实硬约束：“每页最多 180 字”。

### 8.5 漫画剧本与分镜编辑器 (`script-section.tsx`, `storyboard-section.tsx`, `storyboard-editor.tsx`)
- **剧本空状态**：
  - 精炼为：“基于章节原文自动生成场景划分、情节拍动作与对白。”
- **分镜编辑与保存**：
  - 消除每次微调都弹出的“将使 N 个候选过期”长 Toast，改为右上角版本号（`V{version}`）实时静默更新。
  - 气泡编辑：【新增文字气泡】、【保存更改】、【锁定文字（生图时禁止改写）】。

### 8.6 参考资产与模型包 (`assets-section.tsx`, `asset-production-panel.tsx`)
- **角色/服装/风格工作区**：
  - 移除“不会只依赖文件名猜测身份”、“这里的模式只用于下面创建的新档案”等自述。
  - 概念图/色板生成：精炼为【生成概念设定草稿】、【提议色板】、【生成 1K 风格测试图】。
  - 确认操作：【确认并设为规范参考】、【激活彩色风格】。

### 8.7 单页生成与生产门禁 (`generate-section.tsx`, `production-readiness.tsx`)
- **生成动作**：
  - 规范术语：【生成 1 个 1K 彩色候选】（禁止出现“抽卡”）。
- **门禁与采纳确认**：
  - 替换 `window.confirm` 为规范对话框模态：`暂选候选：请确认文字对白无误。暂选后仍需通过视觉检查才能导出。`
  - 门禁状态徽章：统一为【生产就绪】、【待质检】、【待采用】、【待修复】。

### 8.8 质量检查与任务中心 (`inspection-panel.tsx`, `jobs-section.tsx`)
- **质检面板**：
  - 英文结果映射：`PASS` → `通过`，`ACCEPTABLE` → `符合预期`，`FAIL` → `存在差异(需修复)`。
  - 修复操作：【修复气泡区域】、【修复单格】、【修复整页】。
- **任务中心**：
  - 状态标签映射：`RUNNING` → `运行中`，`FAILED` → `失败`，`WAITING_APPROVAL` → `等待确认`。
  - 费用说明保留为辅助提示：“费用为根据 token/图片计算的预估值，以供应商实际账单为准”。

### 8.9 工作流编排 (`workflow-studio.tsx`)
- **状态与操作**：
  - 保存状态统一为：`已保存` / `保存中` / `保存失败`。
  - 节点状态中文映射：`等待` / `运行中` / `已完成` / `等待确认` / `失败` / `已跳过` / `已取消`。
  - 移除旧浏览器草稿的长篇大论，提供清晰的【另存为新流程】与【忽略】按钮。

---

## 9. 文案精炼原则（审查与自检规则）

后续所有 UI PR 必须遵守以下十条文案精炼规则：

1. **一事不再提**：同一区域的标题与副标题不得陈述同一事实；按钮文字与卡片头不得重复相同说明。
2. **两句上限**：主界面常规说明文字原则上不得超过两句。超过两句的背景解释必须移入 Tooltip、帮助侧栏或折叠面板。
3. **动词开启动作**：按钮文案必须使用明确的动作动词（如【保存】、【发布】、【新建项目】、【测试连接】），严禁使用完整陈述句做按钮。
4. **失败必给退路**：所有错误信息必须包含“失败对象”和“可执行的恢复动作”（如“请核对 API Key”、“请检查网络连接或切换备用连接”），禁止仅输出“操作失败”或“未知错误”。
5. **成功只讲结果**：成功提示只陈述业务结果（如“角色已保存”），严禁宣讲内部实现（如加密算法、缓存清理、数据库写入、路由计算）。
6. **风险贴近动作**：计费、数据外发、不可逆删除等高风险提示，必须且仅在用户触发动作时的确认层（Modal / Inline Alert）中出现，禁止在页面顶部常驻大段免责声明。
7. **全面中文化与去枚举化**：禁止向用户展示任何英文内部枚举（如 `VISIBLE`、`CURRENT`、`HEALTHY`、`GATEWAY`），必须通过前端常量字典映射为中文。
8. **供应商平权与中立性**：禁止在文案中给予任何特定商业供应商（如 Vertex AI、OpenAI）排他性、宣传式或默认首选地位；所有连接与模型均按能力与用户配置统一呈现。
9. **诚实与不夸大**：禁止给出“绝对安全”、“永久不丢”、“零误差”等无法保证的承诺；对预估费用明确标明“估算”。
10. **禁用无信息宣传词**：严禁在生产工作台中使用“强大”、“先进”、“智能”、“全自动神器”等营销宣传词汇。

---

## 10. 实现切片与派发计划

为避免与其他代理正在进行的任务（特别是 Grok 正在负责的 V02-11A 供应商设置 UI）发生文件冲突，后续文案改造拆分为以下 7 个互不重叠的 Issue 切片：

```text
[V02-50-S1] 全局术语表、客户端错误与通用状态
     │
     ├─> [V02-50-S2] 首页、Dashboard 与项目设置
     ├─> [V02-50-S3] 供应商设置与运行诊断 (等待 V02-11A 合并后派发)
     ├─> [V02-50-S4] 原作、剧本与参考资产组件
     ├─> [V02-50-S5] 分镜编辑器与工作流工作室
     ├─> [V02-50-S6] 页面生成、候选采用与生产门禁
     └─> [V02-50-S7] 素材库、任务中心与帮助中心
```

### 10.1 切片清单

| 切片 ID | 包含文件 | 依赖项 | 风险等级 | 执行代理建议 | 冲突防范说明 |
| --- | --- | --- | --- | --- | --- |
| **V02-50-S1** | `apps/web/lib/api.ts`<br>`apps/web/lib/task-status.ts`<br>`apps/web/components/shell.tsx`<br>`apps/web/components/project-workspace/labels.ts` | 基线 | L1 | Antigravity / Gemini | 仅修改全局共享常数字典与通用导航文本 |
| **V02-50-S2** | `apps/web/app/page.tsx`<br>`apps/web/app/projects/[id]/settings/page.tsx` | S1 | L1 | Antigravity / Gemini | 独立页面文件，不触碰内部组件 |
| **V02-50-S3** | `apps/web/app/settings/page.tsx`<br>`apps/web/components/provider-management.tsx` | V02-11A 合并 | L1 | Grok / GLM | **锁定**：当前 Grok 正在执行 V02-11A，本切片必须等 V02-11A 合并后才可派发修改 |
| **V02-50-S4** | `components/project-workspace/source-section.tsx`<br>`components/project-workspace/script-section.tsx`<br>`components/project-workspace/assets-section.tsx`<br>`components/asset-production-panel.tsx`<br>`components/script-editor.tsx` | S1 | L1 | Antigravity / Gemini | 仅限原作、剧本与资产相关视图 |
| **V02-50-S5** | `components/project-workspace/storyboard-section.tsx`<br>`components/storyboard-editor.tsx`<br>`components/workflow-studio.tsx`<br>`components/workflow-editor/*` | S1 | L1 | Antigravity / Gemini | 仅限分镜与工作流编辑器 |
| **V02-50-S6** | `components/project-workspace/generate-section.tsx`<br>`components/production-readiness.tsx`<br>`components/project-workspace/inspection-panel.tsx`<br>`apps/web/lib/generation-rules.ts` | S1 | L1 | Antigravity / Gemini | 仅限单页生成、质检与门禁相关文件 |
| **V02-50-S7** | `components/project-workspace/library-section.tsx`<br>`components/project-workspace/jobs-section.tsx`<br>`apps/web/app/help/page.tsx` | S1 | L1 | Antigravity / Gemini | 仅限素材库、任务中心与帮助页 |

---

## 11. 测试与验收建议

在后续文案改造 PR 中，测试断言应遵循以下要求：

1. **Testing Library 可见文本与语义角色断言**：
   - 优先使用 `getByRole('button', { name: '新建项目' })` 或 `getByRole('status')`，而非全字匹配脆弱的 `getByText`。
   - 对模态确认框断言其包含关键风险文案（如“可能产生模型调用费用”）。
2. **状态与警告语义关联 (`aria-describedby` / `role="alert"`)**：
   - 字段错误提示必须具有 `role="alert"`，并通过 `aria-describedby` 关联到输入框。
3. **安全脱敏与凭据防泄露断言**：
   - 必须断言错误信息中绝对不回显 API Key 明文或本地服务账号 JSON 文件绝对路径。
4. **动态文案与快照测试禁忌**：
   - 包含时间戳、延迟毫秒数、动态计费金额的文案不得使用全量快照（Snapshot）进行断言，必须使用结构化正则或局部文本匹配。
5. **E2E 测试聚焦关键行动与门禁**：
   - Playwright E2E 仅断言核心主干路径（如“生成 1 个候选”、“确认并设为规范参考”以及门禁拦截提示），不固化页面中所有长说明文字，确保后续文案微调不破坏自动化测试。

---

## 12. 未验证边界与环境说明

依据仓库管理规则，明确记录以下验证边界：

- `NOT RUN`：本任务为只读审计与规范制定，未运行浏览器实机（Playwright E2E、Lighthouse、FPS 门禁）。
- `NOT RUN`：未发起真实外部 AI 供应商网络调用，未产生任何实际 API 费用。
- `NOT RUN`：未读取或接触真实生产凭据、私钥或服务账号文件。
- `NOT RUN`：本审计未检查尚未进入代码实现的 V02-20～V02-54 规划页面；未来功能的术语（如场景资产、角色模型包、自然语言导演）已写入本规范术语表，但明确标记为后续实现的约束标准，不伪称当前代码已存在。
- `L1 隔离声明`：本审计文档独立交付，未修改任何 TS/TSX、CSS、Python、测试或配置文件，亦未修改 `docs/roadmap.md`、`docs/development-progress.md` 或 `plan.md`。

---
