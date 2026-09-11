# Round 10-11：审查发现与修复归档（2026-09-11）

## Round 10（扫 PR #399 diff ce1e43e..a08b9bc）：零发现
计数 1/2。核验含变异实验（删 target_kind=file 赋值 → 新用例两层独立红）。

## Round 11（组合镜头扫 3b33ad9..6a0fe46）：2×P4 文档精度
- F1 build-frontend-static.sh #385 头注释仍称"一律重建为 junction"（file 臂实为 cp -l
  hardlink）；行 96/100 "cannot read junction target" 消息未泛化。
- F2 assemble-web-resources.py 模块头 docstring 把 #393 拒跑写成一般行为，实际仅
  same-pid 生效（跨 pid 新跑不删停放树、正常落位）。
- 另核验：桌面/web 目录失败语义无契约裂缝（差异早于本会话）；两测试文件同套串行
  无冲突（gate 日志互证）；#391 谓词等价性主张成立（model-visibility.ts 豁免子句）。

→ issue #400 → PR #401（merge 0ffe1d5，纯注释/消息改动，门禁三件套全绿）。

## 计数状态

Round 10 零（1）→ Round 11 非零（0）→ Round 12 待跑（扫 6a0fe46..0ffe1d5）。
停机条件：连续两轮零发现。Round 12 零则计数 1，Round 13 零则停机总结。
