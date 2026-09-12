import subprocess, json

REPO = "coffe01-10/MangaFlow"
BASE = "f43ac71"

def make(title, body):
    r = subprocess.run(["gh", "issue", "create", "-R", REPO, "-t", title, "-b", body,
                        "-l", "delegation"],
                       capture_output=True, text=True, encoding="utf-8")
    print(r.stdout.strip() or r.stderr.strip())

COMMON = f"""## Baseline
origin/master {BASE} (2026-09-12 sync).

## Hard constraints (all agents)
- Work ONLY inside your assigned worktree. First command inside it: `git checkout -B <branch> origin/master` (verify `git rev-parse origin/master` == {BASE}).
- File-exclusive scope: edit ONLY the files listed below. New native test files MUST be new files named `NativeIssue<NNN>Checks.cs` (never edit Program.cs — the lead wires registration at merge; never edit shared Native*Checks.cs files other agents may touch).
- Forbidden: apps/web, apps/api, docs/roadmap.md, docs/development-progress.md, Program.cs, git stash, master branch, force-push, rebase.
- Preserve UTF-8 for Chinese strings; never round-trip files through PowerShell `>` redirects.
- Tests must construct realistic failure conditions (fake HttpMessageHandler / fake state), not tautologies.
- Required command (from worktree root): `dotnet run --project apps/desktop/native-tests` — all green before handoff.
- Push your branch to origin; report: commit SHA, files changed, checks added, anything NOT RUN.
"""

make("[Delegation][Wave1-A] ApiClient query-traversal + upload timeout (#439 #442 #468 + #471 item4)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-a → fix/native-w1a-apiclient

## Files allowed
- apps/desktop/native/Services/ApiClient.cs
- apps/desktop/native/Views/SourceView.cs
- apps/desktop/native/Views/OutfitWorkspace.cs
- apps/desktop/native/Views/SceneWorkspace.cs
- apps/desktop/native-tests/NativeIssue439Checks.cs, NativeIssue442Checks.cs (new files)

## Tasks
1. #442 + #468 (duplicate pair): `Validate` rejects `..` anywhere in path+query; query values are URL-encoded data, so a project named `序章..终章` or scene filter `东京..雨` permanently blocks delete/load. Fix: apply the `..`/`:``/`//` traversal checks to the path portion only (split on first `?`), keep absolute/`:``/`/`//` rejection for the whole string. Checks: project name containing `..` delete request is sent with the encoded value; scene filter containing `..` loads.
2. #439: `UploadAsync` lacks the TaskCanceledException→TimeoutException translation that `SendOptionalAsync` has (30s HttpClient timeout); SourceView.ImportFileAsync swallows OperationCanceledException with zero feedback. Fix: translate in UploadAsync, surface the localized timeout notice in SourceView (never silent), and make OutfitWorkspace's sibling upload surface the Chinese timeout copy instead of raw English.
3. #471 item 4 (P4): SceneWorkspace 设为规范参考 does DELETE-old then POST-new non-atomically; POST failure leaves the reference unbound. Minimal fix: on POST failure, honest error is kept, but re-attempt must not require re-upload — either reorder (POST new first when possible) or clear local canonical binding state to match server truth on failure.

## Acceptance
- All behaviors above covered by new checks; `dotnet run --project apps/desktop/native-tests` green.
""")

make("[Delegation][Wave1-B] ScriptView + Models integrity (#426 #440 #448 #484)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-b → fix/native-w1b-scriptview

## Files allowed
- apps/desktop/native/Models.cs
- apps/desktop/native/Views/ScriptView.cs
- apps/desktop/native-tests/NativeIssue426Checks.cs, NativeIssue484Checks.cs (new files)

## Tasks
1. #484 (+#440 item 2): `Flag("deleted_at")` matches only JsonValueKind.True but the API sends null | ISO timestamp string. Add a string-form check (helper), set `Deleted` correctly; fixture with `deleted_at: "2026-09-01T00:00:00Z"` asserts Deleted == true.
2. #440 item 1: ScriptView RefreshVariants iterates asset.Variants unfiltered — archived variants selectable then 422 on save. Filter with the same deleted_at string check SceneWorkspace uses.
3. #448: scene/beat form save handlers have no reentrancy guard; double-click double-PATCHes with the same stale version → false 保存未完成 conflict dialog. Add busy/disable guard around the PATCH (handler-side; ViewKit.cs is owned by the lead — do not edit it).
4. #426 (P2): wardrobe multi-save loops SaveOutfitAssignment per dirty character, each re-PATCHes from the SAME stale scene snapshot → removals resurrected. Compute the final merged assignment map ONCE and send a single PATCH carrying the full final map (and the scene version so concurrent edits 409 instead of last-write-wins). Check: two-dirty-character save (one removal + one change) against a fake API capturing PATCH bodies → exactly one PATCH, removed character absent from body.

## NOT yours (Wave 2 owns them)
- ScriptView.Activate quiet/preserve mode for #428; draft guards outside the two save handlers.

## Acceptance
- New checks cover 1–4 with realistic fixtures; `dotnet run --project apps/desktop/native-tests` green.
""")

