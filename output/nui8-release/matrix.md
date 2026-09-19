# NUI-8 WPF 桌面发布收口台账

本轮分支：`goal/nui8-release`（基线 `origin/master` = `601a3e38` Merge PR #980）。
收口时点口径变更：本台账连同 `tools/`、`perf/`、`logs/`、`defect4/` 文本证据
**入仓**（离屏 `render-*/` 不入库，见文末 PR 拆分一节），可长期追溯的结论另落
`docs/native-ui-migration.md`。
状态口径：通过 / 修复后通过 / 失败待修 / BLOCKED+原因 / AUTH-REQ / NOT RUN+原因。

## P0 PR 落地与基线

| 格 | 内容 | 状态 | 证据 |
| --- | --- | --- | --- |
| P0-1 | #980 缺陷修复 | 通过（已合并） | merge SHA `601a3e38`，check+linux-pytest SUCCESS |
| P0-1 | #981 验收工具 | 通过（已合并） | merge SHA `8dcf27e3`，SUCCESS |
| P0-1 | #982 结果文档 | 通过（已合并） | merge SHA `2226e23c`，SUCCESS |
| P0-2 | 原生全套 Release 构建 | 通过 | `dotnet build -c Release` 0 error（native + native-tests） |
| P0-2 | 原生全套运行（`--render`）基线 | 通过 | `native-suite-07-final.log:118` `Native client checks passed: 56; WPF navigation and visual checks passed`；离屏图样 81 项在 `render-nui8/`。01~06 为迭代轮（含 `native-suite-06-d2-negative.log` 的负例轮），全部保留 |
| P0-2 | `npm run check` 基线 | 通过（全绿） | `logs/p0-npm-check.log`（后台任务 exit 0）：Ruff/lint `All checks passed!`；Pytest **1663 passed, 47 skipped** in 648.89s；Vitest **56 files / 615 passed**（68.64s）；`next build` Compiled successfully + 静态页 7/7。日志 251-256 行的 `NativeCommandError` 是 jsdom「Not implemented: navigation to another Document」写到 stderr 被 PowerShell 包装，对应测试文件本身通过，非门禁失败 |

存量 PR 全部落地，P0-1 完成。合并序按建议 980→981→982，lead 已介入并全部合入。

## P1-1 缺陷 #4：工作流保存报 "An error occurred while sending the request"

### 受控复现器（已建，进 native-tests）

`apps/desktop/native-tests/NativeKeepAliveChecks.cs`，两种仪器、三相负载：

- `--keepalive`（离线，已接入默认全套运行路径）：进程内 TCP 服务器
  按固定空闲窗口（120ms）主动回收 keep-alive 连接，精确复刻 uvicorn
  的 `timeout_keep_alive` 行为；走**生产 `ApiClient`**（不注入 fake handler）。
  三相：burst（快速连续 PATCH）／idle-gap-save（跨回收窗口再 PATCH）／
  idle-gap-read（同窗口但可重放 GET 作对照）。
- `--keepalive-live --origin=...`（实机）：对真实 API 重放验收负载
  —— 60 次快速「读+保存」+ 6 次跨 6.5s 空闲的保存 + 6 次跨空闲读 +
  8 轮 ×（40 并发 GET ‖ 1 次保存）的并发压力相。异常链逐条落盘 JSON。

### 实测结果（失败样本保留，未剔除）

| 仪器 | 相位 | 结果 | 证据文件 |
| --- | --- | --- | --- |
| 离线（生产 ApiClient + uvicorn 式空闲回收） | burst 20 | 20 ok / 0 failed | `defect4/keepalive-offline-20260919-172942.json` |
| 离线 | idle-gap-save 20 | 20 ok / 0 failed | 同上（服务端 accept 41 socket，客户端未见半关闭） |
| 离线 | idle-gap-read 20 | 20 ok / 0 failed | 同上 |
| 实机（隔离 sidecar 实例，端口 8000） | burst 60 | 60 ok / 0 failed | `defect4/keepalive-live-20260919-174035.json` |
| 实机 | idle-gap-save 6 / idle-gap-read 6 | 6 ok / 6 ok，0 failed | 同上 |
| 实机 | 并发 320 GET ‖ 8 save | 320 ok / 8 ok，0 failed | `defect4/keepalive-live-20260919-174313.json` |

### 结论（诚实区分）

**任务书假设「HttpClient 连接池复用被服务端回收的坏连接」已被实测否证。**
.NET 8 的 `SocketsHttpHandler` 对服务端先关闭的池化连接能感知并另起连接，
即使是**不可重放 body 的 PATCH** 也没有失败；真实 sidecar 上 400 个请求
（串行 + 并发，含跨 keep-alive 空闲窗口）零失败。

因此 `PooledConnectionLifetime` 一类修复**没有可证的对症依据**，不做。
附带核对：`ImageStore.LoadAsync`（Ui.cs:410-425）响应与内容均正确释放，
`ApiClient.SaveDownloadAsync`（ApiClient.cs:138）`using` 正确——无连接钉死泄漏。

缺陷 #4 当前状态：**失败待修（未复现）**。已排除：连接池空闲回收、
媒体下载响应泄漏、并发 GET 挤占。下一步候选（尚未验证，不据此勾选）：
1. 只有真机 WPF 进程才会触发、我的复现器未覆盖的形态——autosave 去抖
   800ms 与 3s 轮询在同一 `ApiClient` 上交叠，且画布切换会 `CancellationToken`
   作废在途请求（`lifetime.Token`）；需要确认「失败」是否其实是取消被误报。
2. 验收当时 sidecar 是否发生过重启/端口重绑（`MainWindow.xaml.cs:86` 的
   `api = new ApiClient(origin)` 若可重入，旧实例未 Dispose 会遗留池）。
3. 证据缺口：`run-p2/logs/wpf-client.log` 为 **0 字节**，两张截图
   `native-workflow-save-{failed,retry}.png` 大小同为 125887 字节，
   疑为同一帧——即该缺陷**没有留下异常文本证据**。复测须先补客户端异常日志落盘。

### 已交付：传输层诊断（让下一次真机发生时可定因）

上一轮该缺陷完全无法定因的直接原因是**客户端零日志**：`wpf-client.log` 0 字节，
UI 只留一句 `An error occurred while sending the request`，异常链与目标 origin
全部丢失。已补 `ApiClient.ReportTransportFailure`：`SendAsync`/`UploadAsync`
捕获 `HttpRequestException` 时向 stderr 落一行
`[mangaflow-api] PATCH http://127.0.0.1:PORT/projects 传输失败:
HttpRequestException <- SocketException:ConnectionRefused`，含方法、origin、
完整异常链与 socket 错误码。宿主已把 stderr 收进 `wpf-client.log`，因此下一轮
真机若复发，可直接区分「ConnectionRefused＝服务已重启/origin 失效」
「ConnectionReset/aborted＝中途断链」等分支。
回归：`--keepalive` 离线相对拒绝端口断言该行必须出现，已接入默认全套路径。

附带候选验证：`MainWindow.ConnectAsync` 每次重连都取新 origin 并 `api?.Dispose()`
后重建（MainWindow.xaml.cs:72-90），所以 origin 陈旧只在「sidecar 自行重启而
客户端未重连」时出现——这与「保存持续失败、harness 探活正常」完全吻合，
是当前第一顺位假设，但需靠上述诊断在真机取证，不据此勾选。

### 本轮已交付的可信产物

无论根因如何，`--keepalive` 离线相现在是**连接生命周期的回归针**：
若将来误改 handler 配置（如关闭池化、误设极短 lifetime、回退到不感知
关闭的实现），默认全套会立刻变红。已接入默认路径，无需专用 flag。

## P1-2 P3 差异家族（8 项）

提交 `1bfa255c`（修复 D1/D3/D6/D7 + 离屏回归）。口径先说清：本轮 D1/D3/D6/D7
是「源码 + 离屏渲染 + 交互回归」三级证据，**真机截图仍未补**（下一格统一登记）；
D4 是「以 web 源码论证原判定不成立」，不改代码；D2 原以为同属此类，真机把它推翻（见该行）。

