import subprocess

closes = {
366: """Fixed in PR #379 (`fix/web-round2-paused-and-p4` c54a731): both settings pages invalidate their query on 409 (the workflow-studio pattern), and the system page additionally sends the version read from the refreshed cache rather than the stale localDraft — without that second half the 409 loop never terminates. Tests on both pages assert the refetch and a successful retry carrying the server's new version.""",
367: """Resolved in PR #379 (ba50524) as relabel-with-evidence rather than wiring: web polling has no single consumption point (six files with deliberately differentiated intervals: 2000/2500/3000/4000ms per view), so wiring would be a cross-cutting change, not a cheap one. The setting now honestly states 仅桌面客户端生效，Web 使用内置固定间隔 (help text + code comments pinning the divergence at lib/task-status.ts and naming the future integration seam). Test asserts the help text. Reopening is warranted only if web polling is consolidated to a single interval consumer.""",
368: """Fixed in PR #379 (0a23b3f): `?stress` is now ignored outside development (production silently uses the real editor); a cross-chapter `?page` deep link renders a dismissible banner (记忆关闭态 per target page) instead of silently falling back to page 1. Six new test cases cover both behaviors.""",
369: """Fixed in PR #379 (c065a6d): the scene workspace list query now uses `api.sceneAssetsAll` (the endpoint pair created precisely to eliminate this truncation class); a 201-item fixture drives the real pagination loop and asserts the second page request (offset=200) and that card 201 is visible. The existing 12 tests still pass.""",
370: """Fixed in PR #379 (7369222): the scene/beat edit cards' cancel/X now runs `confirmDiscardDraft()` first — the same dirty-draft confirmation every other exit path in the file already uses. New colocated test file (script-editor.test.tsx, 5 cases: dirty→confirm-keep, dirty→confirm-discard, clean→direct close, both cards).""",
372: """Fixed in PR #379 (`fix/tooling-round2-residuals` 75eee32): ① static-build tee can no longer fail before the lock (mkdir -p dist ahead of the build; the destructive section stays inside the #350 lock); ② the e2e's dist verification holds a shared flock for its check window on POSIX/CI with a deadlock-surface note in the docstring (readers hold one lock only; the Windows fallback path is documented as an accepted residual — no shared-lock primitive to compose with the O_EXCL writer); ③ `poweredByHeader: false` (csp contract test re-run 4/4); ④ README D7 count corrected to 31 with a fresh on-disk count. Known accepted residual: the long serving window (D5/node reading dist during a session) remains unlocked beyond the provenance stamp — out of this issue's file scope.""",
}

for number, body in closes.items():
    result = subprocess.run(["gh", "issue", "close", str(number), "--comment", body],
                            capture_output=True, text=True, encoding="utf-8")
    print(f"#{number}: {result.returncode}")

new_issues = [
("[P4][Web] PAUSED 运行期间编排页轮询停摆——从生成台审批返回后页脚停在过期显示",
"""**Found:** 第二轮 #365 修复代理的范围外发现（2026-09-11）。

## 机制

`apps/web/components/workflow-studio.tsx` 的 run 轮询 `refetchInterval` 谓词只认 RUNNING——run 到审批栅栏转 PAUSED 后（用户被指引去生成台"前往采用"），编排页不再刷新：审批完成/继续运行后返回该页，页脚与节点徽标停留在过期的 PAUSED 显示，直到窗口聚焦触发 refetch。桌面端 PollTick 同样只看 RUNNING（#371 修复时按 web 契约钉住）。

## 修复面

把 PAUSED 加入轮询谓词（代价：栅栏等待期间持续 3s 轮询；可用较长间隔如 10s 折中）；两端同步；测试钉住 PAUSED 态仍轮询。"""),
("[P4][Web+Native] generator.page 审批栅栏无可选图片模型时「确认继续」永久禁用",
"""**Found:** 第二轮 #365 修复代理的范围外发现（2026-09-11）。

## 机制

`apps/web/components/workflow-studio.tsx` 审批条：`generator.page` 节点需要选图片模型才能「确认继续」（`disabled={... && !drawModel}`）；imageModels 为空（未配置任何可用图像模型）时按钮永久禁用，run 停在 PAUSED 无提示。#365 修复后用户至少可以取消（不再是"彻底卡死"），但无模型时没有任何出路提示。

## 修复面

无可用模型时给出明确指引（"未配置可用图像模型，请先在设置中启用，或取消本次运行"）；或允许跳过节点。"""),
]

for title, body in new_issues:
    result = subprocess.run(["gh", "issue", "create", "--title", title, "--body", body],
                            capture_output=True, text=True, encoding="utf-8")
    print(result.stdout.strip() or result.stderr.strip())
