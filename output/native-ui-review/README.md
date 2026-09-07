# 原生工作台网页样式验收

2026-09-07。PNG 为 WPF 真正控件树的离屏渲染，使用三部样例作品，不是用户项目数据。
`native-home-1320.png` 包含原生顶栏与首页；其余两张检查宽 / 窄首页布局。
没有打开、关闭或操作用户正在调试的窗口。

- 原生 Release 编译：0 警告，0 错误，输出 `apps/desktop/native/bin/WebAligned/`。
- 原生回归：24 项检查通过；另通过 1320 / 940 DIP 离屏布局及完整窗口内容树加载。
- 修复离屏检查发现的进度条只读属性双向绑定异常。
- 全仓 `npm run check`：供应商中立性、ESLint、Ruff 通过；Pytest
  1320 passed / 37 skipped / 1 failed。
- 失败：`tests/test_local_worker.py::test_execute_job_applies_runtime_lease_override`，
  Windows 清理 `lease-override.db` 时 WinError 32（文件句柄占用）。本轮没有修改
  此测试或 Worker 代码。测试进程退出后清理该次残留临时目录。
- check 因后端测试失败停止，后续 Vitest / Web build 未运行。本轮仅改原生界面，
  未修改 Web 源码。
- 真实交互、供应商调用、完整原生功能迁移和帧率验收不包含在此次离屏检查中。
