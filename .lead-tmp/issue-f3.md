**Found:** 第三轮只读审查，组长已亲自读代码核实（2026-09-11）。

## 机制

两处注释把 dist 锁的平台选择写成跨语言不变式：

- `apps/desktop/scripts/dist-build-lock.sh:21-23`："The two forms never mix on one host: **flock exists exactly where python's fcntl does**"
- `apps/desktop/scripts/build-web-standalone.py:61-62`：同一句 "The mechanisms never mix on one host: flock exists exactly where fcntl does."

该不变式**不真**：MSYS2 bash 自带 `flock`（util-linux），而同机调用的 Windows 原生 python 没有 `fcntl` 模块（POSIX-only）。此组合下 bash 写方走 flock 分支（互斥量=打开文件描述），python 写方走 O_EXCL 锁文件分支——**两把锁互不感知**，`test_dist_build_lock.py::test_python_and_bash_writers_interlock` 在那种主机上会真实失败（测试本身没错，是注释声称的前提过宽）。

本仓主路径（git bash 无 flock + Windows python 无 fcntl）两侧都走 fallback 锁文件，实际一致，**生产行为无恙**——这是注释/假设级缺陷，不是锁缺陷。

## 修复面

把两处注释收窄为"假设 bash 与 python 来自同一环境来源（本仓 git bash 主路径下两者皆无 flock/fcntl，同走锁文件分支）"，并明确写出 MSYS2-bash+Windows-python 混装会破坏互斥、属于不支持配置。可选：在 `test_dist_build_lock.py` 中按实际分支选择断言交叉配对的前提（flock 与 fcntl 可用性分别探测，仅在两侧同源时运行 interlock 断言，否则 skip 并注明原因）。注释收窄为必改，测试加固可选。
