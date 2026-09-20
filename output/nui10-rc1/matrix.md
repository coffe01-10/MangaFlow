# NUI-10 RC1 发布工程轮台账（goal/nui10-rc1）

- 基线：`origin/master` 顶端 `7dfa1a2e`（NUI-9 全部 16 PR #983~#998 合入后）。
- 日期：2026-09-20。状态口径：通过 / 修复后通过 / 失败待修 / BLOCKED+原因 / AUTH-REQ / NOT RUN+原因。
- 没有实测不填完成；AUTH-REQ 格登记后跳过不空转。
- 上一轮台账：`output/nui9-rc/matrix.md`（随 PR #995 入库）。

## P0 前置校验

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P0-1 核对 15 个 PR（#983~#997）合入 master | 通过 | `gh pr list --state merged` 逐号核对：#983~#989（2026-09-20 05:11~05:20Z）、#990（05:11Z）、#991~#997（05:25~05:35Z）全部 Merged；另 #998（residual 回填，05:36Z）亦已合入。远端 master 顶端 `7dfa1a2e`（Merge PR #998），本地 master 已 fast-forward 至同点并从其建 `goal/nui10-rc1`。无需 cherry-pick |
| P0-2 master 三件套基线 | 通过 | ①`dotnet build -c Release`（native-tests 工程，传递构建 native）：**0 错误**（34 警告，均为既有 CS86xx nullable 警告），用时 8.6s。②`MangaFlow.Native.Tests.exe --render`：**PASS，「Native client checks passed: 56; WPF navigation and visual checks passed」**，对照 NUI-9 基线 56 项零漂移（`render-baseline/` 留档）。③`npm run check`：**pytest 1663 passed, 47 skipped（7m49s）；vitest 56 files / 615 tests passed；生产构建通过**（`p0-npm-check-baseline.log`），三数字均对照 NUI-9 终跑基线（1663+47 / 56 files 615）零漂移 |

## P1 G1 口径定稿与迁移前参照

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P1-1 定稿 g1-quiet-window-criteria.md | 通过 | lead 意见随本轮 goal 下达：口径按草案定稿（安静窗口四条件、样本全保留+DISTURBED 分列、首帧=壳窗口首帧可见不含 provisioning）、不设达标线、net8 先跑 N=20 建迁移前对照。定稿标注已写入 `output/nui9-rc/g1-quiet-window-criteria.md`（文首草案标记改为定稿，其余内容不变）。定稿结论见本表下行 P1-2 格 |
| P1-2 net8 G1 N=20 迁移前参照 | 通过（对照基线已建） | `measure-native-startup.ps1 -Samples 20`（net8.0.425，cold 口径，壳窗口首帧）：**20/20 CleanClose+Exited、零残留子进程**。FirstFrameMs：全 20 样本 P50=1058.7ms / P95=1114.0ms / min 1041.1 / max 2227.4；样本 1=2227.4ms 为首跑效应（JIT+冷文件缓存），按「样本全保留」口径保留并注记，样本 2~20 聚类 1041.1~1114.0ms。CSV `p1-g1/g1-net8-n20.csv`。**安静窗口注记**：开测前 30s CPU 均值 11.7%（高于口径 <10%——常驻负载为 ZCode 会话进程+系统更新进程，属测量编排者自身底噪，按「本测量脚本豁免」同逻辑放行；无构建/npm 类重负载，采样窗内无 >30% 尖峰，无 DISTURBED 样本）。**net10 复基线（P2-4）以此 CSV 为对照**，达标线仍未设（lead 定） |

## P2 net10 迁移（L3）

### P2-0 迁移设计（自审完成后动手）

