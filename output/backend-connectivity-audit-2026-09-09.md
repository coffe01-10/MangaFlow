# WPF 已还原页面后端连通性审计

审计日期：2026-09-09。仓库：`D:\自媒体\漫画工作流`。
审计基线：`6c54bb0d6a12abe89c122dd10e14e17678ad7218`。

本轮仅检查，没有修复业务代码。范围为原作、人物设定、角色模型包、服装档案及其公共调用链；不代表全站迁移验收，也不包含字体清晰度和动画性能验收。

## 结论

基础后端能够启动，核心数据接口能够真实保存和读取，不能说这些页面全是摆设。但界面到完整业务结果的链路仍有缺口，不能认定全部功能已完成，更不能认定达到商业级验收标准。

### 已实际验证

- Rust 宿主源码：`cargo check --offline --manifest-path apps/desktop/shell-core/Cargo.toml --bin native-host` 通过。此命令检查源码，不重新部署宿主可执行文件。
- WPF：`dotnet build apps/desktop/native/MangaFlow.Native.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly` 通过；本次输出 0 个错误、13 个警告。
- Python：`.venv-desktop/Scripts/python.exe -m pip check` 通过，没有损坏的依赖关系。
- 使用 WPF 输出目录内实际优先加载的 `native-host.exe`，启动真实 sidecar/API，使用独立用户目录与 SQLite 数据库，并由启动链路执行迁移。
- 32 次真实 HTTP 检查均得到预期结果：健康检查、模型目录、项目创建、原作粘贴/文件导入、修订写入与读取、人物创建/修改、真实 PNG 上传、人物参考图绑定、角色包规格/封面/正面参考、服装创建/修改/读取、素材库响应、版本服装绑定/默认服装、发布/派生/删除新草稿。
- 模型目录返回 6 项，但目录可读取不等于供应商凭据可用。
- 宿主正常退出，隔离测试目录及测试数据库已清除，没有修改用户业务数据。

### 明确未验证

本次连通性检查显式设置 `QUEUE_ENABLED=false`，没有提交生成任务、调用真实图片供应商或产生生图费用。因此生成任务执行、真实供应商权限、完成后的 UI 自动刷新、取消/重试及生成后采纳，均不属于本轮实测通过项。

32 次 HTTP 检查是直接调用真实 API，不是逐个点击 WPF 控件的端到端验收。已有原生页面测试使用 `HttpMessageHandler` fixture，它们有价值，但不能替代真实 API 和供应商验收。

原生后端使用私有动态端口；常规 8000/3000 没有服务不能作为原生后端未连通的证据。任务服务还支持本地执行路径，不能仅凭未启动独立 RQ Worker 判定无法生成。

## 问题与证据

### 1. P1：人物参考图/概念图预览存在确定的 URL 转换错误

状态：调用链代码确认，并通过当前编译 DLL 复现异常；没有以真实 WPF 鼠标点击验证。

- `apps/desktop/native/Views/CharacterAssetPage.cs:160` 将 `OriginFor(asset.ContentUrl)` 传给 `ShowImage`。
- `apps/desktop/native/Views/AssetsView.cs:516` 概念图使用同样调用方式。
- `AssetsView.cs:164-165`：`OriginFor` 调用 `Api.OriginUrl`，`ShowImage` 转交 `OpenImage`。
- `apps/desktop/native/Views/ViewKit.cs:121-124`：`OpenImage` 再次调用 `OriginUrl`。
- `apps/desktop/native/Services/ApiClient.cs:171-186`：路径校验拒绝绝对 URL。

DLL 实测：`OriginUrl("/api/v1/assets/a1/content")` 返回合法本地绝对地址；再次将该地址传入 `OriginUrl` 抛出“无效的 API 路径”。因此上述点击链路不能正常打开预览，并存在事件异常未处理风险。服装页部分调用传的是原始路径，不应笼统认定所有图片入口均坏。

### 2. P1：人物概念生成缺少完成后刷新链路

状态：代码确认；真实生成执行未运行。

`AssetsView.cs` 的 `ConceptPanel` 在构造时及提交生成请求后各调用一次 `LoadCandidatesAsync`（约 463、475-494 行）。`AssetsView.PollTick`（194 行）仅处理服装与风格，没有向人物概念面板转发轮询，也未见该面板自身的完成事件订阅或手动刷新入口。

异步任务在首次读取后才完成时，候选不会因此自动刷新，文案承诺与代码不一致。生成/采纳操作还需检查重复提交保护；候选读取没有明确筛选概念生成类型，其他批次可能混入概念采纳列表。后两项需要修复 AI 根据真实服务端契约进一步验收，不应仅凭推测修改后端。

### 3. P2：角色包发布后缺少冻结规格只读展示

状态：网页与 WPF 代码对照确认，未实机截图验收。

