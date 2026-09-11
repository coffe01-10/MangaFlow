**已修复（round 3 集成批次）**：`assemble-web-resources.py` 的两条 rename 现在包进同一个 `try/except BaseException`——rename 之间落入的异步异常（KeyboardInterrupt/SystemExit/MemoryError）与 rename#2 失败同样触发回滚；回滚 rename 自身失败时置 `rollback_failed` 并抛带恢复指引的 RuntimeError（给出 retired 完整路径与 `Move-Item` 手动恢复命令，`from` 链保留原始错误）；finally 仅在 `res.exists()`（新树就位或回滚成功）时才删 retired，否则保留并大声报错。docstring 同步如实化（#347, #382）。

- 提交：cf4bcff。
- 验证：pytest 4 项全过——新增用例 A（rename#1 完成后抛 KeyboardInterrupt：异常传播、旧树字节级还原、无残留）与用例 B（回滚也失败：res 缺失、retired 存活且字节为旧树、消息含恢复指引）；既有两项保持绿。ruff 干净。
- NOT RUN：真实 85MB bundle 交换、tauri build、真实 Ctrl-C 信号（按窗口语义用 monkeypatch 注入）。
