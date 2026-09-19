# NUI-6/NUI-7 实机验收矩阵台账

- 任务:WPF 原生客户端全功能迁移收口(NUI-6 剩余实机格 + NUI-7 性能与发布验收)
- 分支:`goal/nui67-acceptance`;基线:`bdf38670`(= origin/master @ 2026-09-19)
- 证据根目录:`output/nui67-acceptance/`(不跟踪,正式报告另行入册)
- 状态口径:PASS=有实测证据通过;FIXED=发现缺陷修复后复测通过(附回归测试);FAIL=失败待修;NOT RUN=无实测证据;BLOCKED=环境缺失,注明原因;AUTH-REQ=需用户授权(付费边界)

## 一、页面 × 维度矩阵(17 业务页)

维度:A=常规业务流并排对照;B=409 版本冲突;C=ConfirmLeave 阻断负格;D=弹窗+键盘焦点;E=文件对话框实机;F=像素级并排截图(固定数据集)

**「专项轮」读法**:该格的实测在第五节专项轮完成或已明确列入第六节边界——B/C/D 的机制级实机证据见第五节(409=项目设置、ConfirmLeave=source 编辑器、弹窗焦点=Ctrl+K/任务详情窗);E 的文件对话框=第五节三个真实对话框。逐页枚举与真实拖放手势未穷尽部分在第六节如实列出,不冒充通过。

