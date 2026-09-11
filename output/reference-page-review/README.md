# 原始参考素材页还原与验证

本轮还原参考资产下的第五页「原始参考素材」。依据网页 `assets-section.tsx`、`use-assets-workspace.ts` 和 `globals.css`，使用原生 WPF 控件。

## 页面变更

- 网页同款标题、文件计数、黑底直角用途选择器、大尺寸虚线上传区。
- 将固定 168 像素的竖向小卡片换成双列横向素材卡；窄窗口自动单列。74 像素缩略图、原图预览、名称编辑、用途/大小/状态、绑定关系和操作入口分层显示。
- 上传、拖入、中文改名、用途修改、删除、角色绑定与解绑使用现有 API。分类和删除必须确认；失败保留输入，处理期间阻止重复提交。
- 服装和风格参考先加入待保存选择，进入对应档案编辑器后再保存或分析，选择本身不写入假绑定。提供角色选择器，避免没有可操作的绑定目标。
- 上传用途在发请求前确定；返回错误用途的重复素材不会触发角色绑定。图片上传成功但绑定失败时显示部分成功提示并保留已上传素材。
- 刷新保留当前页实例、改名输入和待保存选择。读取失败保留内容并提供重试；离开页面或切换项目后不执行迟到上传的后续绑定。
- 页面使用实色底、布局像素对齐和 Display 文本排版；没有给文字所在容器添加缩放或透明度动画。

## 验证

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/reference-page-review --references
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/style-page-review --style
```

最终构建成功：0 错误、35 警告。原始素材页定向检查和相邻风格页回归均通过。

原始素材页覆盖：1100/650 DIP 响应布局、改名失败保留输入、取消危险操作不发请求、重复绑定保护、204 解绑、刷新与读取失败保留页面、服装/风格待保存参考交接、分类移动卡片、204 删除、上传用途校验、上传成功但绑定失败、离页后忽略后续绑定。

另行启动实际 native-host 与 Python sidecar，使用独立临时用户目录和 SQLite。18 次真实 HTTP 检查通过：创建隔离项目和角色、上传图片、原图读取、640 缩略图、中文改名、重复内容跨用途上传返回 409、角色绑定与读取、204 解绑与读取、重新绑定、分类及旧绑定解除、风格引用列表、204 删除、删除后列表为空、原图返回 404。宿主退出码 0；临时脚本、图片、数据库与用户目录已清除。没有修改后端，没有读取用户项目数据或调用图片生成供应商。

## 截图与边界

- [双列宽窗口](native-references-1100.png)
- [单列窄窗口](native-references-650.png)

截图来自 WPF 离屏测试，使用隔离 fixture 数据与缺图占位，不是用户实际项目。UI 交互测试使用 HTTP fixture，真实后端接口检查独立执行。尚未进行真实窗口鼠标逐项验收、图片供应商生成、所有显示器 DPI 或动画帧率验收；本轮不代表所有页面已完成一比一迁移。