make("[Delegation][Wave1-C] Shell lifecycle (#411 #446 #470 #471 items2,3 #449 item1)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-c → fix/native-w1c-shell

## Files allowed
- apps/desktop/native/MainWindow.xaml.cs
- apps/desktop/native/Services/Preferences.cs
- apps/desktop/native/Services/NativeBackend.cs
- apps/desktop/native/Views/HomeView.cs
- apps/desktop/native-tests/NativeIssue411Checks.cs, NativeIssue470Checks.cs (new files)

## Tasks
1. #446: Preferences.Save (File.Move overwrite / WriteAllText) can throw out of async-void OnClosing/HideDock/ShowDock → process crash, graceful backend stop skipped. Mirror KeyValueStore.cs:82-85's catch (never crash over prefs failures) — wrap the Save call sites and/or add the catch inside Preferences.Save; optional one-line status notice.
2. #470: CreateProject submit cancelled by navigation is swallowed (`catch (OperationCanceledException) { }` HomeView.cs:518) while the server may have created the project → invited duplicate resubmit. On OCE: surface 提交结果未知，请刷新后确认 -style feedback and invalidate dashboard/projects cache before the version check (or use a write token not cancelled by Deactivate for the POST).
3. #471 item 2: HomeView drawer error written to a collapsed drawer after 取消 → zero visible feedback. Route the failure to State.Status when the drawer is closed (or disable 取消/✕ while creating).
4. #471 item 3: MainWindow F5 `catch (Exception) { }` — set state.Error/status on LoadDashboardAsync failure instead of silently keeping stale metrics.
5. #449 item 1: startup boot-hang (NativeBackend 35s READY wait → TimeoutException) reuses the submit-conflict copy 提交操作可能已被服务接收… — give the startup timeout its own ErrorText copy appropriate for a boot hang (nothing was submitted).
6. #411: NativeBackend.StopAsync has no kill escalation — 40s wait TimeoutException is uncaught so process/started reset is skipped → wedged forever; every reconnect re-fails. Catch timeout/cancel in StopAsync → escalate `process.Kill(entireProcessTree: true)`, reset process/started in finally so Reconnect can retry a fresh spawn. Pin the escalation decision table as a unit check where testable.

## NOT yours (Wave 2 owns them)
- #428 reconnect ConfirmLeaveAsync semantics; OnClosing dirty-prompt for other views.

## Acceptance
- New checks where unit-testable (Preferences failure no-throw, OCE feedback, StopAsync escalation table); `dotnet run --project apps/desktop/native-tests` green.
""")

make("[Delegation][Wave1-D] WorkflowView switch race (#427)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-d → fix/native-w1d-workflow

## Files allowed
- apps/desktop/native/Views/WorkflowView.cs
- apps/desktop/native-tests/NativeIssue427Checks.cs (new file)

## Tasks
#427 (P2): rapid A→B→C selector switches write one workflow's graph into another (async-void handler suspends at SaveNowAsync before workflowId = id; LoadWorkflowAsync has no request-version guard; queued flush PATCHes with targetWorkflowId null → uses whatever id is current; equal versions pass the backend CAS silently).
- LoadWorkflowAsync: add a request seq/token (ScriptView scriptLoadVersion pattern, ScriptView.cs:126-137) and discard stale responses.
- Serialize workflowId assignment before any awaited flush in the switch handler.
- ScheduleSave: validate the armed graph actually belongs to the armed workflow (or re-snapshot at save time).
- Activate: selector handler currently fires a double load — exactly one load per Activate.

## Checks
Two overlapping LoadWorkflowAsync calls with reversed completion order → final canvas/workflowId coherent, no PATCH issued with a mismatched (graph, id) pair; Activate → exactly one load.

## NOT yours (Wave 2 owns)
- WorkflowView.Activate quiet/preserve mode for #428.

## Acceptance
`dotnet run --project apps/desktop/native-tests` green with new checks.
""")

make("[Delegation][Wave1-E] Settings forms dirty-guard (#469 #471 item1 #429 item2)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-e → fix/native-w1e-settings

## Files allowed
- apps/desktop/native/Views/ProjectSettingsView.cs
- apps/desktop/native/Views/SettingsView.cs
- apps/desktop/native-tests/NativeIssue469Checks.cs (new file)

## Tasks
1. #469: ProjectSettingsView is an editable form with no dirty flag and no ConfirmLeaveAsync override — F5 / section switch / Ctrl+K project switch / window close all silently discard edits (incl. the typed delete-confirmation name). Implement dirty tracking (Dirty() seam exists at :305) + ConfirmLeaveAsync override; RefreshAsync prompts (or skips) when dirty. Every other editable view already has this (SourceView.cs:462, AssetsView.cs:356, WorkflowView.cs:1800, ScriptView.cs:435, StoryboardView.cs:1793) — mirror their pattern.
2. #471 item 1: navigation away mid-PATCH/mid-DELETE cancels the call; if the server applied it the local version was never advanced → immediate re-save 409s until F5, and a completed delete leaves the page up. On OCE surface a refresh hint (提交结果未知，请刷新后确认-style) rather than silence.
3. #429 item 2: SettingsView.RefreshAsync unconditionally reloads+renders — radio/concurrency edits and the half-typed ConnectionPanel API key wiped on refresh. Add a dirty check mirroring the other views' RefreshAsync guard.

## Checks
Dirty form + each of the four leave paths → confirm fires; clean form → no prompt. SettingsView refresh with dirty ConnectionPanel → preserved.

## Acceptance
`dotnet run --project apps/desktop/native-tests` green with new checks.
""")