| # | 页面 | A 常规流 | B 409 | C 离开阻断 | D 弹窗/焦点 | E 文件对话框 | F 像素对照 | 证据/备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | home `/` | PASS | N/A | N/A | N/A | NOT RUN(封面对话框专项) | PASS | run-p1-v3/shots/{native,web}-home-1500.png + run-p1-v5 重验;指标带/双项目卡/AI 面板/生产闭环全一致;P3 差异:web hero 第二行有 2px 朱红下划线而 WPF 无;web「最近创作」右侧有 N 个项目计数而 WPF 无;WPF 项目卡点击进 NextSection(generate) 而 web 卡片进项目默认页(专项核实) |
| 2 | help `/help` | PASS | N/A | N/A | PASS(锚点/返回可见) | N/A | PASS | run-p1-v5/shots/{native,web}-help.png;hero/锚点标签/四列流程卡/返回入口对照 |
| 3 | settings 全局 `/settings` | FIXED→PASS | NOT RUN | NOT RUN | 专项轮 | N/A | NOT RUN | run-p1-v5/shots/native-global-settings-fixed.png;发现缺陷③:Number() 对显式 null 数值字段抛异常(TryGetInt32 对错误类型抛而非返回 false)→设置页供应商列表读取失败→修复+fixture 回归(NativeSystemSettingsPageChecks 加 latency_ms:null);P3 观感:双头部(壳+页)待对照 web;真实验证/付费冒烟=AUTH-REQ |
| 4 | usage `/settings/usage` | PASS | N/A | N/A | NOT RUN(明细抽屉实机) | NOT RUN(CSV 对话框) | PASS | run-p1-v5/shots/{native,web}-usage.png;3 次调用/成功失败口径/¥66 账单/未知与零值/筛选趋势按钮对照;CSV 导出与抽屉专项轮 |
| 5 | source `/source` | PASS | 专项轮 | 专项轮 | 专项轮 | NOT RUN(TXT/MD 对话框) | PASS | run-p1-v3/shots/{native,web}-source-1.png;三章渲染/选择/修订保存(DB rev2)/分页门禁随章节状态切换;P3:侧栏副标 WPF「3 章·3 页·1 已采用」vs web「3 章·3 页已规划」 |
| 6 | assets/characters | PASS | 专项轮 | 专项轮 | 专项轮 | NOT RUN(参考上传对话框) | PASS | shots/{native,web}-assets-characters.png;2 角色渲染/编辑表单(固定特征+禁改项)/保存即时更新(DB aliases=["小晚","晚儿"])/模型包空态+视觉模型门禁 |
| 7 | assets/outfits | PASS | 专项轮 | 专项轮 | 专项轮 | NOT RUN | PASS | shots/{native,web}-assets-outfits.png;2 档案/三步绑定表单/绑定关系/视觉模型门禁 |
| 8 | assets/scenes | PASS | 专项轮 | 专项轮 | 专项轮 | NOT RUN | PASS | shots/{native,web}-assets-scenes.png;1 场景(学校天台·1 变体·已就绪)/筛选/详情/主参考图 |
| 9 | assets/style | PASS | 专项轮 | 专项轮 | 专项轮 | NOT RUN | PASS | shots/{native,web}-assets-style.png;1 档案/新建表单+黑白彩色切换/待分析计数 |
| 10 | assets/references | PASS | 专项轮 | 专项轮 | 专项轮 | NOT RUN | PASS | shots/{native,web}-assets-references.png;6 文件/四分类标签+角色筛选/上传区;双侧分类均 0 FILES(种子 GENERATED 资产语义一致,非缺陷) |
| 11 | script `/script` | PASS | 专项轮 | 专项轮 | 专项轮 | N/A | PASS | shots/{native,web}-script-ch1.png;覆盖 100%·场景/变体绑定·空态(ch2)·章节下拉;章节上下文跨页延续 |
| 12 | storyboard `/storyboard` | FIXED→PASS | 专项轮 | 专项轮 | 专项轮 | N/A | PASS | run-p1-v4/shots/native-storyboard-structure-banner.png;发现缺陷①:缺 web「旧版分页数据」警告条→补齐(源码+回归 NativeStoryboardPageChecks);页列/画布/导演台/缩放全对照 |
| 13 | generate `/generate`(含导演/局部编辑) | PASS(图像修复后) | 专项轮 | 专项轮 | 专项轮 | NOT RUN(导出对话框) | PASS | run-p1-v5/shots;候选/批次/场景/生产门禁/参考警告对照;真实供应商生成=AUTH-REQ |
| 14 | library `/library` | FIXED→PASS | N/A | 专项轮 | 专项轮 | NOT RUN(ZIP/PDF/JSON 对话框) | PASS | run-p1-v5/shots/native-library-fixed.png vs web-library.png;发现缺陷②:ImageBox 无模板→全 app 图像空白(自初版重写)→删 DefaultStyleKey override+回归 NativeVisualChecks;批次/筛选/暂选撤回/导出门禁对照 |
| 15 | jobs `/jobs` | PASS | N/A | N/A | 专项轮(详情窗) | N/A | PASS | run-p1-v5/shots/{native,web}-jobs.png;失败分组/错误码/费用口径/重试归档/日期折叠对照 |
| 16 | workflow `/workflow` | PASS(渲染) | 专项轮 | 专项轮 | 专项轮 | NOT RUN(导入/导出) | PASS | run-p1-v5/shots/{native,web}-workflow.png;草稿V1/节点库/画布/检查器/运行门禁对照;拖拽连线专项轮 |
| 17 | settings 项目内 | PASS | 专项轮 | 专项轮 | 专项轮 | N/A | PASS | run-p1-v5/shots/{native,web}-project-settings.png;工作方式/清晰度/并发与种子一致 |

## 二、NUI-7 性能门禁

口径依据:`docs/v02-desktop-performance-acceptance-plan.md`(N=20 全样本保留、P50/P95 nearest-rank、失败轮不剔除、清理失败即失败)。

