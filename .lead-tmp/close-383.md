**已修复（round 3 集成批次）**：两处注释收窄为"bash 与 python 假定同一环境来源；本仓主路径 git bash 两侧皆无 flock/fcntl、同走 noclobber 锁文件分支；MSYS2-bash+Windows-python 混装时两侧机制互不可见、互斥失效，不受支持"。`test_dist_build_lock.py` 的 interlock 用例运行时分别探测 bash 侧 `command -v flock` 与 python 侧 `import fcntl`，两侧不同源时 skip 并注明原因（引用本 issue），避免混装主机假失败。

- 提交：87a5ce9。
- 验证：pytest `test_dist_build_lock.py` 5 项全过（本机两侧皆无 flock/fcntl、机制匹配，interlock 真实运行未 skip）；`bash -n` 语法检查过；ruff 干净。
- NOT RUN：真实混装主机（MSYS2 bash + Windows python）上 skip 路径的实际触发（代码实现与审查过，无该环境）。
