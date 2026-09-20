# 网页到 WPF 原生工作台迁移清单

盘点日期：2026-09-08；状态复核：2026-09-15；初始基线：`9e6f738`；当前合并基线：
`master` / `c3f8376`。以当前源码和 `docs/adr/native-windows-client.md` 为准。旧
Tauri/Next 壳保留，但不计作 WPF 功能交付。

## 当前结论：尚未达到全功能与 UI 一比一复刻

2026-09-15 状态收口：`output/native-repair-backlog-2026-09-14.md` 中原列为源码风险的
A01–A15，已经由 `ea3f0bf` 完成代码修复并补入受控 WPF 回归；当前 `master` 包含该提交。
这些项目不再作为待修缺陷重复派发。已覆盖的责任包括工作流失败保存门禁、项目/系统设置
保存竞态与 409 恢复、系统设置草稿保护、模型 catalog/alias 路由、用量筛选/CSV/facets/
分区错误，以及保留 HTTP 状态和结构化详情的 ApiException。本次状态收口仅核对提交与
测试源码，没有在 `c3f8376` 上重跑统一门禁。

NUI-6 仍未完成：B01–B16 是尚待逐项确认或实现的网页功能/交互差异；C 类 1–10 是尚待
执行的真实窗口、全链路、DPI/性能和发布验收。受控 HTTP、离屏渲染和编译通过只能证明
其覆盖范围，不能把这些剩余项改写为已验收。

2026-09-19 NUI-8 收口轮已把 C 类中的「安装/升级/卸载」「签名」「严格首帧」「长列表
滚动」从 BLOCKED 推进为有实测（见下文该节），并把发布决策落到「留在 .NET 8 + 另开
升级 PR」；但「逐页交互（弹窗/拖放/键盘可达性）枚举」仍有大半是 NOT RUN，
NUI-6 不能据该轮勾选完成。

2026-09-08 用户补充了原作、人物资产/角色包/参考图、剧本、分镜、生成、素材库与任务页
的真实网页截图。现有“已重写”和 NUI-6C 的受控 HTTP 状态回归，不能当作所有业务操作
及像素布局已与网页一致的证据。侧栏图标、编号位置已在原作页还原批次中修正；人物表单仍使用
固定宽度，其他页面留白及信息层级也需继续逐项对照。角色包、上传、剧本、双画布和生成页的
完整操作与各状态仍须分别验收，不能只凭入口存在或编译通过标记完成。

本次按用户要求移除全局 Pill/Chip 按钮的椭圆轮廓，开关改用小圆角矩形；资产子导航
对齐网页 44 DIP/朱红底线，模型选择对齐 68 DIP/两列矩形卡片。修复 ComboBox 模板
遗漏 ItemTemplateSelector，避免项目选择框输出整个 ProjectItem。新增 NativeButtonChecks
验证矩形状态、分类切换/重复选择、模型互斥选择及 DisplayMemberPath；650/1060 DIP
资产页预览使用模拟数据，仅作为本次样式检查。

## 2026-09-20 NUI-10 RC1 发布工程轮（分支 `goal/nui10-rc1`）

台账：`output/nui10-rc1/matrix.md`（随栈顶 PR #1003 入库）。本轮实测结论：

- **net10 迁移**（`6418b5f1` + 家族修复 `c5d2d581`）：6 文件纯 TFM/路径替换 +
  global.json 钉 10.0.401。net10 行为差异一处成灾：HttpClient 把已取消请求的底层
  故障以原始异常浮出（net8 包成 TaskCanceledException），五视图加载链 12 处补
  CTS 实例捕获 + 双守卫后 `--render` 56 项全绿。Release 0 error（警告 34→26）。
- **复基线**（同机同口径对照）：G1 N=20 net8 P50=1058.7ms → net10 P50=1198.7ms
  （均匀右移 ~130ms，归因运行时冷启）；G4 N=20 20/20 OK（dirty P50 464→480ms、
  save P50 749.5→797ms）；G5 零漂移（20/20 CleanClose+Exited）。达标线均由 lead 定。
- **种子/取名双缺陷**（`2073b99a`）：种子 draft_graph legacy 形状经 GET 透传后
  native 全量 PATCH 必 422（保存链瘫痪）→ canonical v2 + 回归测试；AddNode 读
  `display_name` 而目录暴露 `label` → 新增节点空名必 422 → label 优先。实机节点
  拖拽保存链恢复（draft_version 3→4→6 两轮取证）。
- **安装/升级**（P2-5/P3-2）：1.0.0-rc1 安装器 42.1MB（sha256 落台账）六格 PASS，
  用户数据三阶段逐字节零漂移；0.9.0→1.0.0-rc1 跨版本升级 PASS（0.9.0 为版本元数据
  等价替身，台账注记）。-Launch 探测 NOT RUN（实机被人工占用，避免弹窗干扰）。
- **边界（如实）**：工作流连线拖拽复验、G2 三档/600 档、`--render` 收口终跑被实机
  人工占用阻塞（合成输入停发；非缺陷判据）；真实签名与应用内检查更新维持 AUTH-REQ；
  多 DPI 单屏环境 BLOCKED。payload 运行时捆绑建议 AUTH-REQ 登记（建议捆绑用户目录级
  Desktop Runtime，DOTNET_ROOT 机制已实测）。

## 2026-09-19 NUI-8 发布收口轮（分支 `goal/nui8-release`）

逐格台账：`output/nui8-release/matrix.md`（本轮起随验收工具入仓；离屏 `render-*/`
与隔离 run 目录内的 SQLite/媒体库不入库）。基线 `origin/master` = `601a3e38`
（#980 合并）。统一门禁实测：`npm run check` 全绿（Ruff 通过、Pytest 1663 passed
47 skipped、Vitest 56 文件 615 passed、`next build` 成功）；原生全套
`MangaFlow.Native.Tests.exe --render` exit 0、56 项通过。