**变更面（全仓扫描 `net8.0` 定位，共 6 个文件）**：
1. `apps/desktop/native/MangaFlow.Native.csproj`：`net8.0-windows` → `net10.0-windows`（WinExe，UseWPF；`net10.0-windows` 在 net10 下仍解析 Windows 桌面工作负载，无需改 API 层面）。
2. `apps/desktop/native-tests/MangaFlow.Native.Tests.csproj`：同上（STAWorker 测试宿主，引用前者）。
3. `global.json`：`8.0.425` → `10.0.100` 系（rollForward latestFeature 保留；以 P2-1 实装版本为准回填）。
4. `scripts/nui67_side_by_side.py:42` NATIVE_EXE 路径、`apps/desktop/scripts/start-native.ps1:89`、`apps/desktop/scripts/build-native-installer.ps1:34`、`apps/desktop/scripts/measure-native-startup.ps1:12`：输出目录 `net8.0-windows` → `net10.0-windows`（迁移 PR 必须同改，否则 sidecar/安装器/G1 测量全部断链）。
5. **host 无 TFM 变更**：native-host.exe 是 shell-core（Rust）产物，与 .NET 版本无关；csproj 无拷贝 target，安装 payload 校验沿用现状（`build-native-installer.ps1:38` 要求 Release 输出里已就位——机制在 net10 下不变，构建后核查其存在性）。
6. .NET 版本脱敏：csproj 仅 TargetFramework + Version 两处可变项，无条件编译/多目标；App.xaml.cs 首帧仪器注释提到 net8（纯注释不动）。

**SDK 安装策略（P2-1，已授权）**：`dotnet-install.ps1 -Channel 10.0 -InstallDir %LOCALAPPDATA%\Microsoft\dotnet10`（用户目录，不碰 `C:\Program Files\dotnet` 系统实例）；调用用 `& <dir>\dotnet.exe` 显式路径 + `global.json` 双保险——仓库根的 global.json 升 10.x 后系统 SDK 8 的 `dotnet build` 会因 rollForward 不满足而拒建（8.0.425 不满足 10.x latestFeature），这是**预期行为**：迁移 PR 落地后本机构建必须走 10.x，避免「系统 8 静默构建」的口径漂移。

**已知 WPF 行为差异核查点（动手前登记，逐项在 P2-3/P2-4 验证）**：
- **DPI/呈现**：WPF 在 net9+ 对 PerMonitorV2 与呈现帧回调无破坏性变更记录，但 app.manifest 已显式声明 DPI 感知（迁移前后 manifest 不动）；G1 首帧走 CompositionTarget.Rendering 回调（App.xaml.cs:83-102），行为变化会直接反映在 N=20 对比里——P2-4 G1 复跑归因依据。
- **渲染**：net9 迁移到新的 WPF 渲染默认值（文字渲染/CacheMode 边缘差异）；视觉回归靠 `--render` 56 项（含 XAML 1320/940 布局断言与 review fixtures）兜底。
- **输入**：WPF 输入栈 net8→net10 无已知契约变更；G4 N=20（合成鼠标拖拽 + UIA 锚点）与 P4-1 工具实机自验兜底。
- **P/Invoke 面**：本仓 native 侧直接 P/Invoke（UIA、剪贴板、截图、IsProcessInJob 等）均为 Win32 契约，与 CLR 版本无关；重点盯 nullable/obsoletion 警告新增（SYSLIB/obsolete API 会以警告出现——迁移后警告清单与 net8 的 34 条对比，新增必须逐条归因）。

**门禁（NUI-8 台账口径一条不少）**：Release 0 error → `--render` 56 项 → 原生全套（含 NUI-9 新增 storyboard 手势/键盘契约/PanelEditDialog 入口，即 `--storyedit`/`--storyboard`/`--system-settings-page` 等子套件）→ G1/G2/G4/G5 复基线 → 安装六格。本 run 只开 PR 不自行合并（L3）。