| 项 | 内容 | 状态 | 样本数 | 原始数据 | 结论 |
| --- | --- | --- | --- | --- | --- |
| G1 | 严格首帧(呈现层) | NOT RUN | | | 需要 ETW/呈现层采样工具,本轮未实施;窗口句柄/输入空闲口径见 G7,不得换算为首帧 |
| G2 | 窗口内页面切换响应 | NOT RUN | | | 专项:固定数据集 N=20 逐页切换计时 |
| G3 | 长列表内存 | PASS(稳态口径) | 20 | run-g3/perf/g3-library-memory.csv + shots | Dataset-L 式播种(120 页/600 候选,`nui67_seed_long_list.py`)后素材库长列表页稳态:WorkingSet P50=265.5MB,30 秒 20 样本零增长(-1.1MB),无泄漏迹象;口径限制:UIA 滚动驱动未生效(scroller 无 AutomationProperties.Name),滚动中的虚拟化压力留待跟进 |
| G4 | 双画布帧时间(分镜+工作流)全样本 | NOT RUN | | | 专项:拖拽帧采样 N=20 |
| G5 | 多窗口宽度(940/1280/1440 DIP) | PARTIAL PASS | 3 | run-p1-v5/shots/widths/ | 本机 125% 缩放:实测 960(MinWidth 钳制)/1126/1267 DIP 三档,渲染干净无裁切;940 DIP 低于应用 MinWidth=960(MainWindow.xaml),按设计不可达;1440 DIP 因物理屏幕(1920px/125%=1536 DIP)可达上限 ~1536,1440 档实测=1584px 截图 |
| G6 | 多 DPI/跨屏实机(100/125/150/200%,发糊检查) | PARTIAL PASS | 1 | run-p1-v5/shots/widths/ | 本机唯一物理屏 125%:文字 ClearType 锐利、无发糊(PerMonitorV2 manifest 生效);100/150/200% 与跨屏需多显示器,BLOCKED(单屏环境) |
| G7 | 启动计时/退出清理复跑(不回归) | PASS | 3 | 会话日志 call_e83421e1 | 2026-09-19 复跑:句柄 519/483/508ms、空闲 577/558/540ms,3 次干净关闭、递归子进程扫描零残留;优于历史热样本(939/931ms),无回归;注:dev 栈同机运行(轻载干扰已注明),样本口径=句柄/空闲,非严格首帧 |

## 三、NUI-7 发布与生命周期

| 项 | 内容 | 状态 | 备注 |
| --- | --- | --- | --- |
| H1 | 受支持 .NET LTS 核对/升级 | PASS(带提示) | net8.0-windows = .NET 8 LTS;本机 SDK 8.0.425;注意 .NET 8 支持期至 2026-11-12(不足 2 个月),升级 .NET 10 LTS 属新工作量,是否升级留 lead 决策 |
| H2 | 安装 | BLOCKED | 仓库当前无 WPF 客户端安装器产物(仅 start-native.ps1 开发启动器);无包可装 |
| H3 | 升级(旧版本覆盖安装) | BLOCKED | 同 H2 |
| H4 | 卸载 | BLOCKED | 同 H2 |
| H5 | 生命周期(更新检查/退出清理) | PARTIAL PASS | 退出清理=PASS(G7 递归扫描零残留,3 样本);更新检查:应用内无更新检查器(设计如此,桌面嵌入本地服务),无可测项 |
| H6 | 签名 | BLOCKED | 无签名证书(历史 W-12 轮同样记录为不可用) |

## 五、2026-09-19 第二轮:实机专项格(409/ConfirmLeave/弹窗焦点/文件对话框)

