# 生成素材库 · 本轮还原记录

对照网页 `library-section.tsx` 和 `globals.css` 调整 WPF 原生素材库。

## 已完成

- 页面标题、候选计数、细线分隔及与前面工作区一致的字号和间距。
- 章节、收藏、角色、类型、模型、清晰度组成对齐的筛选栏；宽窗口给模型名称更多空间，窄窗口自动分行。
- 日期范围与重置独立成行；选中的筛选项高亮，长选项提供悬停提示。
- 批次标题显示编号、生成类型、本地日期时间和图片数量。宽窗口最多四列，多个候选的批次跨列，窄窗口整批排列。
- 去除候选图片的重复边框和额外内边距，保持 3:4 预览区域；无图片时禁用放大入口。
- 已暂选状态、收藏、撤回和删除保留既有操作；长模型名使用省略号及完整名称提示。
- 区分“素材库为空”和“没有符合筛选条件的素材”。即使接口返回相同空数据，筛选变化后也会更新空状态。
- 保留整章导出门禁、阻塞页跳转和导出文件下载。

## 验证

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/library-page-review/verified --library-page
```

最终构建 0 错误、16 个警告。专项回归通过：

- 1240、940、650、360 DIP 宽度下筛选和批次不重叠、不越界。
- 四列单候选批次、多个候选跨列、窄窗口整批布局。
- 全部筛选控件传递到接口查询；重置一次只提交一次素材读取。
- 筛选无结果和未筛选空库的状态切换。
- 原有 NativeLibraryChecks：游标与筛选隔离、收藏防重复提交与失败恢复、候选撤回/删除、未变化数据刷新保留卡片、导出门禁及防重复导出、下载文件保存和中断清理。

以上接口测试使用 HTTP 消息处理器夹具；下载检查确实写入临时文件并验证清理，但不是对运行中后端的联调。

## 最终截图

- [宽窗口四列批次](verified/native-library-1240.png)
- [中等窗口](verified/native-library-940.png)
- [窄窗口](verified/native-library-650.png)
- [最窄布局](verified/native-library-360.png)
- [筛选无结果](verified/native-library-filtered.png)
- [多个候选的混合批次](verified/native-library-mixed.png)

截图使用独立的失败、排队及选择状态夹具，预览区未加载真实生成图片。截图不包含主窗口导航栏。

## 尚未验收

真实图片网络加载、运行中后端/数据库联调、高 DPI 真实窗口字体及滚动帧率：NOT RUN。本轮未修改后端。

测试偏好文件和下载临时文件已清理，只保留最终截图及记录。