**已修复并有回归位置**：分镜旧版分页警告条 / ImageBox 无模板 / `Number()` 对 null
（#980，已在 master）；D1 hero 第二行朱红下划线、D3 侧栏与卡片副标口径拆分、
D6 TXT 导入标题串场、D7 导出失败提示本地化（`1bfa255c`，离屏 + 真机双侧取证）；
D2「N 个项目」计数（`addfb23a`：`StringFormat` 里的 `{}` 是 XAML 转义前缀，从 C#
赋给 `Binding.StringFormat` 时原样进 `string.Format` ⇒ 绑定静默变空串，真机 UIA
里表现为一个空文本节点；回归同时断言文本与 `ActualWidth>20`，并有把 `{}` 改回去
的负向红轮留档）；D8 库滚动容器 `AutomationProperties.Name`（`2e6a4fec`）；
种子导出行 `export_type` 越枚举导致下载 500（`2a9b923a` + `tests/test_nui67_seed_export_download.py`）。

**安装与发布从 BLOCKED 变实测**：新增 NSIS 安装器与静默装/覆盖升级/静默卸载验证
（`4419acfe`，沿用旧 Tauri 壳 W-12 配方：用户数据 sha256 快照 → `/S /D=` 装 →
比对 → 同目录再装 → 比对 → `unins000.exe /S` → 比对）。P4-2 八格全通过，包括
「从安装目录直接启动即可起后端」（隔离用户数据里 shell 日志
`ready_verified → healthy → stopped exit_code=0`）与「卸载后用户数据零改动」。
四轮真实失败轮全部留档（`WaitForExit` 类型误用、`unins000.exe` 自复制跳板造成的
卸载竞态、SQLite 句柄锁、`native-host.exe` 镜像句柄未释放）。附带一条正向结论：
强杀 WPF 客户端后 `native-host.exe` 会经 stdin 关闭自检自行收尾，不留孤儿进程。
签名只到「自签名可签、链不受信」这一步（数学必然，非缺陷），且签名会改变产物
sha256，分发清单须按签名态/未签名态分别记；时间戳与 SmartScreen 记 NOT RUN。
.NET 决策：本轮结论**留在 .NET 8** 并另开独立升级 PR（本机只有 SDK 8.0.425、
两个 WPF 工程零 NuGet 依赖、安装器为框架依赖摆放，换大版本会作废 G1/G2/G4/G5
基线与刚量到的安装器契约）；代价如实记下：8 LTS 支持到 2026-11-12。

**性能仪器化**：G1 严格首帧不再是 BLOCKED —— `App.xaml.cs` 在
`MANGAFLOW_FIRSTFRAME_OUT` 存在时挂 `CompositionTarget.Rendering` 记首个合成回调
相对进程启动的毫秒数（口径是呈现层，不是句柄可见也不是 `WaitForInputIdle`），
冷/热各 N=20 全样本有效（冷 P50 1035 ms、热 P50 1255 ms）。诚实记录两点：
第 1 轮因读帧过早只有 3/20 热样本命中，失败轮保留；「热」比「冷」还慢说明该口径
没隔离出 JIT/镜像缓存效应，首帧被 native-host + python sidecar 整棵子进程树的
provisioning 主导，因此**不给达标结论**，先要求定义安静测量窗口。
G3 滚动压力：修掉采样器自身的 UIA 用错（`$sp.ScrollVerticalPercent = 100` 打的是
客户端对象上不存在的属性，两轮静默失败）后改为 `SetScrollPercent(-1, ±100)`，
20/20 样本 `ScrollDriven=True`，对照跑 WS +2.9 MB / Private +2.9 MB / Gen2 回收 0。
结论是**库页没有 UI 虚拟化**（`LibraryView.cs:214` 直加 `WrapPanel`， realized UIA
节点恒 1423），内存不涨靠服务端游标分页；且本轮实测对象是首屏 150 张卡片，
「600 候选一次滚完」仍是 NOT RUN。

**实机验收口径修正（后续验收必须遵守）**：

1. UIA `BoundingRectangle` 是**物理像素**，MCP 截图坐标是
   `img = (phys / 1.25 − 窗口原点) × (截图宽 / 窗口宽)`（本机 125% 缩放）。
   本仓一度把两者混用，导致一批「拖拽无反应」的观测全部作废。换算已固化为
   `apps/desktop/scripts/uia_rect_to_screenshot.py`，配 `dump_uia_rects.ps1`
   取 rect，不再靠肉眼估点。
2. **任何 UIA Invoke 结果不得充当拖放证据**。旧台账把「G4-工作流」记为 PASS，
   其实际口径是 `measure_workflow_layout.ps1` Invoke「自动布局」。
3. 修正坐标后做了对照实验：注入式 `drag`（rust-sendinput）能真实驱动本应用的
   capture 型拖拽（画布滚动条 thumb UIA 位置 624→794，内容同步滚动）。因此
   「沙箱拦截导致分镜拖拽不可测」这一历史口径**已撤回**；分镜面板拖拽仍零反应
   只剩「面板鼠标命中路径」一种解释，记 **失败待判**，需按台账第 2 项定性。

**本轮新立、尚未修的缺陷**：NUI-8-A `MessageBox.Show` YesNo 确认框 Esc 不关闭且
默认焦点在 `是(Y)`（付费/不可逆分支），而自研 `ConfirmDialog` 两条都做对
（`Ui.cs:500/502`）——建议把付费与删除类确认迁到 `ConfirmDialog`；仪表盘项目卡在
UIA 里 `Name` 是 `ProjectItem { … }` 记录转储且 `invoke=False select=False`，
屏幕阅读器读出内部文本、UIA 无法点开；D5 全局设置双头部（改动方案已记，属
壳↔视图动作所有权重构）；缺陷 #4 仍**未复现**——受控复现器在真实 sidecar 上打了
400 个请求（串行 burst、跨 keep-alive 空闲窗口的不可重放 PATCH、320 并发 GET ‖
8 保存）零失败，**否证了「池化连接被服务端回收」假设**，因此不加
`PooledConnectionLifetime` 一类无对症依据的改动；该轮同时交付
`ApiClient.ReportTransportFailure`（把方法/origin/异常链/socket 码落到
`wpf-client.log`），因为上一轮该缺陷零日志正是它无法定因的直接原因。