| 格 | 状态 | 证据 |
| --- | --- | --- |
| D1 hero 第二行 2px 朱红下划线 WPF 无 | 修复后通过（离屏实测 + 真机已见） | `HomeView.cs:72-95` 第二行拆成 Grid（文字 + `Height=2`、`Margin(0,-3,0,0)`、`RotateTransform(-1)` 的 Accent 线），Grid 宽度由文字决定，线只覆盖文字本身（web `em::after` 同语义）。离屏像素扫描 `output/nui8-release/render-02/native-dashboard-hero-1320.png`：y=187..191 出现一条 x∈[50,315]、每下一行左移约 57px 的斜带（≈1°），文字行本身在 y=147..181；回归断言在 `NativeNui8ParityChecks` 里钉死「线宽=文字宽（±1px）、左边缘对齐、位于文字下方」。真机：`run-live1` 首页 hero 第二行下方可见同一条短朱红斜线，只覆盖文字宽度（截图核对，非离屏） |
| D2「最近创作」右侧项目计数 WPF 无 | 修复后通过（离屏实测 + 负向对照 + 真机已见） | 上一轮「源码有绑定 ⇒ 不是差异」的判定在真机不成立：`run-live1` 实机截图像素与完整 UIA 树（未截断那次 `get_window_state`）都没有「N 个项目」文本，「最近创作」头部右侧只有一个空文本节点。`HomeView.cs:163-170` 确实绑了 `ProjectCount`（`StringFormat="{}{0} 个项目"`），`MainWindow.xaml.cs:144` 每次 `LoadDashboardAsync` 都发 `CountsChanged()`，所以缺陷在绑定/渲染层而非缺少代码；已在 `NativeNui8ParityChecks` 加离屏断言（要求 hero 头部存在文本以「个项目」结尾且等于「1 个项目」），用于判定是「离屏也丢」还是「只有真机丢」——该断言尚未跑（客户端占用 `MangaFlow.Native.exe`，构建被锁），定因（离屏复现，与真机同因）：`StringFormat` 写成 `"{}{0} 个项目"`——`{}` 只是 XAML markup 的转义前缀，从 C# 赋给 `Binding.StringFormat` 时原样进 `string.Format`，格式串非法 ⇒ 绑定静默回落成空串 ⇒ `TextBlock` 有节点、无文字，正是真机 UIA 里那个空文本节点。修：`HomeView.cs:168` 去掉 `{}`。回归在 `NativeNui8ParityChecks`：断言文本恰为「1 个项目」、`ActualWidth>20`（空串会是 0，挡住「有元素但看不见」）、右边缘落在头部右侧 40% 之外。负向对照：把 `{}` 改回去重跑，套件在 `native-suite-06-d2-negative.log` 里红 `NUI-8 D2: dashboard project count missing, found <none>`；改回正确写法后 `native-suite-07-final.log` exit 0、56 项全绿 （`native-build-07.log` 0 error）。真机复采已补（2026-09-19 `run-g3b` 开机）：仪表盘头部右侧读到「3 个项目」，同屏 hero 第二行朱红线也在场，截图 `output/nui8-release/run-g3b/shots/d2-dashboard-project-count-live.png` |
| D3 侧栏副标口径不一致 | 修复后通过（离屏实测 + 真机已见） | web 两条口径：卡片 `app/page.tsx:67`「N 章 · N 页 · N 已采用」，侧栏 `project-workspace.tsx:129-134`「N 章 · N 页已规划」，无章节回退「漫画生产工作区」。WPF 原先把卡片串直接绑侧栏（`MainWindow.xaml:85`）。现拆为 `ProjectItem.SidebarSummary`（`Models.cs`），侧栏绑它、卡片仍用 `Summary`；`page_count` 服务端就是该项目章节下 `MangaPage` 行数（`projects.py:229`），与 web 按章节求和同值且不依赖当前 section。回归：`NativeNui8ParityChecks` 断言两条串各自形态 + 空章节回退 + XAML 不再绑 `CurrentProject.Summary`。真机：侧栏副标读作「3 章 · 3 页已规划」，同项目卡片副标仍是「3 章 · 3 页 · 1 已采用」，两条口径同时在场即修复生效 |
| D4 项目卡点击落点与 web 不一致 | 通过（源码论证 + 真机已见落点） | web 卡片是 `<Link href={/projects/${id}/${item.next_action.section}}>`（`app/page.tsx:51`），WPF 卡片按 `NextSection` 导航（`Models.cs:109` 取同一 `next_action.section`），该字段由服务端算（`projects.py:231-259`）。两侧落点同源，「web 进默认页」的前提在 HEAD 不成立。真机：点项目卡落在服务端 `next_action.section` 指定的「生成素材库」，与源码判定一致 |
| D5 全局设置双头部观感 | 失败待修（本轮未改，附改动方案） | 差异成立且比记录更重：web 只有一个 `header.topbar.settings-topbar`（`apps/web/app/settings/page.tsx:126-127`，kicker+标题+三个动作同栏）；WPF 壳顶栏已有 kicker `SYSTEM / CONTROL ROOM`+标题「系统设置」（`MainWindow.xaml.cs:440-451`）**和同一组三个按钮**（`MainWindow.xaml:50-54`），页面又画一条含 kicker+标题+同样三个按钮的头部（`SettingsLayout.cs:12-20`）。不修的理由是风险而非「保持现状合理」：删页面头部要把 `runtimeSave` 的「保存中禁用」态（`SettingsView.cs:702/780`）迁到壳按钮（壳按钮当前只绑 `Connected`），属壳↔视图动作所有权重构，且本轮没有真机截图面可比对。方案：删 `SettingsLayout.cs:18-20` 的头部 Border，壳 `SettingsActions` 的保存按钮改绑视图 `runtimeSaving`，标题串统一为 web 的「系统设置与运行诊断」，配一条「设置页只有一条头部栏」的离屏回归 |
| D6 TXT 导入成功提示回显旧字段 | 修复后通过（交互回归）| 不只是提示回显：`EditChapter` 把所属章节标题写回 composing 的 `titleInput`（`SourceView.cs:261`），`ResetCompose` 不清它，于是下一次 TXT 导入把旧标题当作本次标题——既进 multipart 的 `title` 字段（`SourceView.cs:311`）也进成功提示，导入结果挂在从未导入过的章节名下。修：`DefaultComposeTitle` 常量 + `ResetCompose` 复位标题。回归在 `NativeSourceChecks.cs`：编辑「第二章」→保存修订→标题必须回到「第一章」→再走一次真实 `ImportFileAsync`，断 multipart `title` 带的是「第一章」而正文是本次文件内容 |
| D7 库导出下载失败无可见错误 | 修复后通过（代码+回归+真机已见） | 原判定「无可见错误」在 HEAD 已不成立（两处 catch 已写 `notice.Text`）。真实残留是文案语言：`HttpRequestException` 的 `An error occurred while sending the request` 原样进中文提示。修：`MediaErrors.Localize(error, 场景回退)`（`Ui.cs:325`）加可选回退，`LibraryView.cs:378/398` 导出/下载两条路径改走它；服务端 detail（`InvalidOperationException`/`TimeoutException`）仍原样透出。回归：`NativeNui8ParityChecks` 断两类异常的映射 + `LibraryView.cs` 源码必须恰好两处调用 Localize 且不再拼裸 `error.Message`。真机证据：P1-3 首跑 500 时素材库提示节点显示「下载失败，可重试：下载失败（500）」（中文 + 状态码，非英文异常原文），即本修复在真机生效；修复后重跑提示改为成功落盘路径 |
| D8 库滚动容器缺 AutomationProperties.Name | 修复后通过（代码 + 真机 UIA 已见 + G3 已用它驱动滚动） | 提交 `2e6a4fec`：`LibraryView.cs` 整页 `ScrollViewer` 补 `AutomationProperties.Name=「素材库滚动区」`，G3 滚动压力恢复可寻址。真机 UIA 已读到该节点：素材库页「52 窗格 素材库滚动区」，可被 UIA 寻址与滚动；G3 已用这个名字定位并驱动它跑了 20×N 样本（见 P3-G3）。注意：可寻址 ≠ 可驱动——驱动滚动时踩到采样器自身的 API 用错，见 P3-G3 第 2 轮 |

离屏全套复跑：`dotnet build -c Release` 0 error（`native-build-03.log`），
`MangaFlow.Native.Tests.exe --render` exit 0、`Native client checks passed: 56;
WPF navigation and visual checks passed`（`native-suite-02.log`），新增 PASS 行
「hero accent rule geometry, sidebar vs card summary split, localized
export/download failures」。


## P1-3 种子补齐（ExportBundle 真实 zip）