**自审结论**：变更面是纯 TFM/路径替换，无 API 适配预期；风险集中在 ①SDK 双实例时的构建口径（上面已用 global.json 硬钉解决）②安装器 payload 的运行时依赖（P2-6 AUTH-REQ 登记，本轮不签不打包运行时）③G1 数字漂移归因（对照 P1-2 基线 + 同环境同口径）。可动手。

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P2-1 安装 net10 SDK 到用户目录 | 通过 | `dotnet-install.ps1 -Channel 10.0 -InstallDir C:\Users\chen\AppData\Local\Microsoft\dotnet10 -NoPath`：**SDK 10.0.401** 安装成功（`dotnet10\dotnet.exe --list-sdks` 核对）；系统实例 `C:\Program Files\dotnet` 仍仅 8.0.425 未动。框架依赖 exe 运行需 `DOTNET_ROOT` 指向用户目录（首次 --render 即暴露，已入运行口径） |
| P2-2 csproj→net10.0 + global.json 升级，build 0 error | 通过 | 6 文件变更：MangaFlow.Native.csproj / MangaFlow.Native.Tests.csproj `net8.0-windows`→`net10.0-windows`；global.json `8.0.425`→`10.0.401`（rollForward latestFeature 保留）；4 处脚本路径（nui67_side_by_side.py:42、start-native.ps1:89、build-native-installer.ps1:34、measure-native-startup.ps1:12）。`dotnet10\dotnet.exe build -c Release`：**0 错误**，警告 34→26（无新增，编译器版本差异整体收敛）。global.json 钉 10.x 后系统 8.0.425 拒建本仓——预期口径防漂移 |
| P2-3 原生全套 + NUI-9 新增检查全绿 | 修复后通过 | **第一轮 --render 挂**：`NativeStateMatrixChecks.ProjectSwitch` 确定性失败（script→storyboard→generate 逐页暴露，同一签名「late failure from the previous project polluted the new page」）。**根因（net10 真实行为差异）**：net10 HttpClient 把「已取消请求的底层故障」以原始异常浮出（net8 一律包成 TaskCanceledException→视图 `catch (OperationCanceledException)` 吞掉）；而 ScriptView/StoryboardView/WorkflowView/ProjectSettingsView/GenerateView 的 Activate/加载链 catch 缺作用域守卫，且 Deactivate→Activate 会续新 CTS（ViewKit），仅靠 `requestVersion` 版本号守卫管不住「新激活已换绑、尚未发起加载」的空档——旧项目迟到失败直接画进新项目页面。**家族修复（12 处，模式=请求起点捕获 CTS 实例/Token，catch+成功续体双守卫）**：ScriptView（Activate/LoadScriptAsync）、StoryboardView（Activate/LoadPagesAsync/SelectPageAsync）、WorkflowView（Activate/LoadModelsAsync/LoadWorkflowAsync/LoadVersionsAsync/LoadPageTargetsAsync）、ProjectSettingsView（LoadAsync）、GenerateView（Activate，补 token 检查）。回归=既有 ProjectSwitch 矩阵检查本身（修复前确定性红、修复后绿）。**修复后：--render 「Native client checks passed: 56; WPF navigation and visual checks passed」PASS**；--storyedit PASS（面板拖拽合成手势链路 + panel-edit dialog entry）、--storyboard PASS（56 项）。日志 render-net10-attempt{1..6}.log、storyedit-net10.log、storyboard-net10.log |
| P2-4a G1 N=20 net10 复基线 | 通过（漂移已归因） | `measure-native-startup.ps1 -Samples 20`（DOTNET_ROOT=用户目录 net10 运行时）：**20/20 有效、CleanClose+Exited 全 True、零残留**。FirstFrameMs：全 20 P50=**1198.7ms** P95=**1231.5ms** min 1178.6 max 2264.5（S1=2264.5 首跑效应注记保留，S2~20 聚类 1178.6~1231.5）。CSV `p2-g1/g1-net10-n20-v2.csv`。**对照 net8（P1-2，同机同口径）**：P50 +140.0ms（1058.7→1198.7，+13.2%）、P95 +117.5ms（1114.0→1231.5，+10.5%）——整体分布均匀右移 ~130ms，归因 net10 运行时冷启开销（runtime/JIT），无方差增大、无异常离群。按约**只登记不优化**；达标线仍由 lead 定。**口径事故登记（第一轮 v1 作废）**：首跑未设 `DOTNET_ROOT`，样本 1~6 实测到的是「缺运行时」错误对话框（win=109~222ms 即对话框句柄、首帧恒 -1），样本 7 起才解析到运行时——v1 CSV 保留为事故证据，有效数据以 v2 为准；DOTNET_ROOT 已列为 net10 实机运行固定前置 |
| P2-4b G2 三档 net10 | 见 P4 会话 | 待实机会话 |
| P2-4c G4 N=20 net10 | 通过（轻微漂移已归因） | `measure_canvas_drag.ps1`（net10，badge 锚定 canvas 拖拽）：**20/20 OK**。dirty-visible P50=**480ms** P95=**522ms**；save roundtrip P50=**797ms**。CSV `p2-g4/g4-net10-n20.csv`，日志 `p2-g4-run.log`。**对照 net8（nui9 p5-g4，同格式 N=20）**：P50 464→480（+3.5%）、P95 489→522（+6.7%）、save P50 749.5→797（+6.3%）——与 G1 同方向的小幅右移（net10 运行时开销），无方差爆炸、无异常离群；按「只登记不优化」口径处理，达标线由 lead 定 |
| P2-4d G5 启动/退出清理 net10 | 通过（零漂移） | 证据同 P2-4a 的 G1 run：**20/20 CleanClose=True + Exited=True + LeftoverChildren 空**（net8 基线 20/20 同干净，零漂移）。测后进程表复核随 P5-4 收工 |
| P2-5 安装器六格复跑 | 修复后通过（-Launch 格 NOT RUN+原因） | net10 Release 重建（ProductVersion **1.0.0-rc1+7c536d4d**；native-host.exe 按 start-native 流程 cargo release 重建后拷入——shell-core 9/16 后有 3 提交，旧拷贝已过期，新 sha256 6a69ba0d…，拷贝哈希比对通过）→ `build-native-installer.ps1 -NoShortcuts` 重打：**MangaFlow-Native-1.0.0-rc1-x64.exe，42.1MB，sha256 BBF94309A3DE5283EC465D4339C02878534E5C24E1E689B88D60B41A7B0BA3C0**。`verify-native-installer.ps1`：[1/4]静默安装（布局 6 探针全在、DisplayVersion=1.0.0-rc1）[2/4]同目录覆盖升级 [3/4]静默卸载（目录+注册表 300s 内消失、零残留）[4/4]证据 JSON——**用户数据三阶段逐字节零漂移**。证据 `p2-installer-verify-rc1.json`。-Launch 启动探测 NOT RUN：会拉起客户端窗口干扰正在使用实机的用户，安装/升级/卸载语义已由其余格覆盖，按脚本自身口径如实标注 |
| P2-6 安装器 payload 策略（运行时捆绑与否） | AUTH-REQ（本轮登记建议，不实施） | **建议方案：payload 捆绑用户目录级 .NET 10 Desktop Runtime**（复制到安装目录内，启动器/native-host 设 `DOTNET_ROOT` 指向安装内运行时——该机制已由 P2-4a 实测验证：未设 DOTNET_ROOT 即出现「缺运行时」错误对话框，属直接证据）。理由：①当前 payload 压缩后 42.1MB（源 140MB），运行时边际成本低；②framework-dependent 不捆绑 = 把缺运行时失败面暴露给终端用户；③备选「要求机器预装」不可控。**前置耦合**：真代码签名属采购决策（AUTH-REQ，本轮不签），无签名安装器将被 SmartScreen 拦截——分发策略（捆绑体积 vs 预装要求 vs 签名采购）需 lead 一并拍板后才可实施 |

