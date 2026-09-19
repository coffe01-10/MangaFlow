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
| P1 面板鼠标命中定性（离线合成手势） | 失败待判 → 见 P1-DET | 待跑 |

## P2 待修缺陷

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P2-1 D5 全局设置双头部 | NOT RUN → 设计先行 | 待设计稿 |
| P2-2 NUI-8-A MessageBox YesNo（Esc/默认焦点）+ 43 处普查迁移 | NOT RUN | 待普查表 |
| P2-3 仪表盘项目卡 a11y（语义 Name + Invoke/Select + UIA 断言） | NOT RUN | 待跑 |

## P3 缺陷 #4 有界时间盒

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P3 合成假设①（autosave 去抖 × 3s 轮询 × 切画布取消） | NOT RUN | 待跑 |

## P4 逐页交互格子

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P4-1 七格窗口模态 M2/M3/M5~M9（Esc/焦点返还/双支线） | NOT RUN | 不依赖 P2 修复，可先行 |
| P4-2 43 处 MessageBox 取消支线（ConfirmDialog ≥5 实机 + 纯信息 2 处） | NOT RUN | 依赖 P2-2 完成 |
| P4-3 参考图拖放上传 + 工作流节点拖拽连线 | NOT RUN | 待跑 |
| P4-4 逐页 Tab 序横扫（除分镜画布外 12 页） | NOT RUN | 待跑 |
| P4-5 焦点环逐控件族抽查 | NOT RUN | 上轮仅 1 页 |

## P5 性能补全

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P5-1 G4 分镜拖拽 N=20 + measure_canvas_drag.ps1 UIA 坐标改造 | NOT RUN | 依赖 P1 定性 |
| P5-2 G1 安静测量窗口口径（AUTH-REQ：请 lead 确认） | AUTH-REQ | 口径草案待写进本台账，lead 确认后才跑 N=20 |
| P5-3 G2 长列表 100/300/500 滚动+内存 | NOT RUN | 500 档若炸登记虚拟化建议，不改 UI |

## P6 运行时升级与版本统一

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P6-1 global.json 钉 SDK 8.0.425（独立小 PR，最先合） | NOT RUN | 待开 PR |
| P6-2 .NET 8→net10 迁移（门禁：Release 0 error + 原生全套 + G1/G2/G4/G5 复基线 + 安装六格） | NOT RUN | 独立 PR；L3 lead 设计先行 |
| P6-3 版本统一（csproj 0.3.0 vs 安装器 0.9.0） | AUTH-REQ | lead 定发布号（建议 1.0.0-rc1）前只登记不动手 |

## P7 收口

| 格 | 状态 | 证据 |
| --- | --- | --- |
| P7-1 逐格汇总与剩余优先级 | NOT RUN | 收尾时填 |
| P7-2 NUI-8 父项勾选判定 | NOT RUN | 以本轮实测为据 |
| P7-3 本轮新增提交拆 PR（基线 A~G 合入后的 master） | NOT RUN | 收尾时拆 |
| P7-4 收工复核（sidecar stop / 进程与端口 / npm run check 终跑 / dotnet build + --render） | NOT RUN | 收尾时跑 |

## 运行记录

- 2026-09-19：轮启动。goal/nui8-release 已 push；PR #983~#989 已开（见 P0-1）。
