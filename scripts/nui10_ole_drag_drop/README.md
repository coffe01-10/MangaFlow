# ole_drag_drop — NUI-10 P4-1 OLE 拖放自动化工具（第三代）

对前台 WPF 客户端（或任何注册了 OLE drop target 的窗口）执行**真实 OLE 拖放**：
`inject` 模式驱动资源管理器自身的 DoDragDrop（真实 CF_HDROP 载荷）完成跨窗口
拖放；`filedrop` 模式在本 helper 进程内直接发起 ole32 `DoDragDrop` + CF_HDROP。
NUI-10 P4-3a「参考图拖放上传」即用本工具复验通过（Explorer → 应用上传区）。

## 构建

```powershell
# 需 net10 SDK（仓库 global.json 钉 10.0.401，用户目录安装见 NUI-10 台账）
%LOCALAPPDATA%\Microsoft\dotnet10\dotnet.exe build scripts\nui10_ole_drag_drop\ole_drag_drop.csproj -c Release
# 产物：scripts\nui10_ole_drag_drop\bin\Release\net10.0-windows\ole_drag_drop.exe
```

独立工具，不进任何 solution / 安装器载荷。

## 用法

```text
ole_drag_drop --mode filedrop|inject --to <x,y> [--from <x,y>] [--files <p1[;p2...]>]
              [--steps N] [--step-ms MS] [--hold-ms MS] [--settle-ms MS]
              [--start-delay-ms MS] [--log <path>]
```

- `inject` — 纯合成拖拽（按下/行走/抬起，无 OLE 载荷）。用于应用内部拖拽
  （分镜面板、工作流节点——这些是 WPF 鼠标捕获拖拽，不是 OLE）。
- `filedrop` — helper 进程真实 `DoDragDrop` 携带 CF_HDROP。注意：合成按下
  落在其它进程窗口时，OLE 拖拽循环拿不到按下事件、不会进入跟踪态（见下
  「失败机理」）——跨进程文件拖放请优先用 `inject` 驱动资源管理器这份
  真实 OLE 源。

坐标一律**物理像素**（与 UIA BoundingRectangle、CopyFromScreen 同一空间）。
工具启动即 `SetProcessDPIAware()`，不要再做 ÷1.25 之类换算。

退出码（filedrop）：0/1/2/4 = DROPEFFECT NONE/COPY/MOVE/LINK；124 = watchdog
强制终止卡死会话；125 = 参数/初始化错误。inject 模式：0 = 注入完成。

## 已验证的配方（2026-09-20/21 实机，nui10-live4）

1. **Explorer → Explorer（对照实验）**：两个文件夹窗口不重叠摆放，注入拖拽
   把文件项从 src 拖到 dst —— 同盘默认 MOVE，src 移出、dst 收到，真实
   DoDragDrop/CF_HDROP 链路全通（`output/nui10-rc1/p4-1/explorer-inject-drag3.log`）。
2. **Explorer → WPF 上传区（P4-3a）**：参考资产页选角色 → 「拖拽图片到这里，
   或点击上传人物参考」按钮（`CharacterAssetPage.cs` upload，AllowDrop=true，
   Drop → UploadAsync）——注入拖拽后 uploads 落盘、assets/character_references
   落库（`wpf-upload-drag2.log` + 台账 P4-3a 行）。
3. **分镜画布面板自验**：inject 拖「格 01」→ 撤销栈激活 → 保存本页后
   panels.bounds 持久化（version 1→2）。

## 实机约束（违反即失败）

- **安静窗**：注入即真实光标移动。前台确认 + 光标 12 采样 ×250ms 零移动后才
  允许注入；真人操作中一律停手（零位移手势检查会被物理鼠标移动干扰）。
- **前台锁定 z 天花板**：后台会话启动的窗口（explorer 等）会被压在前台窗口
  之下，`SetWindowPos(HWND_TOP/TOPMOST)` 抬不上去。两个可用解法：
  点任务栏图标 / 点目标窗口可见的标题栏（等价用户手势，激活即提升）；
  或 `AttachThreadInput` + `SetForegroundWindow` 依次把两个窗口送前台
  （z 序：最后送的前台，先送的次之）。
- **ctypes 传 HWND 常量**：x64 下 `-1`（HWND_TOPMOST）要以 `c_void_p(-1)` 传，
  c_int 会高位带垃圾导致调用失败。
- **ctypes hWnd 插入位**、**PS 5.1 CJK 脚本要 UTF-8 BOM**、多行 `python -c`
  在 cmd 静默不执行——均踩过，见仓库记忆/台账。

## 与前两代的差异（为什么 v3 能跑）

- v1（PowerShell Add-Type）：`DoDragDrop` 9ms 返回 None —— 未注入按下，
  QueryContinueDrag 首轮即判释放。
- v2（载体窗 + WndProc 内 DoDragDrop）：按下依赖抢前台成功与否；无 watchdog
  卡死后只能杀进程；诊断走管道。
- v3（本工具）：STA 主线程 `OleInitialize` + 裸 `DoDragDrop` P/Invoke；独立
  注入线程先按下再行走、容忍「先启动后按下」的 `QueryContinueDrag`；UP 后
  3s 未返回自动 ESC 取消、再不行 exit(124)；日志走文件不走管道；启动即
  `SetProcessDPIAware`。filedrop 模式仍受「按下必须落在自己线程」的 OLE 语义
  限制（见上），跨进程文件拖放用 inject 驱动 Explorer 真实拖拽。
