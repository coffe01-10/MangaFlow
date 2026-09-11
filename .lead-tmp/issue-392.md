**Found:** 第七轮终扫（a08dae8..a7b7e55）（2026-09-11）。

## 机制

`apps/desktop/scripts/build-frontend-static.sh` 的 `recreate_junction`（87-101）：`clone_hardlink_tree` 跳过并上报所有 `-L` 条目（不区分 junction 与真符号链接）；`recreate_junction` 用 `readlink` 原文与绝对 `REPO_ROOT` 前缀匹配，**相对目标**（npm/pnpm `.bin` 风格 `../next/...`）永远不匹配 → 落入"points outside the repo"分支 return 1 → `set -euo pipefail` 终止构建。而相对目标从链接所在目录解析后实际在树内。当前宿主（npm + Windows 文件 shim）不触发——休眠边界。

## 修复面

匹配前把相对目标解析为绝对路径（`target_dir=$(dirname ...); cd "$target_dir" && cd "$target" && pwd` 一类），或对非 junction 的真符号链接单独处理。验证：在临时目录构造相对符号链接 fixture 走一遍克隆函数断言正确重建/跳过（bash 可测，不需要真 node_modules）。
