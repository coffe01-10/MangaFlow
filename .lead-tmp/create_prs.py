import subprocess

def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    out = (r.stdout + r.stderr).strip()
    print(out)
    return out

def pr(head, title, body):
    gh("pr", "create", "--head", head, "--base", "master", "--title", title, "--body", body)

pr("fix/native-w1a-apiclient",
   "Fix native path validation, upload timeouts and canonical swap recovery",
   """Closes #439, Closes #442, Closes #468 (also resolves #471 item 4). Delegation #500.

## Changes
- ApiClient.Validate: '..' traversal check now applies to the path portion only (split on first '?'); absolute-path and '://' rejection stay whole-string. Legitimate encoded query values (project name 序章..终章 in delete-project, scene filter 东京..雨) are no longer unsendable.
- ApiClient.UploadAsync: OperationCanceledException→TimeoutException translation mirroring SendOptionalAsync (guard `when (!cancellation.IsCancellationRequested)` keeps genuine caller cancels as OCE).
- SourceView import: timeout surfaces 请求超时，服务可能已保存。请先刷新确认 copy instead of vanishing silently.
- OutfitWorkspace upload: Chinese timeout copy replaces raw English "A task was canceled.".
- SceneWorkspace 设为规范参考: on failed promote-POST a best-effort rebind restores the original non-canonical binding; message states the true outcome.

## Tests (new files, lead-wired into RunIsolated post-merge)
NativeIssue442Checks (encoded '..' accepted as data; traversal paths still rejected), NativeIssue439Checks (timeout translation, visible notices, canonical rebind). Agent mutation-verified each check fails on unfixed code.

## Verification
dotnet run --project apps/desktop/native-tests green before (54) and after. Lead re-reviewed the full production diff.""")

pr("fix/native-w1b-scriptview",
   "Fix scene-asset deletion flags, variant filtering, save reentrancy and wardrobe multi-save",
   """Closes #426, Closes #440, Closes #448, Closes #484. Delegation #501.

## Changes
- Models.JsonFields.FlagDate: deleted_at is null | ISO-string (web contract), not boolean; SceneAssetItem.Deleted now detects archived assets; legacy boolean true still accepted.
- ScriptView variant dropdown filters archived variants (was: selectable → 422 场景变体已归档).
- ScriptView scene/beat/wardrobe save handlers: in-flight guard (busy flag + disabled button, finally reset) kills double-click double-PATCH false 保存未完成 conflicts.
- Wardrobe save: final assignment map computed once from live selectors and sent as ONE PATCH carrying the scene version (optimistic lock) — the old per-character loop re-PATCHed from the same stale snapshot and resurrected removals.

## Tests
NativeIssue484Checks (timestamp-shape fixtures + dropdown filtering), NativeIssue426Checks (two-dirty-character save → exactly one PATCH, removal absent, version present; double-click guards). Agent verified each fails on unfixed code (both fixes independently required).

## Verification
dotnet run --project apps/desktop/native-tests green before/after. Lead re-reviewed the full production diff (encoding verified intact UTF-8).""")

pr("fix/native-w1c-shell",
   "Harden native shell failure handling (prefs, cancel, F5, boot-hang copy, kill escalation)",
   """Closes #411, Closes #446, Closes #470 (also resolves #471 items 2-3 and #449 item 1). Delegation #502.

## Changes
- Preferences.Save catches IOException/UnauthorizedAccessException (KeyValueStore contract) — async-void OnClosing/HideDock/ShowDock can no longer crash the app or skip the graceful backend stop.
- HomeView CreateProject OCE: 提交结果未知 feedback + dashboard/projects cache invalidation (no more invited duplicate resubmit); drawer failures route to State.Status when the drawer collapsed.
- MainWindow F5: dead backend now sets state.Error + retry cue instead of silent stale metrics.
- NativeBackend startup timeout gets dedicated boot-hang copy (no misleading 提交 wording); WrapStartupFailure is a pure mapping.
- NativeBackend.StopAsync: timeout→Kill(entireProcessTree:true) escalation via pure PlanStopEscalation decision table; process/started reset in finally on every path — reconnect can spawn a fresh host instead of wedging 40s forever.

## Tests
NativeIssue411Checks (decision table + real stubborn-process kill + unwritable prefs paths + copy mapping), NativeIssue470Checks (real mid-POST cancel via Deactivate, collapsed-drawer failure routing, F5 dead-backend). Mutation-verified.

## Verification
dotnet run --project apps/desktop/native-tests green before/after. Lead re-reviewed the full production diff.""")

pr("fix/native-w1d-workflow",
   "Guard WorkflowView against switch races writing one graph into another",
   """Closes #427. Delegation #503.

## Changes (four seams)
- LoadWorkflowAsync: request seq token (ScriptView pattern); stale responses discarded whole; current/workflowId/canvasWorkflowId/version committed as one block with no awaits between.
- Switch handler: workflowId committed BEFORE the leaving flush; SaveNowAsync(leaving) flushes by explicit target.
- SaveNowCoreAsync: PATCH target is the canvas owner (canvasWorkflowId), never "current workflowId"; named targets abstain when they no longer own the canvas; response writeback guarded.
- Activate: id committed before SelectedItem — exactly one load per activation.

## Tests
NativeIssue427Checks: reversed-completion load race, A→B→C switch with pending flush (every PATCH (graph,id) paired), Activate loads exactly once. All three verified red on unfixed code.

## Verification
dotnet run --project apps/desktop/native-tests green. --render chain: only NativeWorkflowRunChecks:594 load-count calibration shifts (getA>=3→getA>=2) — lead recalibrates in the wiring commit. Lead re-reviewed the full production diff.""")

pr("fix/native-w1e-settings",
   "Add dirty guards and cancel-outcome feedback to settings forms",
   """Closes #469 (also resolves #471 item 1 and #429 item 2). Delegation #504.

## Changes
- ProjectSettingsView: dirty flag wired to ALL inputs (incl. typed delete-confirmation name); ConfirmLeaveAsync override covers sidebar switch / Ctrl+K / window close; RefreshAsync (F5) skips reload while dirty; OCE on save/delete surfaces 提交结果未知，请刷新后确认 (delete button re-enabled so the cached view is not left permanently disabled).
- SettingsView: RefreshAsync skips reload while runtime drafts or ConnectionPanel drafts (half-typed API key) exist — live-compare, no sticky flag.

## Tests
NativeIssue469Checks: dirty leave-confirm seam (consulted when dirty, silent when clean), cancelled-save OCE feedback, SettingsView dirty refresh preservation. Sabotage-verified (guards removed → suite fails on intended assertion).

## Verification
dotnet run --project apps/desktop/native-tests green (54). Lead re-reviewed the full production diff.""")