| 层 | 状态 | 实测证据 |
| --- | --- | --- |
| 种子写真实 zip + 真实 byte_size/sha256 | 通过（数据层实测） | 隔离目录 alembic upgrade head + `nui67_seed.py`：`exports/nui67-chapter1.zip` 落盘 903 字节；DB `byte_size=903` 与文件一致；`sha256` 与实际摘要一致；zip 可打开、`testzip()` 无坏块、含 `chapter1/page-001..003.png` 三个成员，`page_count=3` 与成员数一致 |
| 素材库「下载导出包」真机落盘 + 打开校验 | 修复后通过（真机实测 2026-09-19 20:05） | 隔离环境 `run-live1`（web=3000 api=8000 wpf_pid=15048，native 数据在 `run-live1/native-data`）真机点「↓ 下载」→ 系统「保存导出文件」对话框输入完整路径 + Return → 文件落盘 `run-live1/downloads/mangaflow-nui67-chapter1-retest.zip`：903 字节 = DB `byte_size`、`sha256(disk)` = DB `sha256`、`testzip()` 无坏块、成员 `chapter1/page-001..003.png` 且全部带 PNG magic、`page_count=3` 与成员数一致。逐字段核对见 `run-live1/downloads/p1-3-retest-verification.txt`，客户端画面见 `run-live1/shots/p1-3-export-download-retest.png` |
| 真机首跑失败：导出下载 HTTP 500（新发现，已定因并修） | 修复后通过（数据层 + 真机复测） | 首点「下载」客户端提示「下载失败，可重试：下载失败（500）」且目录不落盘（该提示本身即 D7 修复生效的证据）。定因：`nui67_seed.py` 写了产品枚举外的 `export_type="PNG_ZIP"`，而 `exports.py:397` 用 `media_types[bundle.export_type]`（只认 PNG/PDF/JSON）→ `KeyError` → 500；`schemas.py:1270` 的请求校验根本不允许该值，只有绕过 API 直插 DB 的脚本能造出来。修：种子改 `export_type="PNG"`，并加注释钉住这层耦合。回归 `tests/test_nui67_seed_export_download.py`：把种子灌进临时库后经真实 API 走下载，断 export_type ∈ 枚举、字节数/sha256 与文件一致、zip 可开且成员为 3 张 PNG。负向对照（改回 `PNG_ZIP` 重跑）两条用例均红，报错正是 `KeyError: 'PNG_ZIP'` at `exports.py:397`，日志留 `output/nui8-release/pytest-seed-export-02-negative-control.log`，修复后 `pytest-seed-export-01.log` 2 passed |

产品侧 `media_types` 查表未做兜底这一点如实记为边界：经 API 正常写入的行不可能带枚举外取值（请求校验挡住），本轮不为此加防御分支；能造出该状态的只有直插 DB 的脚本，因此修在脚本侧并让回归读真 API。

修复前该行 `storage_key` 指向的文件从未写过、`byte_size=1024` 与
`sha256="1"*64` 均为编造，因此导出下载路径整条无法验收。
ruff：改动前后 `scripts/nui67_seed.py` 在仓库配置下同为 24 条（均为既有 `draft_graph` 长行等），
新增 0 条；新回归文件 0 条。

## P2 逐页交互枚举 / P3 性能仪器化

P2 弹窗清单**先入台账再开测**，逐格实机触发状态见下表末列。

### P3-G3 长列表滚动压力（真机实测，失败轮全留）

| 轮 | 状态 | 实测证据与定因 |
| --- | --- | --- |
| 第 1 轮：静默测错页 | 失败样本保留（不作结论） | `run-g3b` 首跑 20 样本 `navigated=False`、`scrollPattern=False`、realized 节点恒 90 —— 采的是仪表盘。这类曲线与「健康的平坦内存曲线」长得一模一样，因此给采样器加了 UIA 导航 + 「找不到滚动目标就 throw」的硬 guard（`measure_library_memory.ps1`），并把该 CSV 留在 `perf/g3-library-memory-run-g3b.csv` |
| 第 2 轮：`scrollPattern=True` 但一次都没滚 | 失败样本保留（不作结论），根因已定 | 新加的 guard 报 `only 0/6 samples actually drove the scroller`。定因在**采样器自己**，不在客户端：PowerShell 里 `$sp.ScrollVerticalPercent = 100` 打的是 `ScrollPattern` 客户端对象上并不存在的属性（该属性属于 `ScrollPatternInformation`，只能经 `$sp.Current` 读），每样本静默抛异常被 catch 吞掉；`Scroll(ScrollAmount)` 在 UIAutomationClient 这一版也解析不到重载。两轮独立探针留档 `perf/g3-scrollpattern-probe.txt`（属性写法报错原文）与 `perf/g3-scrollpattern-probe2.txt`（`SetScrollPercent(-1,100)` 后 `vPct` 0→100→0，证明客户端可驱动）。因此 `perf/g3-library-memory-run-g3b*.csv` 三份全部是「静止页」样本，只作失败证据 |
| 第 3 轮：真滚动，N=20 双跑 | 通过（内存不随滚动增长）+ 一项明确边界 | 修成 `SetScrollPercent(-1, ±100)` 后：20/20 样本 `ScrollDriven=True`，`VerticalPercent` 在 0/100 之间交替（=10 次全程往返）。对照跑（`-NoNodeProbe`，只测内存不做 UIA 全树遍历）`perf/g3-scroll-control-run-g3c.csv`：WS 273.7→276.6 MB（+2.9 MB / +1.1%）、Private 192.1→195.0 MB（+2.9 MB）、Gen2 collections 全程 0。带全树遍历的探针跑 `perf/g3-scroll-probe-run-g3c.csv`：同页 WS +19.1 MB —— 差值即仪器自身每样本一次 `FindAll(Descendants)` 的负载，不能记到客户端头上，故两条分开记 |

G3 口径（不粉饰）：真机停在「06 生成素材库」，页头读数「150 个候选」。库里 600 条是 `nui67_seed_long_list.py` 写进 DB 的量（120 页 × 5），但 `library.py:227-232` 的 feed 是游标分页（`limit`/`next_cursor`），客户端首屏只拿到 150 条，所以本轮滚动压力实测的是 **150 张卡片的滚动**，不是 600；「600 候选一次滚完」仍属 NOT RUN。

虚拟化核验结论：**库页没有 UI 虚拟化**，内存不涨靠的是服务端分页而不是回收。`LibraryView.cs:214` 把候选卡直接 `tiles.Children.Add(...)` 进 `WrapPanel`，外层是普通 `ScrollViewer`；`Theme.xaml:617/666` 的 `VirtualizingPanel.IsVirtualizing` 只作用于 `ListBox`/`ListView`，库页不走这条路。证据形态与之吻合：10 次往返中 realized UIA 节点恒为 1423（不随滚动位置增减，也不是先增后降的回收曲线），即「一次性全部实例化、滚动只挪视口」。当前规模由游标页兜住；若把 `limit` 提到 600 或改成一次拉全，这条曲线会跟着涨 —— 记为后续项，不是本轮必修。

测量 caveat（不作为产品缺陷断言）：经 UIAutomationClient 读 `ScrollPattern.Current.Extent/Viewport` 恒为 0×0，而同句柄的 `VerticalScrollPercent` 正常在 0↔100 变化。未定因是 WPF 的 `ScrollViewerAutomationPeer` 还是 PowerShell 侧结构体封送，因此 G3 的「视口真的动了」这条证据改用 `VerticalPercent` 交替 + 20/20 `ScrollDriven` 硬断言，不用 extent。

顺带发现（新 a11y 缺陷，未修）：仪表盘项目卡在 UIA 里是 `DataItem`，`Name` 直接是 `ProjectItem { … }` 记录 ToString 转储，且 `invoke=False select=False` —— 屏幕阅读器读出的是内部记录文本，且 UIA 无法点开项目（G3 第 1 轮的导航因此只能靠 MCP 坐标点击 + F5 兜底）。修法：卡片 `AutomationProperties.Name` 给成人读串（如「打开 <项目名> · 3 章 · 11 页」）并让卡片本身可 Invoke；本轮未动，登记待修。

### P3-G1 严格首帧（呈现层仪器已建成，全样本保留）

仪器：`App.xaml.cs` 的 `StartFirstFrameProbe()` —— 仅当 `MANGAFLOW_FIRSTFRAME_OUT` 存在时挂钩 `CompositionTarget.Rendering`，首次回调写下「回调时刻 − 进程启动时刻」毫秒数（口径是合成器泵出第一帧，不是句柄可见、也不是 `WaitForInputIdle`）；不设该环境变量时产品路径零改动（提交 `1cc98531`）。采样复用 `measure-native-startup.ps1` 的启动/进程树清理/残留校验框架，新增 `-HotReuse`（冷=每样本新数据目录含完整后端 provisioning；热=共用已 provisioning 过的目录）与 `-OutCsv`。