- `CharacterPackagePane.cs:164-175` 无草稿时仅提示版本冻结并渲染参考矩阵，没有展示该版本的 `spec_snapshot`。
- 网页 `apps/web/components/project-workspace/character-package-workspace.tsx:671` 明确渲染 `FrozenSpecReadout`。

后端发布/派生本轮真实 HTTP 检查通过；此处是客户端没有完整呈现已保存的身份、视觉与负面约束。不要误报为后端丢数据。

### 4. P2：服装未保存编辑存在切页丢失路径；人物异步操作隔离不足

状态：静态代码确认存在风险路径，未进行实际 WPF 操作复现。

- `AssetsView.Switch`（76 行）直接 `Render`；`Render`（142 行）清空容器并新建子面板。
- `OutfitWorkspace` 的输入/参考选择保存在面板实例中；构造时依据 `SelectedOutfit` 重新 `BeginEdit`，后者（124 行）用原服装对象重新填表。
- `AssetsView` 未实现离开确认；`ViewKit.cs:68` 默认允许直接离开。切到其他资产标签再返回可能无提示丢失未保存修改。
- 内部标签切换不增加 `epoch`；部分人物/角色包异步操作只核对父视图 `epoch`。旧子面板请求返回后仍可能通知或触发父页面重新加载，应通过延迟响应测试进一步确认具体影响。

注意服装面板已经有 `OwnsOutfits(this)` 检查，不要将其现有保护说成完全不存在，也不要用全页周期性重建来解决生成刷新问题。

### 5. P2：宿主构建产物与客户端实际加载文件不一致

状态：文件时间、大小、SHA-256 及启动代码确认。旧宿主本轮实际启动成功，本项不等于已发生启动故障。

`Services/NativeBackend.cs:19-20` 优先使用 WPF 输出目录内的宿主，只有缺失才回退到 Rust debug 目录。

| 文件 | 大小 | SHA-256 |
| --- | ---: | --- |
| `apps/desktop/shell-core/target/debug/native-host.exe` | 1014272 | `843D42DACDF7EB22E73C8461FC512EFF4D14EFD3108DD921DCEC75667BA4D162` |
| `apps/desktop/native/bin/Release/net8.0-windows/native-host.exe` | 999936 | `97B3B3E28AD976E43D72F996B42301368E471E237E6175A6833FC4666520DF2F` |

前者时间为 2026-09-08，后者为 2026-09-07。`apps/desktop/scripts/start-native.ps1` 已包含 cargo build、dotnet build 和 Copy-Item；单独 dotnet build 不执行宿主同步。因此不能用 WPF 编译成功证明实际运行的是刚验证的宿主源码。不同哈希仅证明产物不同，本轮未证明具体源码版本差异或运行回归。

## 可复制给其他 AI 的提示词

建议逐项交付和验收。下面公共要求与对应任务一起复制；这些是后续执行指令，不代表本轮已经修复。

### 公共要求

```text
你将在 D:\自媒体\漫画工作流 修复 WPF 原生客户端已迁移页面。先读 AGENTS.md 和当前 git 状态。审计基线是 6c54bb0d6a12abe89c122dd10e14e17678ad7218；若当前源码已变化，重新核实问题，不覆盖其他人的修改。

阅读 output/backend-connectivity-audit-2026-09-09.md。只处理本次指定问题，不顺手重做其他页面。原网页的布局和业务语义是依据，保持原生 WPF，不用 WebView 替代，不引入椭圆按钮。不要用模拟数据或恒定成功提示冒充业务接通。

不得读取/输出密钥，不操作用户已有项目或数据库。真实 API 验收使用独立用户目录、独立数据库和本次启动的宿主；只清理确认属于本次验证的资源，临时文件用完立即清除。默认使用内置浏览器核查网页；WPF 用适用的原生验证方式。

为行为变更补针对性回归，区分静态检查、模拟 HTTP、真实 API、真实供应商四类证据。不要把构建成功等同于功能验收。缺少供应商权限或其他基础设施时明确 NOT RUN/BLOCKED，不能伪造通过，也不要擅自发起付费生图；先说明最小验收调用及所需条件。

交付具体修改、测试命令/结果、真实 UI 操作证据、未验证项。不要声称全部页面或商业级体验已验收。
```

### 任务一：修复图片预览异常（优先）

```text
修复人物参考图和概念图打开预览时 URL 被转换两次的问题。

重点检查 CharacterAssetPage.cs:160、AssetsView.cs:164-165/516/1387、ViewKit.cs:121-124、Services/ApiClient.cs:171-186，并搜索所有 ShowImage/OpenImage 调用。当前 OriginUrl 只接受相对 API 路径，调用者先生成绝对 URL 后，OpenImage 再次转换必然抛异常；审计已通过编译 DLL 复现“无效的 API 路径”。

统一媒体地址在调用链中的约定，保证只转换一次。保留现有本地后端地址与路径校验边界，不要简单允许任意外部地址来掩盖问题。必要时给点击事件提供可见的错误反馈。

验收：用隔离真实 API 上传一张 PNG，从真实 WPF 人物参考图和概念候选入口打开/关闭预览；覆盖不同合法相对路径、空路径和非法地址，确认服装预览没有回归。补能捕获原始双重转换错误的回归测试，不要只验证新帮助函数。
```

