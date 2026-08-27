# README 视觉素材说明

## 参考与改编范围

参考项目为 [keli-wen/agy-staff](https://github.com/keli-wen/agy-staff)，研究版本固定为 [`0fcddbb963cb5706d051c093b91494f23b85d30d`](https://github.com/keli-wen/agy-staff/tree/0fcddbb963cb5706d051c093b91494f23b85d30d)。

- [中文 README](https://github.com/keli-wen/agy-staff/blob/0fcddbb963cb5706d051c093b91494f23b85d30d/README.zh-CN.md)：借鉴居中品牌区、两行本地徽章、概览、使用场景与上手顺序。
- [徽章生成器](https://github.com/keli-wen/agy-staff/blob/0fcddbb963cb5706d051c093b91494f23b85d30d/scripts/gen_badges.py)：复用 MIT 授权的像素字形与阶梯边框思路。
- [Logo 生成器](https://github.com/keli-wen/agy-staff/blob/0fcddbb963cb5706d051c093b91494f23b85d30d/scripts/gen_logo.py)：参考像素字标与明暗主题适配方式，不复用其闪电标志和品牌配色。
- [原始概览 SVG](https://github.com/keli-wen/agy-staff/blob/0fcddbb963cb5706d051c093b91494f23b85d30d/assets/design.svg)：参考“用图交代工作方式”的信息组织，不照搬其产品机制或截图。

MangaFlow 的漫画页图形、流程文案与布局为本项目定制。米纸色 `#f4f1e9`、墨黑 `#151512`、朱红 `#b23c25` 与绿色 `#47745a` 取自现有 [globals.css](../../apps/web/app/globals.css)。像素字形的完整 MIT 声明保存在 [LICENSE.pixel-font.txt](LICENSE.pixel-font.txt)，并嵌入使用字形的 Logo 与徽章 SVG。该第三方声明不代表 MangaFlow 项目已选择 MIT 许可证。

## 事实依据

本次核对基于 2026-08-27 的主分支内容；文档改写前基线为 `bf25aa5469a86664fec29121240dfd6a14f751f6`。以下是文案依据，不是新的运行验收记录：

| README 中的信息 | 仓库依据 |
| --- | --- |
| Node.js 22+、开发与检查命令 | [package.json](../../package.json) |
| Python 3.12+ | [pyproject.toml](../../apps/api/pyproject.toml) |
| Windows 安装、迁移、启动与代理处理 | [setup-codex.ps1](../../scripts/setup-codex.ps1)、[start-dev.ps1](../../scripts/start-dev.ps1) |
| 默认数据位置与配置入口 | [.env.example](../../.env.example)、[config.py](../../apps/api/app/config.py) |
| 健康检查预期字段 | [health.py](../../apps/api/app/api/routes/health.py) |
| 页面与人物、服装、分镜、候选关系 | [数据模型](../../docs/data-model.md)、[开发进度](../../docs/development-progress.md) |
| 模型能力与供应商接入范围 | [供应商与模型平台](../../docs/provider-platform.md)、[工作台](../../apps/web/components/project-workspace.tsx) |
| 密钥加密与开发环境主密钥 | [credential_crypto.py](../../apps/api/app/services/credential_crypto.py) |
| 当前稳定性边界与历史测试 | [路线图](../../docs/roadmap.md) |

没有使用真实界面截图、生成漫画样例或性能数字。中英文概览图分别明确标为“流程示意 · 非界面截图”和“Workflow diagram · not a screenshot”。徽章是静态版本要求 / 开发入口 / MVP 阶段说明，不模拟实时 CI、许可证或供应商可用状态。

默认入口 [README.md](../../README.md) 使用英文，完整中文版位于 [README.zh-CN.md](../../README.zh-CN.md)，顶部提供双向切换。两版共享安装命令、功能边界、Logo 和徽章；概览图分别提供中文与英文版。英文 README 明确说明应用界面和深入文档仍以中文为主，本次不涉及应用国际化。

## 文件清单

| 文件 | 用途 |
| --- | --- |
| [logo.svg](logo.svg) | 漫画页图标与像素字标，明暗主题适配 |
| [node.svg](badges/node.svg)、[python.svg](badges/python.svg) | 第一行：最低运行版本 |
| [windows.svg](badges/windows.svg)、[stage.svg](badges/stage.svg) | 第二行：开发入口与当前阶段 |
| [overview.svg](overview.svg) / [overview.png](overview.png) | 中文桌面流程图，920 × 620 逻辑画布，PNG 为 2 倍分辨率 |
| [overview-mobile.svg](overview-mobile.svg) / [overview-mobile.png](overview-mobile.png) | 中文窄屏纵向图，430 × 904 逻辑画布，PNG 为 2 倍分辨率 |
| [overview-en.svg](overview-en.svg) / [overview-en.png](overview-en.png) | 英文桌面流程图，与中文桌面版共用布局逻辑 |
| [overview-mobile-en.svg](overview-mobile-en.svg) / [overview-mobile-en.png](overview-mobile-en.png) | 英文窄屏纵向图，与中文窄屏版共用布局逻辑 |

两版 README 分别用 `<picture>` 在窄屏选择相应语言的纵向 PNG；不支持该选择的阅读器回退到同语言桌面 PNG。PNG 用于固定字体与版面，SVG 保留为可编辑来源。核心流程在正文和图片替代文本中均以相应语言说明，不依赖图片传递唯一信息。

## 再生成

在已经完成仓库依赖安装的环境中，于仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts/generate_readme_assets.py --force
node scripts/render_readme_assets.mjs --force
```

Python 生成器仅使用标准库，无网络请求；PNG 渲染使用当前 Web 依赖树中已有的 `sharp`。`--force` 只重建清单内的已知素材，不访问应用数据库或供应商。默认不带参数时拒绝覆盖已有输出。

只校验源码与成品是否一致，不写文件：

```powershell
.\.venv\Scripts\python.exe scripts/generate_readme_assets.py --check
node scripts/render_readme_assets.mjs --check
```

Logo 和徽章采用矩形像素路径，不依赖外部字体。概览 SVG 使用本机中文字体（优先 Microsoft YaHei），PNG 一致性校验要求相同渲染器和字体环境；不同系统重新渲染后应重新检查字形、换行与边界。

## 验收边界

2026-08-27 中文首版文档验收记录（双语扩展前）：

- README 与两份说明文档的离线链接检查无错误；7 个 SVG 的 XML、视口、引用和可访问标题检查通过。
- 7 个 SVG 与生成器输出一致，2 个 PNG 与各自 SVG 在本机渲染环境中一致；新增脚本通过 Ruff 与 Node.js 语法检查。
- 本地浏览器检查覆盖约 900px 正文区的桌面明暗主题，以及 375px 暗色、390px / 430px 浅色视口；6 张引用图片均加载成功，窄屏选中纵向概览，整页没有横向溢出。
- 已查看实际渲染截图，核对 Logo 明暗对比、两行徽章及概览中文可读性。页面内导航与跨文档备份标题已核对。

本地预览使用 PowerShell 的 Markdown 渲染器与近似 GitHub 样式；其中文标题标识已在临时预览中按 GitHub 风格调整。上述检查不是 GitHub 实际页面验收，未验证所有阅读器对 `<picture>` 的处理。

2026-08-27 双语扩展验收记录（基于 `fbee071`）：

- 英文主入口与完整中文版的 4 段 PowerShell 代码块、全部行内技术标识一致；四份相关文档的离线链接检查无错误。
- 9 个 SVG 与生成器输出一致，4 个 PNG 与对应 SVG 同步。中文既有图片、共享 Logo 和徽章内容保持不变。
- 英文和中文各通过 5 组本地视口 / 主题检查：1440px 明暗主题、375px 浅色、390px 暗色、430px 浅色。每组 6 张引用图片均加载，语言与横纵版选择正确，页面无整体横向溢出。
- 实际点击“简体中文”进入中文版，再点击“English”返回默认英文 README；两版页面导航锚点均存在。已查看英文桌面、暗色与手机概览的实际渲染截图。
- 只检查本地近似 GitHub 的预览，未将文档推送到远程以验证 GitHub 实际渲染。预览服务、截图和会话日志在检查后清理。

这些结果仅用于文档与素材验收，不替代产品测试。本次未重跑 `npm run check:full`，没有为了 README 启动业务应用或执行真实供应商调用；历史测试状态仍以路线图记录为准。