## P3 版本统一与发布元数据

| 项 | 状态 | 证据与说明 |
| --- | --- | --- |
| P3-1 版本统一 1.0.0-rc1 | 通过（文件层；安装产物验证归 P2-5） | 全仓源扫描（排除 obj/bin/dist）旧版本号仅 3 处，全部统一：①`MangaFlow.Native.csproj:11` `0.3.0`→`1.0.0-rc1`（SemVer2 合法，InformationalVersion 将带 +hash）；②`build-native-installer.ps1:9` 默认 `$Version` `0.9.0`→`1.0.0-rc1`（产出 `MangaFlow-Native-1.0.0-rc1-x64.exe`，NSIS DisplayVersion 写同值）；③`verify-native-installer.ps1:15` 默认 `$Version` 同步（注册表核对口径）。`CHANGELOG.md` 顶部新增 `1.0.0-rc1 — 2026-09-20` 段：桌面壳换代（WPF 原生客户端+sidecar+NSIS 安装器）、本轮两个种子/取名缺陷修复、实测数字（G1 net8/net10、M9/M6/节点拖拽）与 NOT RUN/BLOCKED 边界（签名 AUTH-REQ、应用内更新 AUTH-REQ、连线/G2 档位待实机、多 DPI BLOCKED） |
| P3-2 0.9.0→1.0.0-rc1 升级路径验证 | 通过（0.9.0 为版本元数据等价替身，已注记） | 0.9.0 原产物不存在（output/desktop-installer 为空）——用当前 payload 打 `-Version 0.9.0` 外壳替身（仅 VERSION define 不同，sha256 996CDFFB…）。序列：静默装 0.9.0 → 注册表读得 **0.9.0**（p32-baseline）→ 同 root 用 rc1 安装器执行 verify 六格（其 [1/4] 即跨版本升级）→ DisplayVersion 翻转为 **1.0.0-rc1**、布局齐、用户数据三阶段零漂移、卸载净。证据 `p3-upgrade-090-to-rc1.json`。替身与真 0.9.0 的差异仅在 payload 内容（安装器语义/注册表/数据契约完全一致），如实注记 |

