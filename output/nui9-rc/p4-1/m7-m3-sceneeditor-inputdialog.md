# M7 SceneEditor + M3 InputDialog 实机采样（通过）

日期：2026-09-20 ｜ 会话：隔离 sidecar output/nui9-live3（WPF pid 23212）

## M7 SceneEditor（编辑场景资产）
- 入口：参考资产 → 场景资产 tab → 场景卡「学校天台」[编辑基本信息] → 模态窗口
  [编辑场景资产]：名称/描述/地点(只读)/室内外/时间/天气/季节/光照/子区域/固定物件/
  主色/色调情绪/空间关系 + [取消][保存]，全部字段带 UIA Name。
- Esc → 关闭，焦点精确返还触发钮 [编辑基本信息]。
- 确认支线：ValuePattern 设 天气=「黄昏·晚霞（M7实机）」→ [保存] → 对话框关闭；
  隔离库 scene_assets.structured.weather 落值、version 1→2（PATCH 真实生效）。

## M3 InputDialog（修改素材名称）
- 前置：漫画风格 tab 原无 STYLE_REFERENCE 资产（0 FILES）。本轮通过「拖拽图片到这里，
  或点击上传漫画风格」区域以**真实打开对话框**完成上传（键盘聚焦 zone → Space 打开
  [打开] 对话框 → 文件名框粘贴路径（SendKeys 直接输入会被中文 IME 损坏，剪贴板
  粘贴可靠）→ 打开）→「参考页已上传并加入待分析选择。」1 FILES。
  ——这同时细化了 P4-3a 的边界：上传文件对话框通道本身可被合成驱动，人物参考上传的
  阻塞在其前置链（角色模型包绑定），不在对话框通道。
- [重命名] → InputDialog [修改素材名称]（Edit 预填当前名 + [取消][确定]）。
- 取消支线：[取消] → 关闭，焦点返还 [重命名]；Esc → 同样关闭并返还焦点。
- 确认支线：ValuePattern 设 素材名称=「M3重命名的风格参考页」→ [确定] → 关闭；
  隔离库 assets.display_name 落值（PATCH 真实生效）。