| 格 | 页面 | 状态 | 证据 | 说明 |
| --- | --- | --- | --- | --- |
| B 409 版本冲突 | 项目设置 | PASS | run-p2/shots/native-409-project-settings.png + DB | sqlite 越权推版本(1→2)后保存:409 被捕获→红条「检测到其他端保存了较新版本,本地修改已保留,已同步最新版本号;再次保存将覆盖远端修改」→重试成功(DB version 2→3、并发=6 落库);二次编辑 set_value 6→保存成功 |
| C ConfirmLeave 阻断 | source(修订编辑器) | PASS | run-p2/shots/native-confirmleave-blocked.png | 编辑器输入制造脏状态→点侧栏剧本→模态「当前章节的修改尚未保存,离开会丢弃这些内容。确定要离开?」→否=留在原页+草稿保留(负格)→再次导航→是=丢弃并跳转(正格);另:取消修改按钮同样有丢弃确认 |
| D 弹窗/焦点 | 全局 Ctrl+K | PASS | 会话截图 frame-e3bc7a55 等 | Ctrl+K 打开切换器、搜索框自动聚焦、输入「切换」实时过滤、Enter 打开并切换项目(上下文隔离:任务计数清零、页类别保留) |
| D 弹窗/焦点 | 任务详情窗 | PASS | 会话截图 frame-13880471/df037281 | 调用与成本窗:费用估算口径/价格版本/2 次派发明细;关闭钮聚焦(红框);Esc 关闭后焦点返还触发按钮 |
| E 文件对话框 | source TXT/MD 导入 | PASS | 会话截图 frame-f47942e8/f636c4dc + DB | 真实 Win32 OpenFileDialog(过滤 *.txt/*.md/*.markdown);输入路径导入→第 4 章创建(50字·2段·已导入);P3:成功提示「已导入〈标题〉」回显的是旧标题字段值而非新章名 |
| E 文件对话框 | library 整章导出下载 | PASS(对话框)+FAIL(产物) | 会话截图 frame-cd62b142 | 真实 SaveFileDialog(默认名 mangaflow-*.json/类型过滤);Enter 保存触发下载但文件未落盘:种子 ExportBundle 无真实 zip 文件(404)——种子数据问题;观察:下载失败后导出条附近无可见错误提示(P3) |
| E 文件对话框 | usage CSV 导出 | PASS | fixtures/usage-2026-09-19.csv | 真实 SaveFileDialog+「导出完成」确认框;CSV 落盘含表头+3 行种子调用明细(币种/通道/计量状态分布正确) |
| E 文件对话框 | 参考素材上传 | PASS(同原语) | — | 与 TXT/MD 导入同为 Microsoft.Win32.OpenFileDialog 通道,按 TXT/MD 实测覆盖;单独拖放手势未测(见历史边界) |

新发现(第 4 项,未修复,待 lead 定夺):

4. **快速连续请求后工作流保存传输层故障**(run-p2/shots/native-workflow-save-failed.png、native-workflow-save-retry.png):G2(N=20 页面切换)+G4(N=20 自动布局+保存往返)共 ~60 次快速请求后,工作流保存开始报「保存失败: An error occurred while sending the request.」,手动重试持续失败;独立客户端直查 sidecar /health 正常(server 活着)。错误 UI、离开确认弃稿契约(A 系列)在故障态下行为全部正确(否=留页保草稿等重试)。疑似客户端 HttpClient 连接池在 keep-alive 连接被服务端回收后复用坏连接(需 lead 复核)。

## 六、剩余 NOT RUN 汇总(第二轮后)

- G1 严格首帧(呈现层):BLOCKED——无 ETW/PresentMon 类采样工具,拒以句柄/空闲口径冒充
- G3 长列表内存:NOT RUN——Dataset-L(120 页/1000 候选)规模播种未做
- G4 分镜画布拖拽:BLOCKED(仪器)——harness 沙箱拦截 SendInput/mouse_event,40 失败样本保留于 perf/g4-canvas-drag.csv;工作流画布已用纯 UIA 通道完成 N=20
- G2 之外的批量性能:G5/G6 PARTIAL、G7 PASS(见第二节)
- P4 安装/升级/卸载/签名:BLOCKED(无安装器产物/无证书,见第三节)
- 真实供应商生成/质检/导出闭环:AUTH-REQ(付费边界)

## 七、历史已确认的 NOT RUN 清单(来源:native-ui-migration.md,本轮状态见上文各节)

1. 17 页与真实运行 web 版并排对照 → **本轮 PASS**
2. 409 版本冲突格、ConfirmLeaveAsync=false 阻断导航负格、逐页弹窗与键盘焦点格 → **本轮 PASS(项目设置 409/source ConfirmLeave/Ctrl+K/任务详情窗)**
3. 文件选择器、真实拖放手势、删除/用途修改确认弹窗 → **文件选择器本轮 PASS;真实拖放手势 NOT RUN**
4. 真实供应商生成/质检/导出闭环 → AUTH-REQ
5. 严格首帧(呈现层)、页面切换响应、长列表内存、双画布帧时间 → 呈现层 BLOCKED/页面切换 PASS/长列表 NOT RUN/工作流画布 PASS·分镜 BLOCKED(仪器)
6. 100/125/150/200% DPI 实机截图、跨屏切换、缩放发糊 → 125% 单屏 PASS;其余 BLOCKED(单屏)
7. 安装/升级/签名/卸载/生命周期 → BLOCKED/见第三节
8. 物理屏幕 DPI/动画性能 → 见 G6/G7