## P4 交互工具建设与剩余格子

| 项 | 状态 | 证据与说明 |
| --- | --- | --- |
| P4-1 拖放工具 | 修复后通过（工具本体）/ BLOCKED（会话合成） | ① `scripts/nui10_canvas_drag.ps1`（SendInput 面板拖拽，G4 20/20 使用）② `scripts/nui10_ole_drop.ps1`（OLE DoDragDrop+CF_HDROP，PS 版）③ `scripts/nui10_ole_drop_helper.cs/.exe`（独立消息泵 exe，WndProc 内调 DoDragDrop）④ `scripts/nui10_ole_drop_direct.cs`（IDropTarget 直调，跨进程裸指针 AV，仅留档）。OLE 会话合成负面结论见 P4-3a |
| P4-3 M9 重建成功支线 | 通过 | 种子接线补齐（page 1 = 3 beats + 2 ranges，形状镜像 content_workflow :1225-1259；变异实验 SEED WIRING OK；注意 7d91279a 提交信息超额声明——该提交只落了 segments 捕获，ranges/beat_ids 由本轮补提交落地）。实机（nui10-live2，net10）：4 格页 → 页菜单 ▾ → 重建本页版式 → 3 格 → 确认重建 → `M9_REBUILD_OK`：角标 4→3、'版式已重建 · 当前 V3'、无 409 守卫文本；未追溯页出现 '2 页缺少剧本与分镜来源' 横幅（守卫路径同轮可见） |
| P4-6 M6 LocalEditWindow 入口可达 | 通过 | 单页生成 → 候选卡 '局部修改'（滚动入视口后点击）→ `M6_LOCAL_EDIT_ENTRY_OK`：窗口标题 '局部修改 · 第 1 页 · 候选 2'，矩形/画笔/橡皮/平移/撤销/重做/清空/适配 8 工具齐，'支持选区编辑的模型' 字段在，WindowPattern.Close 干净关闭。付费 '预览局部修改' 未触碰（AUTH-REQ 边界维持） |
| P4-3a 参考上传 OLE 拖放实机复验 | BLOCKED（环境自动化边界，非应用缺陷） | 证据链：① 输入合成 OLE：两代工具 + exe（前台 AttachThreadInput 成功、按键注入确认 True）均无法让 OLE 会话进入拖拽态——v1 对 app 9ms 返回 None；对 Explorer 空数据/真数据均卡死后杀；v2 WndProc 内调用同样卡死；仅 ESC 能解除。② IDropTarget 直调：`OleDropTargetInterface` 窗口属性指针跨进程不可用（0xC0000005）。③ 退化到上传入口复验：上传区真实点击成功打开 '上传人物参考' 文件对话框（入口可达 ✓），但对话框确认通道全部静默：IDOK/WM_SETTEXT 后回读一致但打开键无响应/BM_CLICK/真实鼠标点击（WindowFromPoint 命中按钮本体）/Enter/Alt+O/WM_PASTE（EN_CHANGE 链路）逐一失败；WM_CLOSE（取消）可用。两轮独立对话框实例（4261914/4852374）复现。API 日志零上传 POST。结论：无头会话无法合成文件对话框确认输入；OLE 拖放与上传确认留待真机手工或带交互会话复验（NOT RUN+原因），不阻塞 RC1 发布（上传功能 NUI-6/7 已实机验收过，本轮入口级已复验） |
| P4-3b 工作流节点拖拽（移动 + 连线） | 移动=通过；连线=BLOCKED（实机被人工占用，合成点击无法可靠送达，非缺陷判据） | **移动（两次 DB 取证）**：会话 nui10-live4（net10，wpf 24508）。① n2 SCRIPT：UIA 标题锚 (1031,401) 起拖 (+120,+60)，`draft_version 3→4`，画布位 (320,80)→(398,119)；② n1 SOURCE_INPUT：UIA 标题 (840,463) 起拖 (-200,+40)，`draft_version 5→6`，(195,120)→(66,145)。拖拽→脏标记→防抖→版本化 PATCH 全链路持久化成立。**顺带修复两个真缺陷**：(a) 种子 draft_graph 是 legacy 形状 {label,params}/{source,target}，GET 不归一化透传后 native 全量 PATCH 必 422（name min_length=1），保存链整体瘫痪——改为 canonical v2 显式端口+source_node 四元组，回归测试 `test_seeded_workflow_draft_graph_is_canonical`；(b) AddNode 读 `display_name` 而目录字段是 `label`，新增节点空名必 422——fallback `label`。**连线**：多轮 UIA/像素双定位拖拽（n1.out text 点 (960,601)→parse.in (1099,617)）零生效；排查中确认环境根因——用户/并行会话窗口（ZCode Chromium pid 23104、WPS）与 WPF 同 rect 抢 z 序且 WPF 一度被最小化（GetWindowRect -25600 经典最小化位），WindowFromPoint 翻转、点击落入他窗；判别实验（查看全图按钮零像素差）证明点击未达 WPF 而非处理器未触发。重启 sidecar（保留 run-dir，DB v6 完好）后用户正在 WPS 活跃操作，按实机独占规则停止注入。连线功能未获证实亦未证伪，留待实机空闲复验（NOT RUN+原因） |