### 任务二：补齐人物概念生成与采纳闭环

```text
修复 AssetsView.cs 的 ConceptPanel。现在生成仅 POST complete-sheet 后立刻读一次候选，而 AssetsView.PollTick 不刷新人物概念面板，异步完成不会自动展示。

先核对真实后端 job/batch/candidate 状态和 generation_kind 契约，再实现提交中防重复、排队/执行/失败/完成展示、完成后增量刷新与采纳后更新人物参考/服装。只展示适用于概念采纳的候选，不能混用其他生成类型；不要改后端状态语义来迁就 UI。

刷新必须属于当前项目、当前角色、当前面板。切换后取消无用读取或忽略迟到响应，不得重建正在编辑的整页，不丢用户输入。检查采纳按钮重复点击保护与失败反馈。

验收覆盖：请求被延迟、生成耗时超过首次候选读取、失败、重入页面、快速切换角色/项目、连续点击生成/采纳；检查是否产生重复任务和串页。用确定性延迟测试覆盖 UI 状态，再分别报告真实 API 和真实供应商验证；未进行真实生图时明确写出，不能称全链路通过。
```

### 任务三：恢复已发布角色包规格展示

```text
对照网页 character-package-workspace.tsx 的 FrozenSpecReadout，补全 CharacterPackagePane.cs 无草稿时的已发布版本只读规格展示。

显示所选已发布版本 spec_snapshot 中的身份锚点、视觉锚点、负面约束，并保留参考矩阵。必须读取那个版本的冻结快照，不得用当前包的可编辑规格冒充历史快照。核对网页实际支持的版本查看行为后实现相同语义，不扩大到无关页面。

验收：保存规格 A 后发布 V1，确认只读显示 A；派生 V2 并修改为 B，V1 仍显示 A，草稿显示 B；发布/重入页面后仍正确。先用隔离真实 API 验证数据，再在 WPF 核对显示及不可编辑状态。没有规格的旧版本应有合理空态，不抛异常。
```

### 任务四：保护草稿并隔离旧页面请求

```text
复现并修复 AssetsView 内部切标签导致未保存服装输入丢失的问题，以及人物/角色包旧面板异步返回影响当前页的风险。

重点读 AssetsView.Switch/Render/Epoch、OutfitWorkspace 构造与 BeginEdit、ViewKit.ConfirmLeaveAsync、CharacterPackagePane 的异步操作。服装已有 OwnsOutfits 检查，应保留；其他面板不能只依赖父视图 epoch。

明确草稿的项目/角色/服装归属，实现切换前保存/放弃/取消或可靠的草稿保留，不自动提交用户未确认的编辑。每个异步回写检查当前面板及对象身份，不能用全局禁用所有导航或反复重建整页代替状态管理。

验收：新建未保存服装、编辑已有服装、切资产标签、切角色、离开项目、取消离开、保存失败后返回；输入和参考选择符合用户选择。延迟人物保存/参考上传请求，期间切到另一面板编辑，旧响应不得覆盖当前输入或显示错误对象的成功提示。不得改变 API 事务与版本冲突规则。
```

### 任务五：统一宿主构建部署及真实连接验收

```text
处理 WPF 输出目录 native-host.exe 与 Rust 构建目录产物不同、验证源码和实际运行文件可能不一致的问题。

读 Services/NativeBackend.cs、MangaFlow.Native.csproj、apps/desktop/scripts/start-native.ps1。启动脚本已经复制宿主，不要重复实现；明确项目支持的标准构建/启动/发布入口，确保成功构建后该入口加载的是本次对应宿主，复制/构建失败时不能悄悄复用旧文件。不要仅删除旧文件触发 debug 回退，也不要重构进程生命周期或安全边界。若确需此类 L3 改动，先给设计和风险说明。

验收记录 Rust 与 WPF 构建命令、实际加载宿主绝对路径与哈希。在独立用户目录启动真实 sidecar/API，验证健康、迁移、原作、人物参考、角色包、服装持久化，正常退出后仅清理自有资源。已有审计的 32 次真实 HTTP 通过可作为范围参考，不代表你当前产物已通过。补充真实 WPF 按钮到 API 的验收，而不是仅重复 mock handler 测试。

队列执行与真实供应商另列验证，不要因为未启动 RQ Worker 或未监听固定 8000 就判定故障：先核对项目的 LOCAL/AUTO 执行模式。没有真实供应商调用证据时不得写“生图已打通”。
```
