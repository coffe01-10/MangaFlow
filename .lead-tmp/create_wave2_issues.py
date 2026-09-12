import subprocess

REPO = "coffe01-10/MangaFlow"

def make(title, body):
    r = subprocess.run(["gh", "issue", "create", "-R", REPO, "-t", title, "-b", body,
                        "-l", "delegation"],
                       capture_output=True, text=True, encoding="utf-8")
    print(r.stdout.strip() or r.stderr.strip())

COMMON = """## Hard constraints (all agents)
- Work ONLY inside your assigned worktree. First: `git checkout -B <branch> origin/master` (verify origin/master is the wave-2 baseline; do NOT fetch).
- File-exclusive scope: edit ONLY the files listed below. New native test files must be NEW files named `NativeIssue<NNN>Checks.cs` with the repo's self-contained STA pattern; NEVER edit Program.cs or NativeInteractionChecks.cs (lead wires registration); never edit shared check files.
- Forbidden: apps/web, apps/api, docs/roadmap.md, docs/development-progress.md, git stash, master, force-push, rebase.
- Preserve UTF-8 (Chinese strings); never round-trip files through PowerShell `>` redirects.
- Tests must construct realistic failures; mutation-verify (scratch-revert) that each fails on unfixed code.
- Required: `dotnet run --project apps/desktop/native-tests` green; also run `dotnet run --project apps/desktop/native-tests -- --render <PRIVATE-OUTPUT-DIR-UNDER-YOUR-WORKTREE>` with a private output dir (shared %TEMP% default collides across concurrent runs).
- Push branch; report SHA, files, tests, NOT RUN.
"""

make("[Delegation][Wave2-G] Draft-preservation family (#428 + #429 items 1,3,4 + #449 item3)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-a → fix/native-w2g-drafts

## Files allowed
- apps/desktop/native/MainWindow.xaml.cs
- apps/desktop/native/Views/ScriptView.cs
- apps/desktop/native/Views/StoryboardView.cs
- apps/desktop/native/Views/WorkflowView.cs
- apps/desktop/native/Views/GenerateView.cs
- apps/desktop/native/Views/LocalEditWindow.cs
- apps/desktop/native-tests/NativeIssue428Checks.cs, NativeIssue429Checks.cs (new)

## Tasks
1. #428 (P2): Reconnect (重新连接) → ConnectAsync → OpenProjectAsync(same project) same-id branch (~:430) skips ConfirmLeaveAsync AND Deactivate → ActivateCurrentViewAsync → every view Activate() full-reloads and destroys unsaved script forms / storyboard drafts / director drafts. The #341 fix (1345b5d) only patched RefreshAsync. FIX: route the reconnect/same-id path through the ConfirmLeaveAsync + quiet/preserve seam — either OpenProjectAsync same-id honors ConfirmLeaveAsync and calls a quiet Activate (views accept a preserve mode that skips destructive reload when drafts exist), or Activate gains a preserve-drafts parameter mirroring StoryboardView.SelectPageAsync(target, preserveDrafts:false)'s existing flag. Mirror the 1345b5d RefreshAsync tests at the Activate entry point.
2. #429 item1: GenerateView — PollTick already skips rebuild when pane.HasDraft (~:1400-1403), but RefreshAsync→LoadWorkbenchAsync renders when data changed (~:1417-1423, :98-106) and the chapter-selector handler has no ConfirmLeaveAsync → typed director command dropped. Route both through the HasDraft/ConfirmLeave seam (predicate already exists — reuse it).
3. #429 item3: StoryboardView LoadPagesAsync (~:389-413) has no epoch guard (pageLoadVersion covers only SelectPageAsync) — slow chapter-A pages response completing after B flips pages/canvas back to A while the selector shows B. Add an epoch to LoadPagesAsync mirroring the existing pattern.
4. #429 item4: LocalEditWindow close with groupId==0 (~:319-326) discards drawn regions + instruction with no guard — add a dirty check to OnClosing mirroring StoryboardView's leave confirm.
5. #449 item3: LocalEditWindow (~:146) surfaces raw English HttpRequestException.Message for image load failures — route through the localized error mapping (status-code + Chinese fallback) consistent with the app's error contract.

## Acceptance
Dirty view + reconnect → confirm fires / drafts preserved; GenerateView refresh/chapter-switch with draft → preserved; StoryboardView stale chapter response discarded; LocalEditWindow close with regions → confirm; all new checks mutation-verified; both required commands green.
""")

make("[Delegation][Wave2-H] Package pane busy guards (#485 + #486 item4)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-b → fix/native-w2h-packages

## Files allowed
- apps/desktop/native/Views/CharacterPackagePane.cs
- apps/desktop/native/Views/CharacterPackageDetails.cs
- apps/desktop/native-tests/NativeIssue485Checks.cs (new)

## Tasks
1. #485 (P3): four mutating handlers lack the busy/disable guard that SaveSpec (:358) and Change (:117) already have: Create package (:93-101, double-click → false 创建失败 409 toast after success), Derive version (:131-145, duplicate drafts/false 派生失败), Publish (:385-399, concurrent SaveSpec can complete after the publish snapshot froze → published version missing just-saved edits), Activate published version (:436-447, double-click → false error on expected_published_version_id). Apply the busy/IsEnabled=false pattern to all four; publish must serialize against (or refuse during) an in-flight SaveSpec.
2. #486 item4 (P4): CharacterPackageDetails.Compare (:193) `await Compare(); ShowDialog()` — during the multi-second fetch 对比历史 stays enabled → second click queues a second modal. Disable the button while Compare runs.

## Acceptance
Double-click checks → single POST per handler; publish-during-save serialized or refused; compare re-entrancy guarded; mutation-verified; both required commands green.
""")