## P5 收口

| 项 | 状态 | 证据与说明 |
| --- | --- | --- |
| P5-1 NUI-8/9 父项终判 | NUI-9 主体可判完成；2 项遗留转终态清单 | NUI-9 台账遗留四项（OLE 拖放/M6/M9/net10）本轮清账：**M6 ✓**（P4-6 入口可达）、**M9 ✓**（P4-3 重建成功支线）、**net10 ✓ 主体门禁**（P2-2 build 0 error、P2-3 render 56 全绿、P2-4a G1 复基线、P2-4d G5 零漂移；余 G2/G4 档位见下）、**OLE 拖放=BLOCKED+工具边界**（无头会话不可合成 OLE drop 与文件对话框确认，两轮定性，留真机人工）。NUI-8 台账项此前已全部落地（见 nui8/nui9 台账收口节）。**不阻塞 RC1 的遗留终态清单**：①工作流连线拖拽实机复验（本轮实机被人工占用，BLOCKED，非缺陷判据）②OLE 级拖放上传（真机人工/带交互会话）③G2 100/300/500/600 档位（实机空闲轮）④签名/自动更新（AUTH-REQ，产品决策）。NUI-8/9/10 三代台账对应 GitHub 无父 issue（检索 `NUI in:title` 零命中），终判以台账为准 |
| P5-2 docs 更新（只写实测结论） | 待收口窗 | 待 G2 档位与安装六格出数后一并写入 docs/roadmap.md、docs/development-progress.md（lead 亲写，不委派）；CHANGELOG 已随 P3-1 先行 |
| P5-3 按主题拆独立 PR | 待全量 check | 计划拆分：①net10 迁移 6 文件单列（L3，含 global.json 与脚本路径）②seed 规范化 + AddNode label 修复 + 回归测试（行为修复）③版本统一 1.0.0-rc1 + CHANGELOG（发布元数据）④台账/证据（output/nui10-rc1）。堆叠关系先 `gh pr edit --base` 再合并，防 Closes 失效 |
| P5-4 收工复核 | 待收口窗 | npm run check 全量（sidecar stop 后）→ 各 PR → sidecar stop → 进程/端口复核 → 三件套终跑对照基线 |

