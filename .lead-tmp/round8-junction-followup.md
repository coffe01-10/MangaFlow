# Round 8：审查发现修复归档（2026-09-11）

Round 8 只读审查（扫 PR #395 diff 3b33ad9..d886f10）发现 F1/F2 → issue #396 →
PR #397（merge 44dfb0e，组长直修，L1）。

## 发现与修复

- F1（P3）#392 修复无已提交回归测试 → 新增 tests/test_build_frontend_static_junction.py
  （6 钉；readlink 走 PATH shim——宿主无符号链接权限，mklink //J 与 cp -l 副作用走真文件系统）。
- F2（P4）悬空绝对目标逃逸空 target_kind 进 mklink 分支报 cmd 原始错误 →
  绝对分支仅在 -d/-e 命中时赋 target_abs。

## 钉子真实性（历史版本探针）

- d886f10（修 F2 前）：恰好只挂 dangling-absolute 一条。
- a7b7e55（修 #392 前）：挂 4 条，全部落在 #392 范围（相对目录/相对文件/悬空相对/悬空绝对）。

## 门禁（gate-render/cargo/check-r8.log）

1. --render：68 PASS / 0 FAIL（54 原生检查）。
2. cargo test：全 ok。
3. npm run check：pytest 1526 passed / 37 skipped（+6）、Vitest 488、构建绿。

## 环境教训（值得记）

- Git Bash 启动时把 /mingw64/bin:/usr/bin 预置在转换后的 Windows PATH 之前，
  Python 侧 env PATH 前置 shim 无效；必须在 bash 脚本内部 export PATH 前置。
- os.readlink 对 junction 返回的目标可能带 \\?\ 前缀，断言前需剥离。
- cmd 复合命令里 `;` 会被此 shell 环境吞进上一命令参数（pytest 报 unrecognized
  arguments），串联命令用 && / || 。

## Round 9 输入

审查范围：49ac949..44dfb0e（本轮 diff：sh 12 行改动 + 新测试文件 209 行）。
零发现计数：Round 8 非零（F1/F2）→ 计数 0。
