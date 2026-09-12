# -*- coding: utf-8 -*-
"""Wave2 closure round: evidence comments + closes. Run from repo root."""
import subprocess
import sys

def gh(*args, text=None):
    result = subprocess.run(["gh", *args, "--repo", "coffe01-10/MangaFlow"],
                            input=text.encode("utf-8") if text is not None else None,
                            capture_output=True)
    out = (result.stdout or b"").decode("utf-8", "replace") + (result.stderr or b"").decode("utf-8", "replace")
    if result.returncode != 0:
        print("FAIL:", args, out)
        sys.exit(1)
    print("OK:", args, out.strip()[:200])
    return out

lead = "lead 复核记录（2026-09-12）："

comments = {
    486: (
        lead + "四项全覆盖后关闭。item1-3 由 PR #540（cc05291）修复，item4 由 PR #539（3d33526）修复。\n"
        "独立验证：wt-c --render 全链绿；四轴变异——①恢复 SequenceEqual 后 #486-2 检查仍通过（C# record 合成等值"
        "包含 body 声明的 get; init; 属性，原审计『仅主构造参数参与相等』前提不成立，缺陷按描述不可复现；"
        "PR #540 将其如实处理为 SameDisplay 契约固化）；②删 VerifyPendingBatchAsync → 「重试未检测到缺席的死批次」转红；"
        "③恢复 EnsureSuccessStatusCode → 409 以原始英文泄漏转红；④恢复 Lightbox catch{} → 「失败 UI 未出现」转红。"
        "检查已由组长接入 RunIsolated 链（892cb95）。"
    ),
    521: (
        lead + "已合并 PR #539（3d33526）。wt-b --render 全链绿（5 项新检查全 PASS）；变异验证：回退两个生产文件后"
        "首项检查以「创建角色模型包双击必须只发出一次 POST（实际 2 次）」转红。PublishConfirmOverride 测试缝与"
        "StoryboardView.DeleteConfirmOverride 先例一致，生产语义未变；保存进行中发布为拒绝语义（委派单允许），"
        "保存完成后重试发布有严格排序断言。"
    ),
    522: (
        lead + "已合并 PR #540（cc05291）。#486-2 独立核实为前提不成立（见 #486 评论），代理未谎称修复、按加固处理——"
        "符合要求。MediaErrors 与 ApiClient.ThrowResponseError 逐行比对一致（fallback 构造/422 数组/blockers/409 前缀/"
        "2000 截断/同一异常类型）；VerifyPendingBatchAsync 的 target_type+target_id 过滤与后端路由必填参数吻合"
        "（asset_generation.py:1051-1073）。"
    ),
    523: (
        lead + "已合并 PR #541（753a572）。委派单所写 src-tauri/src/protocol.rs 不存在，实际为 apps/desktop/shell-core/src/protocol.rs，"
        "改动确认为注释级。canonicalize（GetFinalPathNameByHandleW 逐层解析、未创建叶子沿用父目录形态、失败回退词法路径）"
        "与 KAT 哈希方案逐项核实；start-native.ps1 有效 UTF-8 无 BOM、行尾与 master 一致（blob LF/工作树 autocrlf CRLF），"
        "新增行纯 ASCII。wt-d --render 全链绿；两轴变异（关规范化/删拒绝分支）均转红。"
    ),
    520: (
        lead + "已合并 PR #542（1bc8573 + d8840e1 接线）。代理中断于 NativeIssue429Checks 的 TaskCanceledException——"
        "诊断结论：链内高负载下的等待健壮性缺陷（DIAG 探针显示读取实际 0.5s 内发出，非生产缺陷），lead 加固"
        "（>= 判定、15s 预算、可读超时异常）后双套件绿。七轴变异验证（同项目确认/保真分派/F5 守卫/切章确认/纪元/"
        "关闭守卫/错误映射）全部以精确预期消息转红。#429 item2（ProjectSettingsView/SettingsView 刷新脏守卫）"
        "不在本委派文件白名单，#429 已重开跟踪该剩余项。"
    ),
    449: (
        lead + "三项全部落地：item1（boot-hang 文案）Wave1 已修（链内既有 PASS 行）；item2（媒体面错误契约）PR #540；"
        "item3（LocalEditWindow 英文原文上屏）PR #542。组合语义核实：HTTP 错误经 ImageStore 映射（#540），"
        "连接层失败由 DescribeImageLoadFailure 兜底（#542），LocalEditWindow 不再有裸英文上屏路径。"
    ),
}

for number, body in comments.items():
    gh("issue", "comment", str(number), "--body", body)

# reopen 429 (item2 uncovered) with evidence, keep open
gh("issue", "reopen", "429")
gh("issue", "comment", "429", "--body",
   lead + "items 1/3/4 已由 PR #542（1bc8573）修复并验证（七轴变异含切章确认/纪元/关闭守卫）。"
   "item2（ProjectSettingsView.cs:397-402 与 SettingsView.cs:636-639/:767-770 的 RefreshAsync 无条件重载，"
   "单选/并发编辑与半输入 API key 被清空）不在 #520 委派文件白名单内，本波未覆盖——保留本单仅跟踪该项。")

# close delegations + fully covered defects
for number in (520, 521, 522, 523, 486, 449):
    gh("issue", "close", str(number))

print("DONE")
