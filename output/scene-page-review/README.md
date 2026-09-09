# 场景资产页还原与应用图标替换

本次按网页 `scene-workspace.tsx` 及其 CSS 实现 WPF 场景资产页，保留当前工作区其他修复。没有修改后端业务代码。

已接入：场景搜索、状态/地点前缀/室内外筛选、归档列表、场景创建与基本信息编辑、结构化空间字段、带版本号更新、确认状态、主参考图上传/预览/解绑/规范设置、环境变体创建/编辑/默认设置/删除及专属参考图绑定、归档前剧本引用检查、恢复。宽窗口采用左侧列表与右侧详情，窄窗口改为上下布局。编辑失败保留输入；保存过程中拦截重复提交；离开页面后忽略迟到读取。

应用图标来自用户提供的 `D:/下载/ChatGPT Image 2026年9月9日 19_58_56.png`，保留透明背景，转换为 16、20、24、32、40、48、64、128、256 像素的 ICO，并更新 32/128 PNG。WPF 可执行文件及主窗口均使用新图标。

## 验证

- `dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly`：成功，0 错误；本次输出 40 警告，含依赖项目/临时 WPF 工程输出。
- `dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/scene-page-review --scenes`：通过。覆盖宽窄布局、结构化室内外、筛选参数、版本更新、编辑器冲突提示/输入保留、重复保存拦截、变体覆盖字段限制、归档引用计数及失败不报零、离页响应隔离，以及 ICO 九档尺寸。
- 启动实际 native-host 和 sidecar，独立用户目录/SQLite，24 次真实 HTTP 检查通过：场景结构化创建/编辑、409 冲突、筛选、PNG 上传/读取、规范参考/状态、变体新增/编辑/默认/专属参考、解绑、删除变体、归档和恢复。宿主退出码 0；测试数据和临时运行目录已清除。
- `git diff --check`：通过。

这里的 WPF 行为检查使用 HTTP fixture，真实 HTTP 验证单独执行；二者不等同于真实窗口鼠标操作的端到端验收。没有调用图片供应商。未进行所有显示器/DPI 的实机文字清晰度或动画帧率验收，也未宣称像素级一致。

当前公共 API 客户端未保留结构化 HTTP 错误状态，所以归档引用检查遇到无法区分的缺失剧本/网络错误时显示“无法确认引用数量”，不把失败错误地当成零引用。

## 截图

下列截图来自离屏 WPF 测试示例，不包含用户真实项目数据；参考图缺失状态为测试数据的一部分。

- [宽窗口场景页](native-scenes-1100.png)
- [窄窗口场景页](native-scenes-650.png)
- [125% 渲染](native-scenes-125pct.png)
- [编辑场景弹窗](native-scene-editor.png)
- [环境变体弹窗](native-scene-variant-editor.png)