make("[Delegation][Wave2-I] Silent-degradation P4 cluster (#486 items 1,2,3 + #449 item2)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-c → fix/native-w2i-silent

## Files allowed
- apps/desktop/native/Views/StyleProductionCard.cs
- apps/desktop/native/WorkspaceState.cs
- apps/desktop/native/Models.cs
- apps/desktop/native/Controls/Ui.cs
- apps/desktop/native-tests/NativeIssue486Checks.cs (new)

## Tasks
1. #486 item1: StyleProductionCard dead-batch latch (:150-152,:166) — batch created but candidates POST fails → pendingBatch kept forever; every 生成 1K 风格测试图 click re-targets the dead batch id with an opaque error until app restart. FIX: clear pendingBatch when the batch's candidates are confirmed absent/expired, or add a retry-through-new-batch path when the batch is unusable.
2. #486 item2: WorkspaceState (:54-55) + Models.cs (:59-68) dashboard card record equality excludes ModeLabel/Resolution/NextSection/NextLabel (bound by HomeView.cs:281) → workflow mode/resolution change leaves the dashboard cover stale. FIX: include the displayed fields in the equality (or compare explicitly).
3. #486 item3: Controls/Ui.cs Lightbox (:658-673) `catch (Exception) { }` → blank dark window forever on full-size load failure while the thumbnail path shows 图片加载失败. Surface the failure with error/retry like ImageBox.
4. #449 item2: image surfaces bypass the ApiClient error-detail contract — Ui.cs:318 EnsureSuccessStatusCode (raw status, backend detail unreachable); route through (or mirror) the shared error mapping with a localized status-code fallback.

## Acceptance
Dead-batch recovery covered; dashboard equality covers displayed fields; Lightbox failure visible with retry; image error surfaces carry localized copy; mutation-verified; both required commands green.
""")

make("[Delegation][Wave2-J] Single-instance/cross-client gates (#410)",
     COMMON + """
## Worktree / branch
D:/自媒体/漫画工作流-wt-d → fix/native-w2j-gates

## Files allowed
- apps/desktop/native/App.xaml.cs
- apps/desktop/scripts/start-native.ps1
- apps/desktop/src-tauri/src/protocol.rs
- apps/desktop/native-tests/NativeIssue410Checks.cs (new)

## Tasks
#410 (P3): single-instance/cross-client gates are keying-blind.
1. WPF mutex name hashes the LEXICAL user-data path (App.xaml.cs ~:52-55) — subst drives, junctions and 8.3 short names of the same directory hash differently → two WPF clients → two native hosts → two sidecars → two API servers on one SQLite. FIX: canonicalize the user-data path before hashing (resolve junctions/subst to the final path — GetFinalPathNameByHandle or equivalent; fall back to the lexical path on error), keeping the sha256+ToUpperInvariant scheme otherwise. Pin the canonicalization helper's unit semantics (pure function over path strings where possible; the Win32 resolve itself may need a smoke-level check with a created junction).
2. scripts/start-native.ps1 (-UserData ~:35) can point the WPF leg at the shell's user-data with nothing refusing the overlap → same two-servers-one-DB outcome. FIX: start-native.ps1 refuses (with a clear message) or overrides -UserData pointing inside the Tauri shell's app-local-data dir.
3. protocol.rs ~:394-397 stale comment ("the WPF leg shares this layout without a single-instance mutex" — the App mutex exists) — update to match reality.

## Acceptance
Canonicalization unit checks (junction alias → same mutex key; distinct dirs → distinct keys; resolve failure → lexical fallback); start-native.ps1 overlap refusal check (bash/powershell shim test if applicable); protocol.rs comment accurate; mutation-verified; both required commands green.
""")