| 轮 | 状态 | 实测 |
| --- | --- | --- |
| 第 1 轮（读帧时机错） | 失败样本保留 | `perf/g1-firstframe-*-round1-frame-read-too-early.csv`：冷 19/20、热仅 3/20 拿到首帧。定因在采样器 —— 帧文件是在 `WaitForInputIdle` 返回的那一刻读一次，而首帧回调可以晚于该刻，热启动几乎必然读空。CSV 与 `*-round1.log` 全留 |
| 第 2 轮（修好时机） | 通过（仪器化，不作达标判定） | 冷 N=20 全 20/20 有效：P50 1035.1 ms、P95 1067.2 ms、min 1016.0、max 2139.1（一个离群点保留不剔除）、均值 1092 ms。热 N=20 全 20/20：P50 1255.3 ms、P95 1321.9 ms、min 1043.1、max 1342.5、均值 1235 ms。原始 CSV `perf/g1-firstframe-cold-n20.csv`、`perf/g1-firstframe-hot-n20.csv` |

两轮对照差异如实记录、不挑好的报：第 1 轮冷 P50 719.7 ms vs 第 2 轮冷 1035.1 ms，同一构建、同一脚本只差读帧时机，说明本机首帧受后台负载（Qoder 会话 + 刚重建的 exe 被扫描）影响明显；且「热」比「冷」还慢，说明这个冷热口径**没有隔离出镜像/JIT 缓存效应** —— 两种模式都仍要拉起 native-host + python sidecar 整棵子进程树，首帧被后端 provisioning 主导。结论：G1 从此**可测**（不再是 BLOCKED），但在给出「首帧达标线」之前需要先定一条安静的测量窗口口径（独占机器、排除子进程树或把首帧定义收窄到壳窗口而非数据就绪）。



### P2-1 模态/弹窗/抽屉全枚举（源码盘点，13 个顶层页 + assets 5 个子视图）

窗口型模态（`Window` 子类，共 9 个 + 壳）：

| # | 模态 | 触发点 | Esc/关闭 | 焦点返还 | 确认+取消双支线 | 实机 |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | `ProjectPalette`（Ctrl+K） | `MainWindow.xaml.cs:654` | 通过（弹窗窗口句柄消失） | 通过（见下「M1 两轮口径」） | 选中/取消 | 修复前即通过 |
| M2 | `ConfirmDialog`（重试任务） | `MainWindow.xaml.cs:602` | 源码：`Ui.cs:500` Esc→`DialogResult=false`+Close；`Ui.cs:502` 默认焦点在「取消」 | 同左（安全默认） | 重试/取消 | NOT RUN（原因见下） |
| M3 | `InputDialog`（重命名参考页） | `StyleWorkspace.cs:194` | 源码：`Ui.cs:550` Esc→false，`Ui.cs:551` Enter→true | NOT RUN | 空值视为取消 | NOT RUN（原因见下） |
| M4 | `Lightbox`（图片查看） | `Ui.cs:679/763/775`，`LocalEditWindow.cs:309` 等 | 通过（Esc 关闭 + 提示语自证「Esc 或点击背景关闭」） | 通过（焦点回到触发缩略图按钮本体） | 单一关闭 | 通过 |
| M5 | `JobDetailsWindow`（调用与成本） | `JobsView.cs:202`（`JobDetailsWindow.cs:73` Esc→Close） | NOT RUN | NOT RUN | 只读 | NOT RUN（原因见下） |
| M6 | `LocalEditWindow`（局部修改） | `GenerateView.cs:950` | NOT RUN（:375 有「未提交即关闭」二次确认） | NOT RUN | 暂选 :323 / 弃用 :355；确认支线属付费 → AUTH-REQ | NOT RUN（原因见下） |
| M7 | `SceneEditor`（场景编辑） | `SceneWorkspace.cs:294/303`（`SceneEditor.cs:80` Esc→Close） | NOT RUN | NOT RUN | NOT RUN | NOT RUN（原因见下） |
| M8 | `PanelEditDialog` | `StoryboardView.cs:2155`（:2340 Esc→Close） | NOT RUN | NOT RUN | NOT RUN | NOT RUN（原因见下） |
| M9 | `LayoutRebuildDialog` | `StoryboardView.cs:2442`（:2503 Esc→Close） | NOT RUN | NOT RUN | NOT RUN | NOT RUN（原因见下） |

**M2/M3/M5~M9 七格 NOT RUN 的原因（不静默）**：本轮 P2-1 只完成了清单入台账 +
抽屉/M1/M4/MB1 四组实机（见下表）。剩余七格未触发的直接原因是同一轮里 harness
暴露了 DPI 坐标口径错误（见 P2-2 结论 1），已取得的实机观测被作废、需要重跑，
工时优先给了「把口径修对并留工具」而不是「用错口径再多跑几格」。
重跑入口已备好，不需再摸一遍：隔离栈 `scripts/nui67_side_by_side.py start` →
`apps/desktop/scripts/dump_uia_rects.ps1` 取目标窗口 UIA rect →
`apps/desktop/scripts/uia_rect_to_screenshot.py` 换算成 MCP 截图坐标 →
`get_window_state`/`capture_native_window_by_title.py` 取证 → 结束 `stop`+`status` 复核 `verify_clean`。
注意 M6 的确认支线与 M2 的重试都属付费动作，按硬性规则 3 只跑取消支线，确认支线记 AUTH-REQ。

#### P2-1 实机已跑格（run-p2c，客户端 pid 7496，主窗口 7407268）

取证工具补位：`capture_native_window.py` 的 `find_main_window` 只枚举无 owner 的顶层窗口，
带 `Owner` 的模态弹窗抓不到，故新增 `apps/desktop/scripts/capture_native_window_by_title.py`
（按标题子串选窗、不 restore 不移动，同一 PrintWindow 物理像素路径）。

| 格 | 操作序列（MCP `run_steps`，rust-sendinput/rust-uia 通道） | 观测 | 状态 |
| --- | --- | --- | --- |
| 抽屉 `HomeView.BuildDrawer` | 点「＋ 新建项目」→ 截图 → Esc | 打开后焦点自动进面板（`focused_element: 96 按钮 关闭创建面板`）；Esc 后面板节点消失、`focused_element: 18 按钮 ＋ 新建项目` | 通过 |
| 抽屉 backdrop 支线 | 重开面板 → 在左侧压暗区 (380,420) 单击 | 面板关闭且焦点同样回到「＋ 新建项目」；与 web `drawer-backdrop` 点击关闭一致。源码：`Ui.cs:597` `backdrop.MouseLeftButtonDown→Open=false`、`Ui.cs:641/661` `returnFocus` | 通过 |
| M1 弹窗（Ctrl+K 快捷键触发） | `ctrl+k` → 面板窗口 47188538 出现 → 对**面板自己的窗口** `escape` | 面板窗口随即 `target_not_found`（已销毁）⇒ Esc 关闭成立。焦点落在窗口根 `0 窗口 MangaFlow · 原生工作台` | 通过（关闭）|
| M1 两轮口径 | 再用鼠标点侧栏「搜索与切换 · Ctrl+K」打开（面板窗口 5179024）→ `escape` | 面板内焦点为 `5 编辑 搜索项目`；关闭后 `focused_element: 49 按钮 搜索与切换 · Ctrl+K` ⇒ 焦点返还触发控件成立 | 通过 |
| M1 结论修正（诚实记录） | — | 上一轮记的「Esc 未关闭 / 焦点未返还」是**测试口径错**：第一次 Escape 打在主窗口 7407268，而面板是独立顶层窗口；快捷键触发时打开前焦点本就在窗口根，关闭后回到窗口根不是缺陷。M1 不立缺陷 | 已更正 |
| M4 Lightbox | 库页点候选缩略图 → 独立最大化窗口 2098832「批次候选 2」→ `escape` | 窗口关闭；主窗口 `focused_element: 81 按钮 放大查看批次候选 2`，即弹窗自己提示语承诺的「关闭后回到原缩略图」 | 通过 |
| MB1 原生 MessageBox（任务中心「重试」） | 任务中心 → 失败任务卡「重试」→ 弹 `重试任务`（`是(Y)`/`否(N)`，独立顶层窗口 9701008） | 「否(N)」关闭后：任务计数不变（`2 个任务`、`失败任务 1 条`、`已失败` 原样），焦点回到触发按钮 `79 按钮 重试` ⇒ 取消支线无副作用 | 通过（取消支线）|
| MB1 确认支线 | — | 重试属付费动作，按硬性规则 3 不点「是(Y)」 | AUTH-REQ |
| MB1 Esc 层级（第 1 次，口径存疑） | 打开后对主窗口 7407268 发 `escape` | 对话框节点仍在树中；但此刻前台归属无法判定，不作为结论 | 不计 |
| MB1 Esc 层级（第 2 次，已消歧） | 重开 → `activate_window` 把 `重试任务` 置为 `isForeground:true`、焦点在 `1 按钮 是(Y)` → 对该窗口发 `escape`（`foregroundStatus:matched`） | 900ms 后同一窗口仍是 `对话框 重试任务 / 是(Y)(focused) / 否(N)`，**未关闭**；随后「否(N)」才关掉（窗口只剩 `0 窗格 (disabled)`） | 失败待修 |

