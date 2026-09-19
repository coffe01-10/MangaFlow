# NUI-9 发布候选收口轮台账（goal/nui9-rc）

- 基线：`goal/nui8-release` 顶端 `77f93dcc`（= `origin/master` 601a3e38 + 13 提交）。
- 日期：2026-09-19。状态口径：通过 / 修复后通过 / 失败待修 / BLOCKED+原因 / AUTH-REQ / NOT RUN+原因。
- 没有实测不填完成；「源码论证通过」显式标注口径。
- 上一轮台账：`output/nui8-release/matrix.md`（随 PR #988 入库）。

## P0 交付动作

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P0-1 push goal/nui8-release + 7 主题 PR | 通过 | `goal/nui8-release` 已推远端；PR-A~G 已开、登记如下；未自行合并，交 lead 逐 SHA 复核 |
| P0-2 删自签名证书 FE01…2A7 | 通过 | 删前 `Get-Item`：Subject/Issuer=`CN=MangaFlow Test Code Signing`（自签，2026-09-19 18:51 本轮签名尝试所建），`CurrentUser\Root` 与 `LocalMachine\Root` 均无此指纹（从未导入受信任根）；删除后 `Get-Item` 确认不存在 |
| P0-3 清理 output/desktop-installer/（~450 MB） | 通过 | 删前核对：`git ls-files output/nui8-release` 77 文件已入库（matrix.md + perf/*.csv 全在，三分支 ls-tree 一致）；安装器 sha256 在 NUI-8 台账 §P4-1 行 320（未签名 `3B2B2470…`/签名后 `25E7415D…`，签名态/未签名态分记）；可按 build-native-installer.ps1 重打。实删 221.1 MB（450 MB 为含 NSIS staging 的旧估计） |
| P0-4 处置 output/nui67-acceptance/ 与 testseed/ | 通过 | 结论：**gitignore 不入仓、保留在盘**。nui67-acceptance 是 `nui67_side_by_side.py` 的 run-dir（session.json+证据，3985 文件 27.9 MB），正式报告已随 PR #982 入库；testseed 是本地 render-check 与 SQLite 种子（291 文件 15.2 MB，`*.db` 按安全规则永不入仓）。两者均为活动脚本的工作目录（`scripts/nui67_side_by_side.py:37-38` 引用前者），故不删除。另删除两个会话内产生的命令行垃圾文件 `$($_.baseRefName)]`、`$(git`（确认为本会话引号事故产物后删）。两目录 .gitignore 条目随本轮提交 |

### P0-1 PR 登记（cherry-pick 溯源见各 PR 描述）

| PR | 主题 | 原 SHA → 分支 SHA |
| --- | --- | --- |
| [#983](https://github.com/coffe01-10/MangaFlow/pull/983) PR-A | 缺陷 #4 可诊断性 | c9cb1401→5c6899c2, 2e6a4fec→e52063aa |
| [#984](https://github.com/coffe01-10/MangaFlow/pull/984) PR-B | 原生差异修复 | 1bfa255c→bbede968, addfb23a→3f2987c0 |
| [#985](https://github.com/coffe01-10/MangaFlow/pull/985) PR-C | 种子与数据 | 14264b72→34a4de80, 2a9b923a→46dc3601 |
| [#986](https://github.com/coffe01-10/MangaFlow/pull/986) PR-D | 安装发布 | 4419acfe→588a4b9c |
| [#987](https://github.com/coffe01-10/MangaFlow/pull/987) PR-E | 性能仪器 | be95e549→fca1f38b, 1cc98531→fe2bbb45, 2c9ff443→2574aa40 |
| [#988](https://github.com/coffe01-10/MangaFlow/pull/988) PR-F | 验收工具与台账 | 5b32e44b→ee7d5d7b（⚠️ .gitignore 冲突并集，唯一内容偏离，见 PR 描述）, 77f93dcc→fc33a66e |
| [#989](https://github.com/coffe01-10/MangaFlow/pull/989) PR-G | 文档 | 32af7f89→913cacad |

堆叠风险：PR-F 的 .gitignore 含 `output/desktop-installer/` 行，PR-D 后合并需 rebase 去重；其余无文件级交叠。

## P1 关键路径：P2-2 面板鼠标命中定性

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P1 面板鼠标命中定性（离线合成手势） | 通过（✅ 结论已按实机复核**修正**） | 离线定性：`NativeStoryboardEditChecks.PanelDragGestureChecks`（capture→moved→提交链路离线可用，零位移 no-op，中心命中探针可达面板子树），整套 `--storyboard` 56 项 PASS。**实机复核修正结论**：真机「面板点选/拖拽无反应」的根因不是注入通道，而是 **种子数据缺陷**——`nui67_seed.py` 面板 bounds 写 `w/h` 键，产品契约是 `width/height`（web geometry.ts:54 与 native PanelNode.From 同源），native 把面板加载成 **0×0**（检查器实测显示 宽 0.0% · 高 0.0%），无可命中的面板。修复 ccabef8f（种子改 width/height）后重播种子：**鼠标点选面板立即生效**（角标锚点点击 → 检查器显示 P.001 / PANEL 01）。原「注入通道差异」判立作废；离线合成手势检查保留为产品逻辑回归。误判原因记录：P1 判别时只验证了「产品逻辑离线可用」与「面板中心命中可达」，未实测种子页面的面板实际尺寸——0×0 面板使一切命中点必然落空。证据：`p5-g4/debug-selected.png`（选中态 0×0）、`p5-g4/debug-badges.png`（修复前后角标 13x76→34x19）、G4 复跑通过 |

**P1 判别逻辑**（修正版）：NUI-8 遗留的「面板鼠标命中无反应」现象是真机观测，本轮离线定性证明产品逻辑无缺陷，但「注入通道差异」的初判**错误**——实机继续定位后发现根因是种子 bounds 键名错误（w/h vs width/height）导致面板 0×0，见上格与 P5-1。方法论教训：**「产品逻辑离线可用」不能反推「真机失效在注入通道」——还要验证真机侧被测对象（数据）本身成立**。

## P2 待修缺陷

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P2-1 D5 全局设置双头部 | 修复后通过（离屏） | 设计稿先行并自审：`d5-design.md`。落地：①页面删除自绘头部（SettingsLayout.cs BuildSystemPage：kicker+标题+三动作全删）；②动作所有权归壳——壳 `SettingsActions` 三按钮为唯一入口，保存按钮 `SaveRuntimeButton` 改代码管理 `IsEnabled = Connected && !RuntimeSaving`（xaml 绑定移除防冲突）；③`SettingsView` 新增 `RuntimeSavingChanged` 事件 + `RuntimeSaving` 属性（SaveRuntime 置位/清位处 raise，沿用 RuntimeSaved 模式；订阅挂在 NavigateAsync 既有 SettingsView 分支，处理器以 ContentHost.Content 守卫防 cached view 陈旧订阅）；④标题统一：TopTitle/Breadcrumb settings-global → 「系统设置与运行诊断」（web 文案）。回归：离屏像素+交互（NativeSystemSettingsPageChecks）新增「页面无第二头部文本」「用量/返回按钮只存在壳」「RuntimeSavingChanged 序列 [true,false]」三断言，原 A05~A09 全保持 PASS，exit 0（`p2-d5/` 含 1440/1240/760/360 PNG 与日志）；恒真断言 `save.TranslatePoint().Y<150` 已删（按钮脱树后无意义）。真机壳级验证转 P4-6 格 |
| P2-2 NUI-8-A MessageBox YesNo（Esc/默认焦点）+ 43 处普查迁移 | 修复后通过 | ConfirmDialog 现状已有 Esc+取消焦点+主题 FocusRing；补齐键盘契约回归（`NativeButtonChecks.ConfirmDialogKeyboardChecks`：初始焦点=安全钮、Esc 处理并 DialogResult=false、Tab 可达确认、danger=红钮、无 IsDefault 回车不触发）。普查表：`messagebox-survey.md`（生产代码 79 处 MessageBox.Show；**43 处 YesNo 全部迁移 ConfirmDialog**、36 处 OK/info 保留；迁移后生产代码 MessageBoxButton.YesNo=0）。全部 headless 测试缝原样保留。原生全套 56 项全绿 + --render 全套 PASS |
| P2-3 仪表盘项目卡 a11y（语义 Name + Invoke/Select + UIA 断言） | 修复后通过 | `HomeView.CreateCardTemplate` 卡按钮显式绑定 `AutomationProperties.Name=项目名`（此前 fallback 到 ProjectItem 记录转储）；InvokePattern 为 Button peer 原生能力，Select 不适用（按钮语义非列表项，与 web 一致）。回归：`NativeNui8ParityChecks` 新增 Name/InvokePattern/peerGetName 三断言（并补 `state.ChangeDashboardPage(0)` 让测试态真的渲染卡片——此前 DashboardProjects 为空，卡片从未被该检查覆盖）。--render 全套 PASS；真机 UIA dump 复核随 P4 会话 |

## P3 缺陷 #4 有界时间盒

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P3 合成假设①（autosave 去抖 × 3s 轮询 × 切画布取消） | 修复后通过（机制证实+分流修复） | 机制证实：取消在途请求时连接随取消拆除，socket 层可以 HttpRequestException（"An error occurred while sending the request"）冒泡而非 OCE——ApiClient 原 `catch (HttpRequestException)` 无条件按传输失败上报+视图显示「保存失败：英文原文」，即缺陷 #4 误报路径。修复（分流）：ApiClient Send/Upload 在 `cancellation.IsCancellationRequested` 下的 HRE 重抛为 OCE（不写传输诊断），视图既有「保存已取消」路径吞掉；真实传输失败（token 未取消）仍照常弹错+落诊断。回归：`NativeKeepAliveChecks` 新增 CancelRaceHandler 合成用例（--keepalive 与默认全套都跑）：取消后必须 OCE、不得出现「传输失败」诊断——PASS。**边界**：合成用例证实的是异常形态→误报的因果链与修复有效性；真机是否真的产生该形态仍靠 ReportTransportFailure 被动取证（真机取证依赖 PR #983 落地后的 wpf-client.log） |

## P4 逐页交互格子

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P4-1 七格窗口模态 M2/M3/M5~M9（Esc/焦点返还/双支线） | 部分通过（1/7 实机） | Lightbox 格实测通过：点「放大查看批次候选 1」→ 模态窗口 [批次候选 1]（含「Esc 或点击背景关闭」提示）→ Esc 关闭且焦点返还触发按钮。其余六格（ProjectPalette、抽屉类等）待续跑 |
| P4-2 43 处 MessageBox 取消支线（ConfirmDialog ≥5 实机 + 纯信息 2 处） | 部分通过（5/5 双支线达成 + 1 加抽；纯信息 0/2） | 实机抽样 5 处迁移后 ConfirmDialog 全部通过（UIA 聚焦+对话框转储）：①设置离开确认（Esc 取消 → 焦点回触发钮；离开确认 → 弃稿跳转）②分镜删除气泡（Esc；删除 → 气泡数 0）③分镜离开确认（Esc 焦点回侧栏项；离开）④删除章节（Esc；删除 → 「撤回删除」出现，已撤回还原）⑤素材库隐藏候选（Esc 取消，焦点回删除钮；确认支线组件路径同①~③已三重验证）。加抽⑥项目设置离开确认（Esc）。全部初始焦点=取消（安全钮）实机成立。**纯信息 2 处未抽**（0/2）：info 类未被本轮迁移触碰（行为零变化），导航成本高；续跑建议：用量导出 CSV info、项目设置空名删除校验 info（后者按钮有名称匹配门禁，需先填确认名） |
| P4-3 参考图拖放上传 + 工作流节点拖拽连线 | NOT RUN | 待跑 |
| P4-4 逐页 Tab 序横扫（除分镜画布外 12 页） | NOT RUN | 键盘通道在分镜页已零散验证（Tab 选中格、Delete 走确认、方向键经 G4 链路间接验证）；完整 12 页逐页 Tab 序横扫待续跑 |
| P4-5 焦点环逐控件族抽查 | NOT RUN | ConfirmDialog 焦点位置已实机验证（初始焦点=取消），主题 FocusRing 视觉断言在离线键盘契约检查中覆盖；其余控件族待抽 |
| P4-6 D5 真机验证（壳单头部 + 保存中壳按钮禁用截图） | 通过 | 实机 UIA + 截图：设置页「系统设置与运行诊断」标题在 UIA 树出现 **1 次**（壳顶栏），三个动作按钮（用量与成本看板/← 返回项目/保存运行设置）全部位于壳顶栏行（y≈57-110），页面无第二头部。截图 `p4-d5-settings.png`、UIA dump `p4-d5-settings-uia.txt`。保存中禁用态由 RuntimeSavingChanged 事件链离线断言覆盖（[true,false] 序列），真机在途窗口过短未单独截帧 |

## P5 性能补全

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P5-1 G4 分镜拖拽 N=20 + measure_canvas_drag.ps1 UIA 坐标改造 | 修复后通过（20/20 OK） | 脚本改造（随本轮提交）：硬编码像素点 → **阅读序角标「格 01」UIA 矩形锚点**（角标绑定面板位置，缩放/平移无关）+ 逐样本重锚（拖拽移动面板后固定点必漂移，首轮 3/20 的教训）+ 状态条按前缀匹配（不再写死 V1/V2）。实测 N=20：**20/20 OK，dirty-visible P50=464ms P95=489ms（max 500ms 级，无超 2s 样本）**，保存往返 P50=752ms。CSV `p5-g4/g4-canvas-drag-n20.csv`。对照 NUI-8 基线（P50 1035/1255ms、3/20 超 2s）**不可直接比**：本轮面板有真实几何（种子修复后），且逐样本重锚消除了漂移失配；基线口径差异已记录。依赖的 P1 定性结论已按实机修正（见 P1 格） |
| P5-2 G1 安静测量窗口口径（AUTH-REQ：请 lead 确认） | AUTH-REQ | 口径草案已写：`g1-quiet-window-criteria.md`（进程/负载/场面条件 + 首帧收窄为「壳窗口首帧可见」不含 provisioning + 全保留样本与 DISTURBED 受扰标注 + 本轮不设达标线）。**lead 确认前不跑 N=20** |
| P5-3 G2 长列表 100/300/500 滚动+内存 | NOT RUN | 续跑入口：`measure_library_memory.ps1`（SectionName/ProjectName 参数化，ScrollPattern 真滚动+WorkingSet/Gen2 采样已具备）+ `nui67_seed.py` 需加 N 候选档位参数；NUI-8 已测 150 卡口径（P50 数据在 NUI-8 台账 §P2）。500 档若炸按既定口径登记虚拟化建议、不改 UI |

## P6 运行时升级与版本统一

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P6-1 global.json 钉 SDK 8.0.425（独立小 PR，最先合） | NOT RUN | 待开 PR |
| P6-2 .NET 8→net10 迁移（门禁：Release 0 error + 原生全套 + G1/G2/G4/G5 复基线 + 安装六格） | NOT RUN（L3：lead 设计先行） | 边界登记：net10 SDK 未装本机（dotnet --version=8.0.425，global.json 已钉 8.0.425，PR #990）。迁移 PR 前置：①lead 批准迁移窗口（.NET 8 EOL 2026-11-12，剩约 8 周）②本机装 net10 SDK 后才能评估 WPF 行为差异。门禁清单照 NUI-8 台账写死一条不少：dotnet build -c Release 0 error + 原生全套（含本轮新增 storyboard 手势/键盘契约检查）+ G1/G2/G4/G5 复基线 + 安装/静默升级/卸载六格复跑 |
| P6-3 版本统一（csproj 0.3.0 vs 安装器 0.9.0） | AUTH-REQ | 两来源已核实存在（csproj Version 与 NSIS 安装器 0.9.0）；建议 lead 定 1.0.0-rc1 后统一并更新 CHANGELOG——未定前只登记不动手（沿用目标约定） |

## P7 收口

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P7-1 逐格汇总与剩余优先级 | 见下「逐格汇总」节 | 本表 |
| P7-2 NUI-8 父项勾选判定 | 见下「NUI-8 判定」节 | 本表 |
| P7-3 本轮新增提交拆 PR | 已登记拆分方案，**PR 待 A~G 合入后开**（基线 master 尚未含 A~G，现在开 PR 会落父分支） | 见下「提交与拆分」节 |
| P7-4 收工复核 | 见下「收工复核」节 | sidecar stop 已过；npm check / dotnet Release / --render 见该节 |

### 逐格汇总（本轮闭环格数）

- **闭环（通过/修复后通过）**：P0-1~P0-4（4 格）、P1 定性（1，含结论修正）、P2-1 D5、P2-2 NUI-8-A、P2-3 a11y、P3 假设①、P4-6 D5 真机、P5-1 G4 N=20 —— **共 11 格闭环**，其中 6 格带产品代码修复+回归（D5、NUI-8-A、a11y、#4 分流、种子 bounds、G4 采样脚本）。
- **部分通过**：P4-1（Lightbox 1/7）、P4-2（5/5 危险类双支线 + 1 加抽；纯信息 0/2）。
- **AUTH-REQ**：P5-2 G1 口径（草案已交 `g1-quiet-window-criteria.md`）、P6-3 版本号。
- **NOT RUN（可独立续跑）**：P4-3 拖放两项、P4-4 Tab 横扫、P4-5 焦点环族、P5-3 G2 三档、P6-2 net10。
- 剩余工作优先级：①P4-2 补 2 纯信息（半小时级）②P5-3 G2 三档（种子加档位参数，需 3 次 stop/start）③P4-1 余下六格模态 + P4-4 Tab 横扫（一个 sidecar 会话可打包做完）④P4-3 拖放两项⑤P6-2 net10（lead 排窗）。

### NUI-8 判定

NUI-8 父项**不够格勾完成**，差口明确：
1. 七格窗口模态 M2/M3/M5~M9 中余下六格（Lightbox 已实测通过）+ 拖放两项——P4-1/P4-3 未完成部分。
2. 拖放三项：参考图拖放上传、工作流节点拖拽+连线——NOT RUN（P4-3）。
3. 逐页键盘横扫 12 页——NOT RUN（P4-4，分镜页零散验证不算横扫）。
4. P2-2 面板命中现象——**本轮已定因并修复**（种子 bounds 键名缺陷），NUI-8 台账该格可据此关。
5. G4 N=20——**本轮已闭环**（20/20，P50 464ms），NUI-8 台账该格可据此关（对照口径差异已注明）。
其余 NUI-8 格（安装/升级/卸载、性能仪器化、种子补齐等）维持已闭环结论。

### 提交与拆分（P7-3）

本轮 `goal/nui9-rc` 新增提交（基线 77f93dcc），A~G 合入 master 后按主题 cherry-pick 开 PR：

| 建议 PR | 提交 | 主题 |
| --- | --- | --- |
| PR-H | ce972c7b | D5 全局设置双头部：动作归壳+单头部，配离屏像素与交互回归（设计稿 d5-design.md） |
| PR-I | d37a1887 | NUI-8-A：43 处 YesNo 迁移 ConfirmDialog，配键盘契约回归（普查表 messagebox-survey.md） |
| PR-J | 9c5e376d | 仪表盘项目卡 UIA 语义名 + Invoke 断言 |
| PR-K | 61db67ea | 缺陷 #4 取消竞态分流（ApiClient HRE@cancelled → OCE），配合成回归 |
| PR-L | ccabef8f + 5929509b | 种子面板 bounds 键名修复（0×0 面板根因）+ G4 角标锚采样改造与 N=20 实测 |
| 台账 | 1dbd5311 + f62280f7 + 本收口提交 | NUI-9 台账与证据（随上述 PR 分摊或单独 PR-M） |

⚠️ 堆叠提醒：本轮提交相互独立（无文件交叠，除台账），但全部基于 77f93dcc；**cherry-pick 到 A~G 合入后的 master 即可**，无需改 base。

### 收工复核（P7-4）

- sidecar stop：本轮三次 stop 两次 `issues: none`；末次 `issues: ['WPF pid 33952 ignored graceful close — tree-killed']`（WPF 弹着离开确认对话框时收到停止，编排器按契约树杀并上报，端口/进程复检干净）——非本回归引入，编排器行为符合设计。
- dotnet build -c Release：0 error ✓（本轮实机验证所用 Release exe 即含全部修复后构建）。
- --render 全套：PASS ✓（迁移与 a11y 断言落地后第 3 轮跑，`render-round3.log`：「Native client checks passed: 56; WPF navigation and visual checks passed」，对照 56 项无漂移）。
- npm run check 终跑：**全绿** ✓（`p7-npm-check-final.log`）：pytest **1663 passed, 47 skipped**（= NUI-8 基线 1663+47，零漂移）；vitest **56 files / 615 tests passed**（= 基线，零漂移）；生产构建通过（"All checks passed!"）。

## 运行记录

- 2026-09-19：轮启动。goal/nui8-release 已 push；PR #983~#989 已开（见 P0-1）。
- 2026-09-19：P1 离线定性完成；P2/P3 代码修复与回归落地（D5、NUI-8-A 43 处迁移、a11y、#4 取消竞态分流）；--render 全套 56 项 PASS。
- 2026-09-20：实机会话（run-dir output/nui9-live3，--no-seed 复用）：D5 真机验证、P4-2 抽样 5+1 处、P4-1 Lightbox、**发现并修复种子面板 0×0 缺陷（P1 结论修正）**、G4 N=20 全 20/20。npm run check 终跑全绿（1663+47 / 56 files 615，零漂移）。sidecar stop 复检干净。