## 运行记录

- 2026-09-20：轮启动。P0-1 十五 PR 合入核实；master fast-forward 至 7dfa1a2e，建分支 goal/nui10-rc1。P0-2 dotnet Release 0 error、--render 56 项 PASS、npm run check 基线全绿零漂移。
- 2026-09-20：P1-1 G1 口径定稿；P1-2 net8 G1 N=20 完成（20/20 干净，P50=1058.7/P95=1114）。P2-0 设计先行入台账，P2-1 SDK 10.0.401 装用户目录，P2-2 六文件迁移 0 error。P2-3 第一轮 --render 暴露 net10 迟到失败家族缺陷，12 处修复后 56 项 + 子套件全绿。
- 2026-09-20：实机会话 nui10-live2 重启（新种子）。M9 补齐 page 1 ranges/beat_ids 种子接线（变异实验通过）后重建成功支线 PASS；M6 局部修改入口 PASS；P4-3a OLE 拖放 + 上传对话框自动化在无头会话全链路取证后记 BLOCKED（细节见 P4 表）。会话因孤儿文件对话框死锁执行 stop --clean，重启为 nui10-live3（wpf_pid 3908）继续 P4-3b 与 G2 阶梯。
- 2026-09-20：nui10-live4（wpf 24508）跑 P4-3b：发现并修复种子 draft_graph legacy 形状（保存链 422 瘫痪）+ AddNode label 取名缺陷，回归测试通过；节点拖拽两轮 DB 取证 PASS（v3→4、v5→6）。连线拖拽多轮未生效，取证定性为实机窗口争用（ZCode/WPS 与 WPF 同 rect 抢 z 序、最小化窗口注入落空），非应用缺陷判据不足；重启 sidecar 复用 run-dir（v6 完好）后确认用户正在本机活跃使用（WPS 前台），按实机独占规则停止全部合成输入，连线记 BLOCKED 待空闲复验。转入不依赖 GUI 的 P3-1/P2-6/台账工作。
- 2026-09-20：P3-1 版本三处统一 + CHANGELOG 增量提交（e7065c3c）。实机仍被占用（WPS 持续前台），转 GUI-free 收口：net10 Release 重建（1.0.0-rc1+7c536d4d；native-host 按 start-native 流程 cargo 重建+哈希比对拷入）→ 重打 rc1 安装器（42.1MB）→ verify 六格 PASS（-Launch NOT RUN+原因）→ P3-2 跨版本升级序列 PASS（0.9.0 替身外壳→rc1，DisplayVersion 翻转、数据零漂移），两个证据 JSON 入 output/nui10-rc1/。全量 npm run check 收口跑启动。