新立缺陷 NUI-8-A（键盘层级 + 危险默认项）：`MessageBox.Show` 的 YesNo 确认框
（全仓 44 处确认点，含付费的「重试任务」）**Esc 不关闭**，且**默认焦点在 `是(Y)`**，
即 Enter 直接走产生费用/不可逆的分支。对照：仓库自研 `ConfirmDialog` 两条都做对了
——`Ui.cs:500` Esc→`DialogResult=false`，`Ui.cs:502` `Loaded → cancel.Focus()`。
建议（未在本轮实施，需逐点判定语义）：把付费/删除类确认从 `MessageBox.Show` 迁到
`ConfirmDialog`，或至少 `MessageBoxImage` + `MessageBoxResult` 显式指定默认按钮为否。
证据：`output/nui8-release/run-p2c/shots/p2-msgbox-retry-confirm.png`（488×222，
含「重试可能再次调用模型并产生费用」文案与 `是(Y)`/`否(N)`）。

证据：`output/nui8-release/run-p2c/shots/p2-drawer-open.png`（1650×1020 物理像素）、
`p2-lightbox-open.png`（1936×1096，含 −/100%/+/复位 与承诺文案）、
`p2-library-before-lightbox.png`。

内嵌抽屉：`HomeView.BuildDrawer()`（新建项目面板，`HomeView.cs:57`）——web 侧对应
`app/page.tsx:138-181` 的 `create-drawer` + `drawer-backdrop`（backdrop 点击关闭），
WPF 是否有等价的「点外部关闭 / Esc 关闭」为 P2 首查项。

`MessageBox.Show` 确认点按页归并（共 44 处，全部需走「取消不得产生副作用」支线）：
source 6（`SourceView.cs:249/284/396/414/445/483`）、assets/characters 6
（`CharacterAssetPage.cs:209/213`、`CharacterPackagePane.cs:176/428/445/466`）、
assets/outfits 3（`OutfitWorkspace.cs:273/391/392`）、assets/references 1
（`ReferenceWorkspace.cs:146`）、assets/scenes 2（`SceneWorkspace.cs:209/266`）、
assets/styles 3（`StyleWorkspace.cs:207/220`、`StyleProductionCard.cs:52`）、
script 6（`ScriptView.cs:373/420/430/445/504/931`）、storyboard 1
（`StoryboardView.cs:1130`）、generate 5（`GenerateView.cs:787/805/822/885/1516`）、
library 2（`LibraryView.cs:247/255`）、jobs 4（`JobsView.cs:220/242/308` + M2）、
workflow 12（`WorkflowView.cs:884/890/1689/1694/1725/1750/1794/1802/1827/1918/1964/2449`）、
settings/project 4（`ProjectSettingsView.cs:433/436/457/469`）、
settings/global 3（`SettingsView.cs:81/485/1507`）、壳 3（`App.xaml.cs:50`、
`MainWindow.xaml.cs:714/748`）。

这 44 处的取消支线**只实机抽到 1 处**（MB1 jobs「重试」，见上表），其余 43 处
**NOT RUN**：原因同 M2~M9（坐标口径返工吃掉工时），且 NUI-8-A（Esc 不关闭 +
默认焦点在 `是(Y)`）已由 MB1 一处证立并被源码确认为**全 `MessageBox.Show`
YesNo 通用行为**，不是单点缺陷 —— 因此剩余 43 处的实机价值在「逐点确认默认按钮
与文案」，而不是发现新机制，登记为 lead 可降级为「一次性源码普查 + 抽样实机」。

系统文件对话框（`OpenFileDialog`/`SaveFileDialog`，11 处）属 OS 模态，只登记不纳入
Esc/焦点断言。付费相关（升至 2K/4K、重试任务、局部修改生成）实机触发一律记
AUTH-REQ，不点确认支线的真实调用。

### P2-2 真实拖放 / P2-3 键盘可达性（隔离栈 `output/nui67-acceptance/run-p2c` 实机，含对照实验）

> 注：本轮沿用的隔离栈实际落在 **`output/nui67-acceptance/run-p2c`**（编排器 state 记录，
> wpf_pid 7496，web 3000 / api 8000），不是 `output/nui8-release/` 下同名目录。
> 所有实机几何/滚动证据写在该 run 目录内，未触碰用户真实 `storage/mangaflow.db`。
> 本轮结束时已 `nui67_side_by_side.py stop`，`verify_clean` 通过（无残留进程、3000/8000 已释放）。
> 期间曾出现 stop 未清干净（leftover `MangaFlow.Native.exe:7496`、`native-host.exe:35388`、
> 3000/8000 仍占用），已复核并关闭。

坐标基准由 `apps/desktop/scripts/dump_uia_rects.ps1`（PowerShell UIA 只读枚举，
收尾时从 `output/nui8-release/tools/` 提升到 `apps/desktop/scripts/` 长期维护）给出，
不靠肉眼估点。**本轮先修掉了 harness 自身的坐标口径错误（见结论 1）**，之前若干「拖拽不动」
的观测因此作废，下表按修正后口径重跑。
换算工具：`apps/desktop/scripts/uia_rect_to_screenshot.py`（把 UIA 物理 rect 按
`--scale/--origin/--window/--shot` 换成 MCP 截图坐标，已用「格 01」rect 校到
`center=543,529` 与实测一致）。

| 单元格 | 操作序列 | 观测 | 状态 |
| --- | --- | --- | --- |
| 工具校正：坐标点击 actuation | 修正后点「－」img(349,398)；再点「＋」img(436,398) | 缩放 61%→49%→61%，`focused_element` 依次落在 `81 按钮 －`、`84 按钮 ＋` | 通过（坐标点击可 actuate WPF 按钮） |
| **工具校正：注入式拖拽是否可达 WPF** | 拖画布竖向滚动条 thumb：img(871,524)→(871,662)，1200ms/150 步，`cursorMoveFrames:28` | thumb `x=1283 y=624` → `y=794`（中心 696→866），画布内容同步滚动：`格 01 y=642→244`、`格 02 677→279`、`格 03 711→313`、`💬 659→261` | **通过 —— 注入式 drag 的真实 move 流会被 WPF mouse-capture 识别** |
| P2-2 分镜面板拖拽（起点＝格 01 徽标中心，61% 缩放） | img(468,212)→(568,212)，1200ms/150 步，`cursorMoveFrames:37` | 状态条仍「已保存 · 当前 V1」、撤销 disabled | 失败待判（见结论 2） |
| P2-2 分镜面板拖拽（起点＝格 01 徽标中心，25% 缩放） | img(543,529)→(603,549)，1200ms/150 步，`cursorMoveFrames:20`，起点物理坐标 (857,702) 与 UIA 徽标 rect 中心逐字对齐 | 状态条仍「已保存 · 当前 V1」、撤销 disabled、导演台无 `PANEL` | 失败待判（见结论 2） |
| P2-2 早期三次拖拽（旧错误坐标） | img(611,639)→(837,620) 700ms、同点 1200ms/150 步、img(468,512)→(648,512)、img(493,507)→(593,507)（`cursorMoveFrames:0`） | 均无几何变化 | **作废**（漏乘 1.25 DPI；且最后一次 move 帧数为 0） |
| P2-2 对照组：GridSplitter（旧错误坐标） | img(1128,725)→(1034,725) | 分隔条与两侧 Pane 不变 | **作废**（该点物理坐标落在导演台内，未命中 8px 分隔条） |
| P2-2 第二注入通道 | `apps/desktop/scripts/measure_canvas_drag.ps1 -Samples 1`（`SetCursorPos`+`mouse_event`，硬编码点 885,735） | `FAIL_DIRTY_TIMEOUT`，样本保留 `perf/g4-canvas-drag-smoke.csv` | 未判定：脚本内坐标为物理像素且未随缩放刷新，需按结论 1 重算后复跑 |
| P2-2 键盘替代路径 | `click` 画布主体 → `Tab` → `Right` | 状态条翻「有未保存修改 · 当前 V1」、撤销由 disabled 变可用（nodeCount 127→170） | 通过（几何修改链路本身可用） |
| P2-2 撤销回退 | UIA `InvokePattern.Invoke` 点 element 98「撤销」 | 回到「已保存 · 当前 V1」、撤销重新 disabled；种子几何复原、未落库 | 通过 |
| P2-3 画布 Tab 序 | 焦点在画布主体时 `Tab` → 导演台显示 `PANEL 01`（格子切换生效）；焦点在外层容器 `69 窗格` 时 `Tab` → 焦点走到 `71 按钮 专注模式` | 「Tab 切换格子」仅在 `page` 子树持有焦点时成立：`StoryboardView.cs:101-102` 有意把 `PreviewKeyDown` 挂在 `page` 而非 View（#339，避免劫持检查器 TextBox 的 Tab） | 设计如此；提示文案隐含「先点画布」前提，建议补一句（非缺陷） |
| P2-3 鼠标点击选中格子 | 修正后点格 01 徽标中心 img(493,507) | 导演台仍显示占位文案「点击画布中的格子或气泡查看属性」，未出现 `PANEL` | 未判定：徽标 `anchor` 是 `IsHitTestVisible=false` 的覆盖层（`StoryboardView.cs:499`），点它应穿透到面板；需换面板内部点复测 |
| P2-2 参考图拖放上传、工作流节点拖拽＋连线 | 未跑 | — | NOT RUN —— 本轮预算用于定位并纠正坐标口径；口径修正后这两格与 G4 一起重跑 |