P2 逐页交互枚举**未完成**：模态清单已全量入台账（9 个窗口型 + 内嵌抽屉 + 44 处
`MessageBox.Show` 确认点 + 11 处系统文件对话框），实机只跑到抽屉（含 backdrop
支线）、M1 `ProjectPalette`、M4 `Lightbox`、MB1 重试确认的取消支线；M2/M3/M5~M9
七格与其余 43 处取消支线、参考图拖放上传、工作流节点拖拽连线、逐页 Tab 序横扫
均为 NOT RUN，原因与复入口写在台账 P2-1/P2-2 节内，不在此勾完成。

## 2026-09-19 NUI-6/7 实机并排验收第一轮（分支 `goal/nui67-acceptance`）

历史并排对照的 ERR_CONNECTION_REFUSED 阻塞已解除：新增
`scripts/nui67_side_by_side.py`（隔离数据目录启动 web+API+原生三件套、健康检查、
命名 Job/验证式树杀的干净关闭、会话台账）与 `scripts/nui67_seed.py`
（NUI67-DS1 固定数据集：2 项目/3 章/3 页/2 角色/2 服装/场景/风格/3 批次候选/完成+
失败任务/调用尝试/账单/工作流草稿/27 供应商预设，双侧数据库+存储一致）。web 与原生
侧同种子并排截图对齐 1500×975 DIP。台账：`output/nui67-acceptance/matrix.md`（不跟踪）。

逐页实机结果（截图证据见台账索引）：17 页主流程与像素对照全部走通——首页指标带
与卡片、原作（章节选择/修订保存落库 rev2/分页门禁随章节切换）、资产五子页
（人物编辑保存/服装绑定/场景/风格/参考分类语义双侧一致）、剧本（覆盖率 100%/场景
变体绑定/空态）、分镜（页列/画布/导演台/门禁）、生成（候选/批次/生产门禁）、素材库、
任务中心（失败分组/错误码/费用口径）、流程编排（草稿 V1/节点库/检查器/运行门禁）、
项目与全局设置、用量（3 次调用/¥66 账单/未知与零值）、帮助。真实供应商生成/付费
冒烟仍为待授权，未跑。

实机抓出并修复三个离线回归未覆盖的真实缺陷（均有回归测试）：

1. **分镜页缺 web 的「旧版分页数据」警告条**（a8666cde）：web
   `getPageStructureIssue` 在页缺 scene/beat 来源或未覆盖原文时按数量警告并引导回
   剧本页，原生缺失。已补 `structureBar` + 前往漫画剧本导航；回归在
   `NativeStoryboardPageChecks`（legacy fixture 断言横幅出现/计数/随重载消失）。
2. **ImageBox 无控件模板 → 真机上全 app 图像空白**（83440f87）：`ImageBox` 曾
   `DefaultStyleKeyProperty.OverrideMetadata` 指向自身却无 Themes/Generic.xaml 样式，
   Content 永不渲染——离线检查只断言 URL 不断言像素，从未暴露；素材库/生成/资产
   缩略图全部空白。移除 override 后 ContentControl 默认模板正常呈现；回归在
   `NativeVisualChecks`（断言视觉子树非空）。
3. **`JsonFields.Number()` 对显式 null 数值字段抛异常**（c961dd1a）：真实后端把
   从未验证连接的 `latency_ms` 序列化为 `null`；`TryGetInt32` 对错误类型抛
   InvalidOperationException 而不是返回 false，导致设置页「供应商列表读取失败」。
   `Number()` 增加 Number 类型守卫；fixture 增加 `latency_ms: null` 回归。

NUI-7 部分推进：启动计时/退出清理复跑 3 样本全过（句柄 519/483/508ms、空闲
577/558/540ms、干净关闭、递归子进程扫描零残留，优于历史热样本，无回归）；
125% DPI 单屏实测文字 ClearType 锐利无发糊；960/1126/1267 DIP 三档宽度渲染干净
（940 DIP 低于 `MinWidth=960` 按设计不可达）；.NET 8 LTS 核对通过（注意支持期至
2026-11-12，升级 .NET 10 留 lead 决策）。仍为 NOT RUN：严格首帧（呈现层）、页面
切换响应 N=20、长列表内存、双画布帧时间、多 DPI/跨屏（单屏环境 BLOCKED）；
安装/升级/卸载 BLOCKED（仓库无 WPF 安装器产物）；签名 BLOCKED（无证书）；
409/ConfirmLeave/逐页弹窗焦点的实机专项轮与文件对话框实机未跑（契约由离线回归
覆盖，不据此勾选实机格）。本节结论由实机截图+数据库+API 证据支撑，台账逐格可溯。

## 2026-09-09 逐页还原：服装档案与卡片文字

服装入口已切换到原生 OutfitWorkspace，依据网页 assets-section.tsx、use-assets-workspace.ts
与 globals.css 还原：网页标题、显式双列模型选择、左侧三步绑定表单/待绑定摘要、右侧全部
角色的已保存档案，以及下方上传区、生成素材选择和双列参考图卡。窄窗口改为上下排列。
支持新建、带 version 的 PATCH 编辑、取消编辑、参考图加入/移除、上传/拖放、素材重命名、
重分类及删除、档案删除确认、生成穿着图、实际提示词和实时结果。locked_fields 按数组传输。
生成素材库使用 groups / next_cursor 对象契约并去重；导入仅加入草稿，保存才绑定。
生成结果涵盖上传/检查阶段轮询，终态停止，返回页面读取最近批次；异步结果校验页面归属，
离开后不会继续派发第二步生成请求。服务端 409 保留编辑内容，写入期间禁止重复操作。

