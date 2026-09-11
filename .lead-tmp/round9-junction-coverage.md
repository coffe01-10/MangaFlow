# Round 9：审查发现修复归档（2026-09-11）

Round 9 只读审查（扫 PR #397 diff 49ac949..44dfb0e）发现漏钉/BOM/编码 → issue #398 →
PR #399（merge a08b9bc，组长直修）。

## 发现与修复

- P3 绝对 file 目标分支零覆盖（变异实验证实：删赋值 6 条测试仍全绿）→
  补 test_absolute_file_target_rebuilds_as_hardlink；变异复核新用例恰好红。
- P4 测试文件 BOM（全仓 244 个 Python 文件唯一）→ 剥离（首字节 22 22 22）。
- subprocess text=True 用 cp936 解 UTF-8 → 显式 encoding=utf-8, errors=replace。
- sh 注释与实测旧行为不符（实际是 mklink 静默建指向文件的 junction、rc=0）→ 注释改准。

## 验证

- 7/7 pass；变异探针 1 failed（恰好新用例）/ 6 passed；ruff、bash -n 过。
- 门禁：--render 68 PASS/0 FAIL；cargo 全 ok；npm run check 1527 passed/37 skipped、
  Vitest 488、构建绿（gate-render/cargo/check-r9.log）。

## Round 10 输入

审查范围：ce1e43e..a08b9bc（+21/-3：sh 注释 3 行、测试 +19）。零发现计数仍 0。
审查方法论升级点：变异实验（在 TEMP 副本上改被钉代码看测试是否红）已成为本轮
标准验真手段，Round 8/9 各抓到一个"绿灯空转"缺口。