**结论与口径修正（硬性规则 1/2/6）**

1. **坐标口径缺陷（harness 侧，非产品）**：UIA `BoundingRectangle` 是**物理像素**，
   MCP 截图与 `click/drag` 参数是**逻辑像素 × 0.9394**（窗口 1320x816 → 截图 1240x768），
   本机显示缩放 125%。正确换算：`img = (phys / 1.25 − 窗口原点(108,0)) × 0.9394`。
   上一轮把物理坐标当逻辑坐标直接用，导致所有点击/拖拽打偏约 1.25 倍。
   校证：修正后「－」「＋」两次点击精确改变缩放并落到对应按钮的 UIA 焦点。
2. **撤回**上一版台账写的「注入式 move 不被 WPF 识别、连标准 GridSplitter 都不响应」——
   那条结论建立在打偏坐标上。修正坐标后，滚动条 thumb 被真实拖动（UIA 位置逐字段变化），
   说明 `rust-sendinput` 的 down→分步 move→up 序列对本 WPF 应用的 capture 型拖拽**可用**。
   在同一条已验证可用的通道上，分镜面板拖拽仍零反应（61% 与 25% 两档缩放、起点与 UIA 徽标
   rect 中心逐字对齐、`cursorMoveFrames` 20/37），且同点鼠标点击也不选中格子（导演台无 `PANEL`），
   而同一画布的键盘 `Tab`+`Right` 能选中并改脏 —— 三条证据合起来把范围收窄到
   **面板 `Border` 的鼠标命中路径**，不再是「测量通道不行」。候选根因（下一步二分）：
   徽标覆盖层 `anchor`（`StoryboardView.cs:499` `IsHitTestVisible=false`）与其绑定的
   Width/Height 是否把面板矩形撑到别处；`panel.Element` 的 `Background`
   （:1902 `ARGB 0x0A FFFFFF`，非 null 应可命中）是否被上层 `page` 的空白命中分支
   （:104-106 `e.OriginalSource is Canvas`）先吃掉。状态记 **失败待判**，
   需 lead 判定是否升级为产品缺陷（若真人在物理鼠标下也无法拖动，则为 P1 级缺陷）。
   **G4 分镜拖拽**：通道已证明可用，改为「等面板命中问题定性后按修正坐标跑 N=20」，
   不再引用旧口径「沙箱拦截 SendInput」。
3. 旧台账把「G4-工作流」记为 PASS，其实际口径是 `measure_workflow_layout.ps1:8-9` 用
   UIA Invoke 点「自动布局」，**不是真实拖拽**。自本起，任何 UIA Invoke 结果不再充当拖放证据。
4. `measure_canvas_drag.ps1` 的 `$panelX/$panelY` 是写死的物理像素且不含 DPI 换算，
   复跑 G4 前必须改为「UIA 取 rect → 按结论 1 换算 → 传入」，否则会持续产出伪 `FAIL_DIRTY_TIMEOUT`。

证据：`tools/uia-rects-storyboard.txt`（61% 基线）、`tools/uia-rects-p002-49.txt`（49%）、
`tools/uia-rects-scrollbar-after.txt` + `tools/scrollbar-compare.txt`（thumb 624→794 对照）、
`tools/rect2img.txt`（换算结果）、`perf/g4-canvas-drag-smoke.csv`（失败样本，不剔除）。


## P4-1 WPF 客户端安装器（已产出可分发 exe）

