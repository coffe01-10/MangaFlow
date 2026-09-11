**Found:** 第七轮终扫（a08dae8..a7b7e55）（2026-09-11）。

## 机制

`apps/desktop/scripts/assemble-web-resources.py` 行 50-53：`_clear(retired)` 在 assemble 开始时无条件 rmtree `web.old-<pid>`（注释只考虑"crash remnant"）。#382 的回滚失败路径特意保留 `web.old-<pid>` 作为唯一旧树恢复副本并指示操作者"Manually move ... before the next tauri build"——若操作者直接重跑且新进程复用同一 pid（Windows 重启后小 pid 顺序分配，安静机器上概率不低），恢复副本在换入新树**之前**就被删掉，与错误信息指令自相矛盾。若 run B 再中途失败，旧树新树俱失（仍可从 dist/web-standalone 重建，故 P4）。

## 修复面

`_clear(retired)` 仅在 `res.exists()` 时执行（res 在位 ⇒ retired 必是残留）；否则报错拒跑并提示先手动恢复。测试：monkeypatch 复现"res 缺失 + retired 存在同 pid"场景，断言第二次 assemble 拒跑且 retired 字节级存活。
