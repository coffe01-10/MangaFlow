# M8 PanelEditDialog 实机采样（修复后通过）

日期：2026-09-20 ｜ 会话：隔离 sidecar output/nui9-live3（api 127.0.0.1:8000，WPF pid 23212，修复版 Release 构建）

## 产品回归根因（本轮修复）
- EditPanel / PanelEditDialog 全仓无调用点：7bfda33b（工作台重构）曾挂检查器「编辑本格」按钮，
  dc901b01 重写 InspectorHeading 时把动作钮写成 null，对话框（含机位高度/拟声词/人物状态/
  409 冲突恢复等检查器内联编辑没有的字段）从此整体不可达。
- 画布提示文案一直承诺「回车打开属性」，但 OnCanvasKey 从未实现 Enter 分支。
- 修复（本轮提交）：①恢复 InspectorHeading 动作钮 Kit.Act("编辑本格", … → EditPanel)；
  ②补 OnCanvasKey case Key.Return（选中格回车打开属性，气泡选中时打开其所属格）。
- 离线回归：native-tests PanelEditDialogEntryChecks（未选中无钮 / 选中恰一钮），
  --storyedit 全链 PASS。对话框本体的 ShowDialog 模态泵与检查器帧泵嵌套时背景优先级
  操作被饿死，离线驱动真实 ShowDialog 不可靠（与 ReplanChecks 走 seam 同理），双支线
  由本实机采样闭环。

## 实机时间线
1. 并排对照主项目 → 分页与分镜 → 点击格 01 → 检查器出现 [编辑本格] 钮（修复点实机可见），
   标题行 P.001 / PANEL 01，几何（只读）X 0.0% · Y 5.0% · 宽 40.0% · 高 30.0%。
2. 选中格回车 → 模态窗口 [编辑本格分镜] 打开：景别/镜头角度/机位高度/动作与表演/背景/
   场景道具/拟声词/人物状态（林晚、陈默）/出血格/无边框 + [取消] [保存本格分镜]。
3. Esc → 关闭，焦点回主窗口；再按 Enter → 对话框再次打开 ⇒ 焦点功能返还画布成立
   （画布快捷键链路拿到回车）。
4. 确认支线：UIA ValuePattern 设 背景 = 「天台·夜（M8保存支线）」→ 点 [保存本格分镜] →
   对话框关闭、焦点回主窗口；隔离库 panels.background 字段落值
   「天台·夜（M8保存支线）」，version 25→26（PATCH 真实生效）。
5. 取消支线：回车再开 → 点 [取消] → 关闭，焦点回主窗口；取消不落库（version 保持 26）。