| 项 | 状态 | 实测证据 |
| --- | --- | --- |
| NSIS 脚本 + staging 组装 + 打包 | 通过 | `apps/desktop/installer/mangaflow-native.nsi` + `apps/desktop/scripts/build-native-installer.ps1`；staging 146 MB（`client\` + `apps\api` + `apps\desktop\sidecar` + `.venv-desktop`），5 项 payload 探针全在；makensis 3.10 zlib/SOLID 打包 exit 0 |
| 产物 | 通过 | `output/desktop-installer/MangaFlow-Native-0.9.0-x64.exe`，44 106 425 字节（42.1 MB，压缩比 31.5%），sha256 `3B2B24703011960D854888A8612DCD8BA7BDF5AD9C7756FB61CCFE0DC127D160` |
| 零改生产代码即可从安装目录启动 | 通过（代码层论证） | 安装根目录按 `App.xaml.cs:41-47` 的「向上找 `apps\api\alembic.ini`」契约摆放，`client\` 为其下级；`NativeBackend` 由此定位 sidecar 与 `.venv-desktop`，无需 `MANGAFLOW_NATIVE_REPO` |

打包过程踩到并已固化的两条构建约束（都曾真实失败，日志留档
`output/desktop-installer/build-0{1,2}.log`）：

1. `makensis` 把无 BOM 的 .nsi 按系统 ACP 解码 → `Bad text encoding ... on line 1`。
   .nsi 已改存 UTF-8 **带 BOM**，构建脚本增加 BOM 前置校验并给出可行动错误信息。
2. 可选 `resources\` payload 用 `File /r` 且目录不存在 → `no files found` 直接中止。
   构建脚本本就不产出该目录，故删掉这段死代码而非加 `/nonfatal`。

NSIS 本体为仓库内便携版 `tools/nsis-3.10/`（用户授权「装到仓库内便携版」），
连同下载包一并 `.gitignore`，不入库。

## P4-2 安装 / 升级 / 卸载独立验收

验收脚本 `apps/desktop/scripts/verify-native-installer.ps1`（沿用旧壳 W-12 配方：
用户数据 sha256 快照 → `/S /D=` 静默装 → 校验 → 同目录再装 → 校验 →
`unins000.exe /S` 静默卸 → 校验）。安装目录与用户数据目录都取仓库内隔离路径，
`-NoShortcuts` 构建故不写真实桌面；启动探测把 `MANGAFLOW_DESKTOP_USER_DATA`
显式指向隔离目录，**未触碰** `%LOCALAPPDATA%\MangaFlow\Native` 与
`%LOCALAPPDATA%\com.mangaflow.desktop`。

| 单元格 | 状态 | 实测证据（`verify-sema2/installer-verify.json`、`verify-launch3/installer-verify.json`） |
| --- | --- | --- |
| 静默全新安装→目录布局完整 | 通过 | 6 项探针全在：`client\MangaFlow.Native.exe`、`client\native-host.exe`、`apps\api\alembic.ini`、`apps\desktop\sidecar\mangaflow_desktop_helper.py`、`.venv-desktop\Scripts\python.exe`、`unins000.exe`；`install_layout_missing = []` |
| 全新安装→启动可用 | 通过（真机，安装目录起服务） | 直接双击式启动 `install\client\MangaFlow.Native.exe`，不注入任何 `MANGAFLOW_*` 仓库变量：40s 内进程未退出，且隔离目录里出现 shell 日志 `spawn → ready_verified(pid=3460, port=2410) → go_sent → healthy → stopped(exit_code=0)`，起服务 5s、健康 7s |
| 覆盖升级→用户数据逐字节保留 | 通过 | 预置 `window.json`(19B) 与 `storage\seed.bin`(64B)，安装后与升级后两轮 `Compare-Snapshot` 均为 `{}`（零增删改）；`upgrade_layout_missing = []` |
| 静默卸载→安装目录清除 | 通过 | 4 项残留探针 `client/apps/.venv-desktop/unins000.exe` 全空，`$install` 目录本身消失 |
| 静默卸载→用户数据完好 | 通过 | 卸载后快照与初始快照的差异 = 启动那一次客户端合法写下的 4 个新增文件（`data\mangaflow.db`、`logs\helper-*.stderr.log`、`logs\shell-*.log`、`runtime\mangaflow-desktop-<token>\owner.json`），**无任何 `changed:` / `missing:`** —— 预置两文件逐字节仍在 |
| 升级后版本号正确 | 通过 | `HKCU\...\Uninstall\MangaFlow.Native` 的 `DisplayVersion = 0.9.0` 与 `-Version 0.9.0` 一致；卸载后该键消失（`uninstall_registry_removed = true`） |
| 升级后首启行为 | 通过 | 首启在指定用户数据目录下新建 `data\mangaflow.db` + `runtime\...\owner.json` + 两份日志，未覆写已存在的 `window.json`；实例互斥句柄按用户数据目录取键（#410）因此不受影响 |
| 「安装/升级不得写用户数据」的强度说明 | 记边界 | 不启动客户端时，安装与升级两轮对用户数据目录是**严格零写入**；一旦真启动，客户端必然写它，所以带 `-Launch` 的那一轮把卸载后的零漂移断言让位给「除客户端自身新增外无差异」，脚本用 `user_data_drift_after_launch` 单列，避免把启动成功误报成失败 |

四轮真实失败轮全部留档，不剔除：

1. `verify-h2h4-01.log` —— `Wait-Quiet` 把 `WScript.Shell.Run` 的返回当 Process 用，
   `[System.Int32] 没有 WaitForExit`，在 [1/4] 就崩（安装那一步其实已经成功）。
2. `verify-sema-02.log` —— 固定 `Start-Sleep 2s` 等卸载：`unins000.exe /S` 会把自身
   复制进 `%TEMP%` 后立刻退出，`Run` 的退出码只覆盖第一跳，于是误报「残留
   `.venv-desktop`、`unins000.exe` + 注册表未删」。事后核查目录与注册表都已消失，
   改成轮询到安装目录与卸载键双双消失（上限 300s）。
3. `verify-launch-04.log` —— 加 `-Launch` 后，紧随 Kill 的用户数据快照撞上 sidecar
   尚未释放的 SQLite 句柄，`Get-FileHash` 在 `ErrorActionPreference=Stop` 下把整轮打死。
   现记成 `LOCKED` 不抛异常；该轮留下的 shell 日志（
   `launch-evidence-run1/`，`ready_verified(pid=37984,port=2097) → healthy →
   stopped exit_code=0`）反过来成了「启动可用」的第一份证据。
4. `verify-launch2-05.log` + `verify-launch2-failed-race/` —— 卸载后只剩
   `client\native-host.exe` 一个文件：客户端被 Kill 的瞬间 host 自身镜像句柄未释放，
   而 NSIS `RMDir /r` 不重试。补「等到能独占打开 `native-host.exe` 再卸载」后转绿。
   附带一条正向结论：**强杀 WPF 客户端后 `native-host.exe` 会经 stdin 关闭自检并以
   `stopped exit_code=0` 自行收尾**，不是孤儿进程。

同一轮里 `launch_probe` 一度报「sidecar 缺事件 stopped」也是同一个竞态：日志读到得太早。

## P4-3 代码签名尝试（H6 从 BLOCKED 变成有实测的边界）

工具链实测：`signtool.exe` **存在**（`C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe`，不在 PATH 上）；
`makecert.exe` 不在 PATH 上（只验过 PATH，未全盘搜 SDK）→ 改用 `New-SelfSignedCertificate` 造证书。

| 项 | 状态 | 实测证据 |
| --- | --- | --- |
| 造自签名代码签名证书 | 通过 | `New-SelfSignedCertificate -Type CodeSigningCert -HashAlgorithm SHA256 -KeyLength 2048` 落在 `Cert:\CurrentUser\My`，`CN=MangaFlow Test Code Signing`，thumbprint `FE01BEB9691125E4571B88DC84E6974CBAD712A7`，有效期至 2027-09-19 |
| 签名安装器 | 通过 | `signtool sign /fd SHA256 /sha1 FE01…` → `Successfully signed`，exit 0；签名内记录的文件摘要 `5D8A662C…69AD` |
| 签名链校验 | 失败待修（**预期失败，非缺陷**） | `signtool verify /pa` exit 1：`A certificate chain processed, but terminated in a root certificate which is not trusted by the trust provider`；`Get-AuthenticodeSignature.Status = UnknownError`（同一句中文原文）。自签名证书不在受信任根里，这是数学上必然的结果，不能靠换工具修 |
| 产物摘要随签名改变 | 已记 | 未签名 `3B2B2470…D160` → 签名后 `25E7415D…7A25`。台账与任何分发清单必须按「签名态/未签名态」分别记 sha256 |
| 时间戳 | NOT RUN + 原因 | 未加 `/tr`：打时间戳要出网访问第三方 RFC3161 服务，本轮按「不产生对外请求」口径跳过；正式发布必须补，否则证书过期后签名即失效（`File is not timestamped` 已在 verify 输出里） |
| SmartScreen 提示行为 | NOT RUN + 原因 | 判定依赖 Mark-of-the-Web + 浏览器下载路径 + 云端信誉，本机无法在不伪造下载来源的前提下测得；按硬规则不勾。可断言的只有：自签名不受信任 → 结论至多是「仍会拦」，不写实测 |
| 构建流水线接入 | 通过（代码） | `build-native-installer.ps1` 新增 `-SignThumbprint` / `-SignTool`，签名后自动重报 sha256 并跑一次 `verify /pa`，让 lead 能原样复现上面所有结论 |

留待 lead 处置：证书 `FE01BEB9691125E4571B88DC84E6974CBAD712A7` 仍留在本机
`CurrentUser\My`，私钥可导出；正式发版前应删除，且**不要**把它导入受信任根。

## P4-4 .NET LTS 决策：本轮结论「留在 .NET 8」

本机事实（实测，`dotnet --list-sdks` / `--list-runtimes`）：

- 只装了 SDK `8.0.425`；`Microsoft.WindowsDesktop.App` 只有 `8.0.31`。
  没有任何 .NET 10 SDK/运行时可用 —— 升级的第一步就得装新 SDK，超出本轮授权范围。
- WPF 侧恰好 2 个工程，TFM 全为 `net8.0-windows`
  （`apps/desktop/native/MangaFlow.Native.csproj:4`、
  `apps/desktop/native-tests/MangaFlow.Native.Tests.csproj:2`），
  两者都**没有任何 `PackageReference`**，第三方 NuGet 面为零。
- 仓库根**无 `global.json`**，csproj 内也无 `RollForward` /
  `RuntimeFrameworkVersion` 固定 —— SDK 解析走「取机器上最新已安装版本」。

建议结论（供 lead 拍板，本轮不实施升级）：**发布版继续留在 .NET 8，另开一个
独立的升级 PR**，理由三条：

1. 本轮安装器 payload 是**框架依赖**摆放（`client\` 只有 42 MB 安装包、
   依赖机器上的 8.0.31 桌面运行时）。换 net10 要么让验收机与用户机都装
   10.x 桌面运行时，要么改自包含发布 —— 两者都会改变 P4-1/P4-2 刚量到的
   安装器体积与卸载契约，不能和缺陷修复混在同一个 PR 里。
2. 运行时大版本会作废本轮与上一轮的 G1/G2/G4/G5 性能基线（AGENTS.md 要求
   这类变更独立可评审），而 NUI-8 剩余范围是缺陷清零与验收证据，不是运行时迁移。
3. 无 `global.json` 是**当下就该补的小风险**，与升不升级无关：日后任何机器装上
   10.x SDK，同一份源码的构建结果会静默漂移。建议单独一条小 PR 固定 SDK 版本。

留 8 的代价必须如实记下：`.NET 8` LTS 支持到 **2026-11-12**，距本轮只剩约 8 周，
到期后不再有安全补丁。因此升级 PR 不是「可选优化」，而是 EOL 前的必做项，
其验收门禁至少等于：`dotnet build -c Release` + 原生全套 + G1/G2/G4/G5 复基线 +
P4-2 全部六格复跑。

## P4-5 版本信息与更新检查

| 项 | 状态 | 依据 |
| --- | --- | --- |
| 应用内「检查更新」 | 设计如此（产品未实现该设计） | 全量检索 `apps/desktop/native/`，命中的「版本」全是内容语义（工作流草稿版本、角色包派生版本、保存冲突同步），无任何 latest-version/release 拉取代码。收尾时顺带修掉一处过期陈述：`apps/desktop/native/README.md:17` 原文写「原生客户端尚非可独立分发的安装包」，与 P4-1 已产出安装器矛盾，已改为指向 `build-native-installer.ps1` / `verify-native-installer.ps1` 与 .NET 8 EOL 边界 |
| 安装器版本号来源 | 通过（已实测，指向 P4-2） | 见 P4-2「升级后版本号」格：安装器 `-Version 0.9.0` 与 `HKCU\...\Uninstall\MangaFlow.Native\DisplayVersion` 一致。边界如实记下：csproj 仍是 `<Version>0.3.0</Version>`，与安装器版本是**两个独立来源**，本轮未统一（统一属产品版本策略决定，需 lead 定发布号后再改），故分发时以安装器 `-Version` 为准 |

## 运行记录与环境边界

- 隔离环境：`scripts/nui67_side_by_side.py start` 起的 web+API+WPF 三件套，
  数据库与存储全在 run-d4 目录内；**未触碰** `storage/mangaflow.db` 与
  `%LOCALAPPDATA%\com.mangaflow.desktop`。收工 `stop` 返回
  `session stopped; issues: none`（端口与进程表干净）。
- 收工复核（2026-09-19 收尾时点）：`nui67_side_by_side.py status` → `no session recorded`；
  进程表无 `MangaFlow.Native` / `native-host`；3000/8000 已释放。本轮曾出现
  `stop` 未清干净仍在该栈上继续测试的失误，已在 P2-2 一节如实登记。
- 离屏渲染产物的目录漂移（harness 侧，已处置）：以不同 cwd 跑
  `MangaFlow.Native.Tests.exe --render output/nui8-release/render-03` 时，
  相对路径落到 `apps/desktop/output/…`，与 `output/nui8-release/render-03` 成了两份。
  收尾时把只存在于漂移目录的 3 份 `keepalive-offline-20260919-20{1040,1218,1417}.json`
  移入 `defect4/`（不剔除失败样本），再删除 `apps/desktop/output/`。
  根因与 `start --run-dir` 同源：脚本按当前工作目录解析相对路径，未钉仓库根。
- harness 已知缺陷（本轮发现，未修）：`start --run-dir output/nui8-release/run-d4`
  会把相对路径拼到默认父目录下，实际落到
  `output/nui67-acceptance/output/nui8-release/run-d4`。仍是隔离路径、不影响
  本轮结论，但传参语义与帮助文字不一致，后续应改为相对仓库根解析。
- 端口独占：P1-1 实机跑期间未并行任何其他重套件；`npm run check` 在
  sidecar 停止后才启动。
- 本地产物不入库（`.gitignore` 既有规则）：`output/desktop-installer/`（42 MB 安装器
  + 146 MB staging + `tools/nsis-3.10/`）。安装器 sha256 已写在 P4-1，需要复现时按
  `build-native-installer.ps1` 重打。
- 本机残留待删：自签名证书 `FE01BEB9691125E4571B88DC84E6974CBAD712A7` 仍在
  `Cert:\CurrentUser\My`（私钥可导出，从未导入受信任根）。正式发版前应
  `Remove-Item Cert:\CurrentUser\My\FE01…`，本收尾未擅自删除，交由用户处置。

## P5 收口：逐格状态汇总与剩余工作

### 已闭环（有实测或明确论证）

| 阶段 | 格数 | 状态构成 |
| --- | --- | --- |
| P0 基线 | 6 | 全通过（含 `npm run check` 1663+615 实测计数、原生全套 56 项） |
| P1-2 差异家族 | 8 | 修复后通过 6（D1/D2/D3/D6/D7/D8）、通过（论证）1（D4）、失败待修 1（D5） |
| P1-3 种子 | 3 | 通过 1 + 修复后通过 2（含真机导出落盘逐字段校验） |
| P2-1 模态（已跑部分） | 5 | 通过 4（抽屉 / backdrop / M1 / M4）+ 取消支线通过 1（MB1），另 1 AUTH-REQ、1 失败待修（NUI-8-A） |
| P2-2/3（已跑部分） | 6 | 通过 4（坐标 actuation、注入拖拽可达、键盘替代、撤销）+ 设计如此 1（画布 Tab 序前提）+ 失败待判 1（面板鼠标命中） |
| P3 性能 | 2 | G1 通过（仪器化，不作达标判定）、G3 通过（150 卡口径 + 无虚拟化边界） |
| P4 发布 | 19 | P4-1 通过 3、P4-2 通过 8、P4-3 通过 3 / 预期失败 1 / 已记 1 / NOT RUN 2、P4-4 结论「留 8」、P4-5 通过 2 |

### 剩余工作（按优先级，均为可独立续跑的格子）

1. **P1 缺陷 #4**：失败待修（未复现）。已用 400 请求实机负载否证「连接池空闲回收」
   假设；下一步靠本轮新增的 `ApiClient.ReportTransportFailure` 在真机取证
   （上一轮该缺陷零日志是它无法定因的直接原因）。
2. **P2-2 面板鼠标命中**：失败待判 —— 通道已被证明可用（滚动条 thumb 实测被拖动），
   面板拖拽与同点点击仍无反应，而键盘链路能选中并改脏。源码二分已做完一轮：
   `BeginPanelDrag`（`StoryboardView.cs:519-564`）先 `SelectPanel` 再
   `element.CaptureMouse()`，`SelectPanel`→`SetSelected`/`RenderResizeHandles`/
   `RenderInspector` 均不把 `panel.Element` 摘出视觉树（`SetSelected` 只改
   BorderBrush/Thickness，:1940-1946），因此「元素被重建导致捕获失败」这一候选
   **已排除**。剩余候选需运行时证据才能判：`CaptureMouse()` 返回值、
   `IsMouseCaptured`、以及 move 期是否真进了 `moved` 回调 —— 下一轮应在
   native-tests 里用 `RaiseEvent(MouseLeftButtonDown/Move/Up)` 合成同一条手势
   （仓库已有此形态：`NativeAssetsLoopChecks.cs:333` `ClickVisual`、
   `NativeStoryboardEditChecks.cs:617` `Press`）做判别：离线若同样不动，
   则是产品缺陷且能直接钉回归；离线能动则仍是注入通道差异。此项**必须定性**
   后才能补 G4 N=20。
3. **P2-1 七格模态 + 43 处 MessageBox 取消支线**：NOT RUN，复入口已写在上文。
4. **P2-2 参考图拖放上传 / 工作流节点拖拽＋连线**：NOT RUN。
5. **P2-3 逐页 Tab 序横扫**：NOT RUN（本轮只跑了分镜画布一页，其余 12 页未过）。
6. **P3-G4 分镜拖拽 N=20**：NOT RUN，阻塞于第 2 项定性。
7. **D5 全局设置双头部**：失败待修，改动方案已写在 P1-2 该行。
8. **NUI-8-A**：`MessageBox.Show` YesNo 确认框 Esc 不关闭 + 默认焦点在 `是(Y)`
   （付费/不可逆分支），失败待修。
9. **新发现 a11y 缺陷（未修）**：仪表盘项目卡在 UIA 里 `Name` 是
   `ProjectItem { … }` 记录转储且 `invoke=False select=False`，屏幕阅读器读出内部
   文本、UIA 无法点开（P3-G3 那节）。

### 本分支提交与 PR 拆分建议（AGENTS.md「各自独立、互不混杂」）

分支 `goal/nui8-release` 相对 `origin/master` 的提交按主题天然分组如下（收尾两条：
`5b32e44b` 验收工具与台账、`32af7f89` 文档），不必重切历史：

| 提交 | 主题 | 建议 PR |
| --- | --- | --- |
| `c9cb1401` + `2e6a4fec` | 缺陷 #4 受控复现器与连接生命周期回归、传输层诊断落盘、库滚动容器 a11y 命名 | PR-A 缺陷与可诊断性 |
| `1bfa255c` + `addfb23a` | 原生/web 差异修复（D1/D3/D6/D7 + D2 `StringFormat`）+ 离屏回归 | PR-B 原生差异修复 |
| `14264b72` + `2a9b923a` | 种子写真实 zip、`export_type` 越枚举修正 + 经真 API 的回归 | PR-C 种子与数据 |
| `4419acfe` | NSIS 安装器 + 静默装/升级/卸载验证 + 签名开关 | PR-D 安装发布 |
| `be95e549` + `1cc98531` + `2c9ff443` | G3 采样器自守卫与真滚动、G1 呈现层首帧仪器、读帧时机 | PR-E 性能仪器 |
| 本次收尾新增 | `uia_rect_to_screenshot.py`、`capture_native_window_by_title.py`、`dump_uia_rects.ps1`、台账与文本证据 | PR-F 验收工具与台账 |
| 待提 | `docs/native-ui-migration.md` + `apps/desktop/native/README.md` | PR-G 文档 |

台账本身（`output/nui8-release/matrix.md`）与 `tools/`、`perf/`、`logs/`、
`defect4/` 文本证据（合计约 380 KB）随 PR-F 一起入仓；`render-*/` 四目录共 332 份
离屏 PNG（约 26.7 MB）属可重跑产物，不入库（`MangaFlow.Native.Tests.exe --render`
一条命令可再生），台账内引用其路径处均已把关键读数抄进正文，评审不依赖本地图。