字体追加修复：移除 Card / CardInk 上包住文字的 DropShadowEffect，将文本/密码/下拉输入
背景改为实色，并在资产页滚动内容内部提供实色背景和 ClearTypeHint。保留按钮模板中
独立的装饰阴影，不栅格化整块卡片文字。
依据：[Microsoft WPF ClearTypeHint 文档](https://learn.microsoft.com/en-us/dotnet/api/system.windows.media.renderoptions.cleartypehint)
说明 Effect、Clip、Opacity 等中间渲染层及透明背景可能导致 ClearType 停用。

验证：Release 编译 0 错误；54 项基础检查和完整原生 UI 回归通过。新增 NativeOutfitChecks
使用受控 HTTP 验证真实 WPF 控件的 650/1100 DIP 布局、实色文字表面、锁定项数组、版本
编辑/冲突保留、保存去重、素材库游标/导入、PNG multipart 上传与草稿选中、显式模型生成、
上传/检查阶段轮询、终态停止、返回结果恢复，以及离页/换角色的迟到响应隔离。
测试配置和上传临时文件在 finally 中清理。预览使用模拟数据：
output/native-parity-review/native-outfits-restored-shell.png、native-outfits-restored-650.png、
native-outfit-references-restored.png、native-outfits-text-125pct.png。

边界：本次内置浏览器访问网页返回 ERR_CONNECTION_REFUSED；网页实时并排验收为 NOT RUN。
文件选择器、真实拖放手势、删除/用途修改确认弹窗、真实供应商调用及物理屏幕 DPI/动画
性能未实机验收。离屏截图不能证明用户看到的模糊已完全消除，也不代表全客户端一比一完成。

## 2026-09-09 逐页还原：人物设定与字体清晰度

按用户要求继续第二页（参考资产 → 人物设定）。以用户截图和网页 assets-section /
character-package-workspace 源码为依据；本次内置浏览器访问 127.0.0.1:3000 返回
ERR_CONNECTION_REFUSED，未启动或修改用户服务，因此真实网页并排运行验收为 NOT RUN。

已调整全宽双输入表单、矩形角色条、两列模型卡片；进入页面不再自动选择第一个角色和
模型。新增独立模型包列表/创建区域及人物参考拖放、上传、重命名、绑定/解绑、用途修改
和删除区域。包详情新增服务端完整度、封面横向操作、四视图、表情槽位、服装集及历史
对比；修正规格工作集读取、派生/激活/封面/解绑版本参数接口。角色特征与禁改项按 API
数组传输，参考数量从 references 读取、素材大小从 byte_size 读取。

文字：保留标题衬线风格，正文使用 Microsoft YaHei UI；页面显式继承 Display / ClearType、
布局与像素对齐，在有实色背景的页面启用 ClearTypeHint，悬停结束位移对齐物理像素。
exe 增加并验证嵌入 PerMonitorV2 manifest；必须重启生效。已输出 96/120 DPI 离屏图，
实际显示器跨屏切换、用户所见发糊是否完全消除、动画帧率仍为 NOT RUN。
参考：[Microsoft DPI manifest](https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process)、
[WPF ClearTypeHint](https://learn.microsoft.com/en-us/dotnet/api/system.windows.media.renderoptions.cleartypehint)。

验证使用模拟 HTTP 驱动真实 WPF 控件：650/1100 DIP 排版、显式选择、角色数组契约、
新增去重、包工作集/完整度、封面 PUT、视角版本参数、规格保存、人物解绑、迟到响应隔离。
Release 编译通过，54 项基础检查与完整原生 UI 回归通过；全量重编译仍有 37 条既有警告。
预览：output/native-parity-review/native-characters-restored-shell.png、
native-character-package-restored.png、native-character-references-restored.png。
发布/归档确认弹窗、真实文件选择器/拖放、供应商生成与跨屏视觉验收未实际操作，不能以
离屏回归宣称本页所有操作或其他资产子页已经一比一验收。

## 2026-09-08 逐页还原：原作与修订

按用户要求先收尾此页。已只读核对内置浏览器中的真实 `/source` 页面：标题与底线、
48 DIP 标题输入、80 DIP 原文框、右对齐导入按钮、章节登记表、绿色覆盖率、40 DIP
编辑/删除图标按钮和底部流程按钮。配套侧栏补线性图标、右侧编号与黑底朱红选中标记，
移除网页没有的常驻项目下拉框（Ctrl+K 切换入口保留）。

修复章节选中后仍处理第一章的问题；分页门禁读取该章的实际 script READY 状态，
分页/解析防止重复提交，并传递章节与页码给后续页面。TXT/MD 改为与网页一致的
multipart 直接导入；修订保存后刷新章节。导入和读取的迟到结果不覆盖其他项目。

验证：Release 编译 0 错误（37 条既有警告）；54 项基础检查及完整原生 UI 回归通过。
新增 NativeSourceChecks 覆盖选中第二章的分页/解析、重复点击、粘贴/文件导入、
修订保存、跨项目迟到读取，以及 650/1062 DIP 布局和空提示不占位。
预览在 `output/native-parity-review/native-source-restored-shell.png`（受控双章节数据）。
本批次未执行真实供应商生成，也未测量动画呈现帧率；不代表其他页面已一比一验收。

## 2026-09-08 NUI-7 首批实测：真实启动计时与退出清理

用新增 `apps/desktop/scripts/measure-native-startup.ps1` 在本机（Windows 11，
Release 构建，仓库后端）启动真实客户端窗口采集：**指标定义为「进程启动 → 主窗口
句柄创建」与「→ WaitForInputIdle」**，不是严格的首帧绘制（未做 ETW/呈现层采样，
不得换算成首帧数据）。3 个样本、每次全新用户数据目录（含完整后端启停）：

| 样本 | 窗口句柄 | 输入空闲 | 干净关闭 | 进程退出 | 子进程残留(5s 后) |
| --- | --- | --- | --- | --- | --- |
| 1（冷，含磁盘冷缓存） | 2109 ms | 2310 ms | 是 | 是 | 无 |
| 2 | 939 ms | 985 ms | 是 | 是 | 无 |
| 3 | 931 ms | 979 ms | 是 | 是 | 无 |

退出清理观察：三次 `CloseMainWindow` 均走完确认-停服-关窗流程，native-host 及其
python 子进程树在 5 秒观察窗内全部回收，临时数据目录可删除，无残留进程。这是
**退出清理的 3 次实测样本**，不构成安装/升级/崩溃清树验收。

NUI-7 其余项保持 **NOT RUN**：严格首帧（呈现层）、窗口内页面切换响应、固定数据集
长列表内存、双画布拖拽帧时间（需全样本含失败）、100/125/150/200% DPI 实机截图、
安装/升级/签名。本节样本量 3、单机、指标口径为句柄/空闲，不能外推为分布或预算结论。

## 2026-09-08 17 页状态验收矩阵 NUI-6C

本次基线为 NUI-6B 提交后工作树。新增 `NativeStateMatrixChecks`：以受控 HTTP fake
（空数据/全 500/停车三模式，停车可按取消令牌真实取消）驱动 13 个视图 Id + 5 个资产
子页（对应 web 17 个业务页面）逐一过**空**（渲染非空、无永久「正在读取」、干净页面
不阻拦返回导航）、**错误**（服务端 detail 可见）、**恢复**（重试按钮 + 刷新 + 重进
页面后错误清除）、**项目切换**（迟到 500 与真实取消不污染新页面、旧项目身份不泄
漏）；每个页面输出空态 PNG 到 `output/native-parity-review/native-matrix-*.png` 作
为证据（13 张 + 资产子页内联断言）。

矩阵抓到并已修复三组真实缺陷（另有专项审查复核）：
1. **Switch 模板 Color/Brush 混用**（Theme.xaml）：Knob 的 Background 引用
   `WhiteColor`（Color 资源），项目设置页有数据实例化模板即抛 XamlParseException；
   改为 `Surface`（同色 Brush）。
2. **取消异常穿透 async void**：7 个视图的 Activate 与约 40 处用户动作 handler 的
   `catch (Exception e) when (e is not OperationCanceledException)` 不捕获取消，而
   async void 无调用者——真实 HTTP 在视图停用/动作飞行中取消时会崩 dispatcher；
   既有测试的 fake handler 从不观察令牌，从未暴露。统一在过滤器前补
   `catch (OperationCanceledException) { }`。
3. **超时与取消不可区分**：HttpClient 30 秒超时表现为「令牌未取消的取消异常」，
   原会被上述吞咽静默吃掉（页面永久停留加载）。`ApiClient` 现将令牌未取消的
   OperationCanceledException 翻译为 TimeoutException（错误 UI 显示、保留取消的
   OCE 语义供视图静默吞咽）。审查确认 LocalEditWindow 关窗 discard 超时因此变为
   可见提示而非无响应。

矩阵本身的审查改进：错误断言收紧为匹配唯一 detail 串；交互看门狗 20s→60s；fake
补对象形状（workflows POST、项目详情按请求 Id 返回）；source 页的切换场景走真实
取消路径（令牌注册取消停车 TCS），确保吞咽修复被实际执行。

**仍未验收（NOT RUN）**：409 版本冲突格子、`ConfirmLeaveAsync=false` 阻断导航的负
向格、弹窗确认流与焦点恢复的逐页格（弹窗/Esc/焦点恢复已由 NUI-6B 的全局回归覆
盖控件原语，但未逐页登记）、全部实机（真实窗口、真实数据）格子。矩阵覆盖的是离
线契约可证明的部分，不得据此宣称 17 页实机验收完成。

## 2026-09-08 全局任务底栏、帮助全文与快捷键/焦点 NUI-6B

本次基线 `de5f5d2`。任务底栏按网页 `.queue-dock` 重做为深色横条（48 DIP 高、墨色
`#151512` 底、`#716E67` 状态点、等待时琥珀点加辉光），新增 `DockQueue` 服务：读取
active-jobs 列表，统计总数/运行中/等待/失败/完成，展示最新任务「名称 · 状态」或按
分区的空闲文案（两者互斥，修复了旧底栏同时显示的问题）；对最新任务提供取消/重试
（重试先弹费用确认，确认后重校验项目、任务 Id 与可重试状态）。写操作按任务 Id 去重、
禁用按钮、失败原样展示服务端详情、成功提示随新数据落地清除、绝不自动重试。

隔离与生命周期：读取/写入响应按发起前捕获的项目 Id 校验，项目切换即 `Reset()` 清空
底栏并立即刷新；读去重按项目维度记录，冲突请求折叠为一次补读（补读只为当前项目发起）；
重连时旧 `DockQueue` 被 `Abandon()`（停止触碰共享 pending 标志/提示），新建实例立即
重读。轮询在无项目、后端进程不在、窗口最小化时不发请求；收起/展开立即持久化到
`window.json`。底栏常显五段计数是按验收要求对网页（仅 jobs/generate 显示
「并发上限|等待|失败」）的增强；网页的并发上限来自项目配置，原生未取该字段，
为避免显示假数据而不展示（后续接入项目设置读取后可补）。

帮助页对照 `app/help/page.tsx` 补齐：网页原文逐字落地（含 hero 段落）、四列流程卡、
故障排查区、「打开系统设置」跳转；新增锚点导航（六个分区跳转并聚焦区内首个可聚焦
元素）与「← 返回项目」入口；断点对齐网页 CSS（900px 两列/堆叠排查区、760px 单列、
静态 42px 问号图标插入 kicker 与标题之间、卡片 165px、页边距 16/28）；锚点跳转按
文档坐标计算，从已滚动位置再跳不会二次累计偏移。

键盘与焦点：`App` 静态注册 ButtonBase 的 PreviewKeyDown 类处理器，Enter 与 Space
一样激活聚焦按钮/筛选控件（走真实 OnClick 路径，含 `e.IsRepeat` 防长按连发与
`OriginalSource` 防冒泡误触）；默认 ToggleButton/CheckBox 模板补焦点环（仅换朱红
边框色，不引起布局跳动）；Esc 关闭抽屉/确认弹窗/输入弹窗/任务详情/局部编辑/大图
预览，弹窗关闭后焦点回到触发按钮，抽屉关闭后回到开启按钮且隐藏面板不再接受焦点
（DrawerOverlay 内 Tab 循环）；动画尊重系统减少动画设置。

验证：`dotnet build .../MangaFlow.Native.Tests.csproj -c Release --no-restore` 0 错误；
`MangaFlow.Native.Tests.exe --render output/native-parity-review` 全部通过（54 项基础
断言 + WPF 导航/重渲染/多尺寸布局/交互 + 新增 `NativeDockChecks`：计数与标题互斥、
跨项目迟到读取/写回 notice 隔离、Abandon 契约（in-flight 不清共享 pending 且拒绝
新写）、取消/重试去重与 409 详情透传、无后端/无项目不轮询、收起/恢复持久化、
Enter/Esc/长按/焦点恢复/弹窗单次确认、帮助锚点（含从已滚动位置回跳）、900/760
窄窗断点、返回入口）。`npm run check` 全部通过。内置浏览器对照了网页帮助页 DOM
结构与 760px 计算样式（单列 + 静态图标位置）、真实项目工作区底栏的样式/文案与
收起-恢复-localStorage 持久化行为。截图对照受 Next.js 开发覆盖层污染，未做像素级
比对。三轮子代理只读审查（共 1×P1、3×P2、其余 P3）已全部修复；剩余已知 P3：
底栏与任务页对同一任务的写操作各自去重（跨面板并发的第二发由服务端 409 兜底）。

**仍未验收（NOT RUN）**：17 页全部业务与网页逐项实机对照（NUI-6C）、真实供应商
生成/质检/导出闭环、启动首帧/页面切换/长列表内存/双画布帧时间/多 DPI 实测、
安装/升级/签名、退出清理实测。不能依据本轮编译或离屏截图宣称像素级一致或商业级
帧率达标。

## 2026-09-08 布局与业务接线修复

本轮直接对照网页源码和内置浏览器中的首页/创建抽屉，修复原生首页启动空白、主副栏覆盖、
卡片超出网格、抽屉未铺满、黑色按钮文字不可读、顶部操作缺失、分镜标题与工具栏挤压等问题。
新建抽屉使用与网页相同的标题区高度、模式卡和等宽矩形清晰度选择；首页与全局设置使用
各自的响应式断点，项目侧栏折叠后保留 52 DIP 图标轨。按钮不再给文本整体加阴影；
页面进场采用网页缓动曲线，切页时释放旧动画与待执行布局事件，尊重系统减少动画设置。
帮助页恢复网页的大标题、四列流程卡和故障排查区；桌面快捷键保留在折叠补充区。

生成页新增真实人物/服装参考选择及角色包版本覆盖，随生成请求提交；补齐模型选择控件挂载、
历史批次读取、候选操作去重、PNG 下载、章节/页面迟到响应隔离。`LocalEditWindow` 新增矩形/
画笔/整区擦除、50 步撤销重做、缩放、模型与清晰度选择、预览/确认/撤回、派生结果查看及暂选。
用量页修正 `HTTP_API` 通道值，接入自定义日期，并固定分页时间和全部筛选条件，防止旧请求串入新结果。

验证：`npm run check` 通过（ESLint/Ruff、Pytest 1502 通过/37 跳过、Vitest 434 通过、Next.js 生产构建）。
原生 Release 编译 0 错误；54 项基础断言与 WPF 导航、重渲染、多尺寸布局、控件交互检查通过。
控件交互使用模拟 HTTP，覆盖创建模式/清晰度、生成参考参数与重复点击、局部编辑源图变化拦截及预览/确认/撤回。
离屏样例输出在 `output/native-parity-review`，不是用户真实项目截图。网页对照未启动 API，
只验证首页/抽屉布局和选项状态，没有进行浏览器业务 E2E。

**仍未验收**：17 页全部业务与网页逐项实机对照、真实供应商生成/质检/导出闭环、DPI 组合、
长列表/双画布帧时间与内存、安装/升级/签名。不能依据本轮编译或离屏截图宣称全功能完成、像素完全一致或商业级帧率达标。

## 2026-09-08 自动化续作：任务中心 NUI-6A

本次基线 `0bf657d`，以当前代码为准推进，不重复上次自动化记忆中的 NUI-2A。
任务页现按 API `JobRead` 读取标量费用、币种、完整度、价格版本、毫秒耗时和嵌套结果图，
保留服务端错误详情；近期/历史切换真正重新查询，已结束任务按本地日期折叠。
新增 `JobDetailsWindow`：按项目和任务筛选 `/usage/attempts`，每次最多 50 条，
游标分页、失败重试、关闭取消、重试/换路信息和未知/零值分开显示。

列表读取捕获请求代次与取消令牌；单项/批量操作使用激活代次隔离跨项目迟到结果，
重复操作去重并禁用对应控件。页面激活期间持续发现新任务，数据未变化时保留控件和焦点，
数据变化重绘时保留日期展开状态。后台页不轮询；已归档任务不提供取消/付费重试。

验证：原生 Release 构建 0 错误（全编译 36 条既有警告）；54 项基础断言及完整 WPF
导航/重渲染/交互检查通过。新增 `NativeJobsChecks` 使用真实响应字段形状和受控 HTTP，
覆盖历史查询、迟到读取失败、跨项目写回隔离、单项/批量去重和失败恢复、空列表重复轮询、
未变化响应保留控件、日期展开保持、账本作用域/分页/重试/关闭取消、未知与零值。
首轮扩充截图测试的反射调用与 WPF 继承方法重名，已限定声明类型并重新通过。
940/1320 DIP 任务页与调用详情预览是样例数据离屏渲染，未连接真实供应商。
本次 `npm run check` 全部通过：neutrality/ESLint/Ruff、Pytest 1508 通过/37 跳过
（761 条既有弃用警告）、Vitest 435 通过/42 文件、TypeScript 与 Next.js 生产构建。

下一项 **NUI-7：性能与发布验收**（NUI-6B/6C 已于上方 2026-09-08 章节完成）。
NUI-6 整体仍未完成，真实账本实机、供应商、
浏览器 E2E、DPI/性能、安装发布继续为 NOT RUN。

## 2026-09-08 素材库接线与布局续作

直接核对 `library-section.tsx`、`use-library-workspace.ts` 及现有 API，修复刷新误用下一页
游标、翻页失败却推进历史、旧筛选响应覆盖新项目的问题。新增日期筛选、完整模型目录筛选、
收藏操作与软删除；候选是否已暂选读取服务端 `is_selected`，撤回携带用户确认的候选 ID。
生产检查失败及章节切换都会关闭旧导出门禁；阻塞页面跳转同时保存章节和页面身份。

PNG ZIP / PDF / JSON 使用原生保存对话框与流式下载，读取真实 `byte_size`，不再调用图片
预览充当下载。下载失败或取消保留已有目标文件并清理本次临时文件。批次采用候选数量跨列、
3:4 图片比例、完整边框和深色导出栏；日期控件使用统一纸面样式并保留原生输入和日历功能。
相同响应及失败的收藏操作保留卡片实例，减少图片重载和焦点丢失。

验证：`NativeLibraryChecks` 覆盖日期输入/选择/清空、筛选与分页失败、迟到响应、重复点击、
撤回候选身份、软删除刷新、导出失败关闭门禁、跨章节定位、下载中断/取消后的文件保护，
以及 360/650/1000 DIP 混合批次布局、700/1060 DIP 正文布局及图片内部铺满断言。
54 项基础断言和完整 WPF 导航/重渲染/交互检查通过；全编译 0 错误，仍有 36 条既有警告。
本次完整 `npm run check` 通过：Pytest 1508 通过/37 跳过、Vitest 435 通过/42 文件，
neutrality/ESLint/Ruff、TypeScript 与 Next.js 生产构建通过。
`output/native-parity-review/native-library-populated-{700,1060}.png` 是模拟 HTTP 的离屏样例，
不代表真实媒体或供应商验收。系统保存对话框实机、全页面像素对照和帧时间测量仍为 NOT RUN。

## 页面与功能覆盖

Web 当前有 11 个 `page.tsx` 路由文件，展开合法动态段后为 17 个业务页面与
3 个重定向入口。导演、质检、角色包、候选预览等是页面内工作区或对话框，不能因没有
独立 URL 而遗漏。下表的“已重写”表示 2026-09-07 全量重写后的原生实现（13 个视图 + 设计系统 +
服务层），并经过审查轮 1–3 与构建/回归/离屏渲染/真实后端烟测；“逐项对照/实机
验收”类的余项集中在 NUI-6/7。

| 网页路径（项目路径前缀为 `/projects/:id`） | 完整功能范围 / 源码入口 | WPF 当前状态 | 阶段 |
| --- | --- | --- | --- |
| `/` | 项目创建、卡片、指标、封面、生产进度、AI 连接统计；`app/page.tsx` | 已重写（HomeView）；封面/创建参数已对照，实机逐项验收待 NUI-6 | NUI-2 |
| `/help` | 全部使用说明与导航；`app/help/page.tsx` | 已重写（HelpView）；2026-09-08 全文/断点/锚点/返回入口对照完成（NUI-6B） | NUI-6 |
| `/settings` | 供应商增删改/启停、连接与密钥、验证/发现/余额、模型展示/价格/冒烟、CLI 配置、运行参数、诊断、存储；`provider-settings/`、`app/settings/page.tsx` | 已重写（SettingsView）：供应商卡/连接面板/密钥/运行参数/分层诊断/存储卡；真实供应商验证与付费冒烟另行验收 | NUI-2 |
| `/settings/usage` | 时间/项目/供应商/模型筛选、KPI、趋势、分组、调用明细抽屉、游标翻页、估算完整度；`components/usage/` | 已重写（UsageView） | NUI-6 |
| `/source` | 文本/TXT/Markdown 导入、章节、分段覆盖、原文阅读/修订、删除/撤回；`source-section.tsx` | 已重写（SourceView）：导入/修订/删除/多章撤回/解析/分页入口 | NUI-2 |
| `/assets/characters` | 人物档案、参考生成/采用、角色模型包各槽、版本/发布/差异、参考选择 | 已重写（AssetsView characters 子页 + CharacterPackagePane） | NUI-3 |
| `/assets/outfits` | 服装档案、人物绑定、参考生成/采用 | 已重写（AssetsView outfits 子页） | NUI-3 |
| `/assets/scenes` | 场景档案、版本、参考、生成/采用、删除/恢复、场景绑定 | 已重写（AssetsView scenes 子页） | NUI-3 |
| `/assets/style` | 风格配置、规则、分析、测试候选、历史版本 | 已重写（AssetsView style 子页 + 4 段流水线；ANALYZING 轮询不再丢输入） | NUI-3 |
| `/assets/references` | 本地图片上传、筛选、缩略图、预览、引用管理；以上五页见 `assets-section.tsx` 等 | 已重写（AssetsView references 子页） | NUI-3 |
| `/script` | 章节与场景选择、情节拍、对白、结构编辑、解析/确认；`script-section.tsx` | 已重写（ScriptView） | NUI-3 |
| `/storyboard` | 联系表、分页、可视画布、格子几何/对白气泡、检查器、布局重建、版本保存/确认；`storyboard-editor/` | 已重写（StoryboardView）：归一化坐标画布、手势/吸附/撤销、原子保存 | NUI-4 |
| `/generate` | 页选择、生产门禁、模型/参考/提示词、候选生成/比较/收藏/采用、质检、修复/升清、导出；`generate-section.tsx` | 已重写（GenerateView） | NUI-5 |
| `/generate` 内嵌导演与局部编辑 | 提议/歧义澄清、命令 diff、确认/拒绝/撤销/重做、选区、局部候选/血缘；`director-workspace.tsx`、`local-edit-workspace.tsx` | 导演台已重写；2026-09-08 接入 LocalEditWindow 原生选区编辑，预览/确认/撤回契约回归通过，真实供应商与全交互对照仍待验收 | NUI-5/6 |
| `/library` | 类型/批次、日期与模型筛选、游标分页、收藏/撤回/软删除、原图预览、整章导出与文件保存；`library-section.tsx` | LibraryView + LibraryFeed：2026-09-08 接线与布局回归通过；实机逐项对照待 NUI-6C | NUI-5/6 |
| `/jobs` | 筛选、进度、错误详情、取消/重试、调用/成本信息；`jobs-section.tsx` | JobsView + JobDetailsWindow；真实字段、历史查询、日期分组、账本分页与状态隔离已回归；实机逐项对照待 NUI-6C | NUI-2/6 |
| `/workflow`（项目内） | DAG 工具栏、画布、拖拽/连线、检查器、草稿/发布、运行、节点审批、监视器；`workflow-editor/` | 已重写（WorkflowView）：DAG 画布/缩放/撤销/导入导出/运行监视/序列化自动保存 | NUI-4 |
| `/settings`（项目内） | 工作方式、分辨率/并发、检查开关、文字模型路由、版本冲突、确认名称后删除 | 已重写（ProjectSettingsView）：五分区 + 危险区 | NUI-2 |

重定向：`/projects/:id` → `source`；`/projects/:id/assets` → `assets/characters`；
全局 `/workflow` → 最近项目流程编排（无项目/失败回首页）。原生入口按明确项目身份导航，
后续深链接接入时再迁移这些兼容规则。

## 原生框架与实现边界

- `ProjectPages` 集中保存九个项目一级页面的顺序、名称、描述与实现状态；
  与 Web `navigationItems` 及项目工作流/设置入口一致。
- `ProjectNavigation` 负责当前页与去重通知。顶部项目面包屑、侧栏选中状态共享同一份
  状态；项目切换继续保留当前页面类别，切换前经过离开确认并取消旧读取。
- 每个页面是独立 `WorkspaceView`（UserControl），由 MainWindow 视图缓存按需创建、
  常驻复用；`Activate/Deactivate/ConfirmLeaveAsync/PollTick` 构成生命周期契约。
  原先的 `WorkspacePageFrame` 共享标题控件与 `EditorDialog` 已在 2026-09-07 清理
  （`ProjectPalette` 拆分保留），各视图改用 CanvasHeader 卡片与各自对话框。
- 无模拟数据、假保存/生成按钮或无限加载状态；断网可导航，未激活页面零请求。

## 分阶段交付与验收

当前状态（2026-09-07 第二轮更新）：NUI-0~NUI-5 的实现已全部落地——13 个视图从零
重写（含导演台规则桩、双画布、资产五子页、角色模型包），经审查轮 1–3 修复
B-1~B-8 全部 P1 与 P2/P3 清单后无 P0/P1。剩余工作集中在 **NUI-6 全局完整性**
（17 页与内嵌工作区逐项实机对照、任务成本明细、局部编辑入口、帮助全文）与
**NUI-7 流畅性与发布**（DPI/性能采样、安装/签名/退出清树发布验收）。

2026-09-07 用户明确授权后，计划、完成状态、验证结果及下一优先级已同步到
[roadmap](roadmap.md) 顶部的 WPF 原生客户端队列；此前文档写入审批阻塞已解除。

1. **NUI-1：导航和页面容器。** 九个入口、即时选中态、项目上下文、可折叠侧栏、
   共享标题/正文、明确待迁移状态；断网/重复选择/迟到响应回归。当前完成。
2. **NUI-2：把已有原生业务补齐。** 项目设置、原作修订/删除/撤回、任务详情、
   全局供应商与运行/诊断设置、首页创建参数。实现完成；真实供应商验证与付费冒烟
   另行验收。
3. **NUI-3：资产与剧本。** 五类子导航、档案编辑/上传、包版本/引用与场景/剧本编辑，
   沿用同一接口和版本语义；缩略图分页、原图按需加载并取消离开后的请求。实现完成。
4. **NUI-4：两种画布。** 分镜联系表/画布/检查器与工作流 DAG 分开实现；统一坐标、
   缩放、键盘、选择、撤销、草稿冲突和离开确认；手势在失去鼠标捕获（Alt-Tab）后
   自动提交并去重。实现完成。
5. **NUI-5：生成与导演闭环。** 候选/素材库/大图、生成/采用/质检/导出、导演台规则桩
   （envelope UUID 与后端契约一致、角色解析优先级对齐 web、支持改口令重发）。
   原实现已落地；2026-09-08 接入局部编辑（mask 重绘）入口，完整实机对照仍属 NUI-6。
6. **NUI-6：全局完整性。** 用量看板、帮助全文、任务成本详情、全局任务底栏、快捷键、
   焦点恢复、空/错/等待状态与所有弹窗对照；17 页和内嵌工作区全部逐项验收。
   2026-09-08 已完成 NUI-6A（任务中心+账本）与 NUI-6B（全局任务底栏、帮助全文、
   Enter/Esc/焦点对照，见顶部章节）；NUI-6C 状态矩阵待做。
7. **NUI-7：流畅性与发布。** 940/1280/1440 DIP 与 100/125/150/200% DPI 对照；采集启动
   首帧、导航响应、长列表内存与双画布拖拽帧时间，保留全部样本（包括失败）；固定数据集和
   硬件后设立预算，不以主观“像 Codex”宣称达标。受支持 .NET LTS、安装/升级、签名及
   退出/崩溃清树独立发布验收。不得用离屏渲染替代窗口实机或性能数据。待做。

## 本轮验证（2026-09-07，审查轮 2 修复后）

- 审查轮 1/2（子代理）累计报告 B-1~B-8 共 5 组 P1 与 9 项 P2/P3；本轮全部修复：
  导演台 envelope（裸 UUID + projectId 注入，对真实后端不再 422）、四处“共享控件
  字段 + 重建容器”闪退、项目切换离开确认/回滚/取消旧读取、工作流拖拽双重换算与
  保存 flush 序列化、跨项目导航保留目标段；P2/P3 含分镜手势失去捕获提交、气泡回弹
  Moved 复位、ImageBox 失败占位、导演台改口令重发、素材库级联加载去重、原作多章
  撤回、风格页轮询不丢输入、死代码清理。
- 审查轮 3（独立子代理复审）：**无 P0/P1**；唯一 P2（工作流保存 in-flight 导航
  窗口）与可顺手修的 P3（DirectorRules 与 web 的文案级差异、UUID 36 位断言等）
  已随后修复并复验。
- 构建：native 与 native-tests Release 均 0 错误。
- 回归：`dotnet run -c Release --no-build --render` 全绿——28 项基础断言 +
  DirectorRules envelope/优先级断言 + 新增 NativeRerenderChecks 二次 Render 冒烟
  （B-2~B-5 崩溃路径回归）+ 1320/940 离屏渲染。
- 真实后端烟测（两轮）：native-host 握手 → healthy → CloseMainWindow → 进程树
  清理，退出码 0。
- 未进行真实供应商调用、DPI/性能采样、安装/签名验证或浏览器 E2E。改动留在工作树，
  未提交或合并（是否提交由用户决定）。
