# -*- coding: utf-8 -*-
import subprocess
import sys

body = """## Hard constraints (all agents)
- Work ONLY inside your assigned worktree. First: `git checkout -B <branch> origin/master` (verify origin/master is the wave-3 baseline; do NOT fetch).
- File-exclusive scope: this wave grants you EXCLUSIVE ownership of the native tree listed below (no other native agent is in flight). Edit ONLY the files listed below. New native test files must be NEW files named `NativeIssue<NNN>Checks.cs` with the repo's self-contained STA pattern.
- The ONLY permitted edit to NativeInteractionChecks.cs is deleting the single line fragment `Cache = new ApiCache(), ` inside `Context(ApiClient api)` (~line 65) so the project compiles; the RunIsolated registration chain (line ~53) must remain byte-identical — the lead wires `NativeIssue441Checks.Run()` after merge. Program.cs is forbidden (verified: it has no ApiCache reference).
- Forbidden: apps/web, apps/api, apps/desktop/shell-core, docs/roadmap.md, docs/development-progress.md, git stash, master, force-push, rebase.
- Preserve UTF-8 (Chinese strings); never round-trip files through PowerShell `>` redirects.
- Tests must construct realistic failures; mutation-verify (scratch-revert) that each fails on unfixed code.
- Required: `dotnet run --project apps/desktop/native-tests` green; also run `dotnet run --project apps/desktop/native-tests -- --render <PRIVATE-OUTPUT-DIR-UNDER-YOUR-WORKTREE>` with a private output dir (shared %TEMP% default collides across concurrent runs).
- Push branch; report SHA, files, tests, NOT RUN.

## Worktree / branch
D:/自媒体/漫画工作流-wt-d → fix/native-w3-apicache

## Background (#441, P3)
`Services/ApiCache.cs` is a dead React-Query-style abstraction: `entries` is always empty (zero `FetchAsync`/`Entry` call sites outside the class), zero `Invalidated` subscribers — so all 37 `Cache.Invalidate(...)` calls across the Views are decorative no-ops. Freshness works only because every mutator ALSO does an explicit reload. Decision (lead): DELETE the inert abstraction (honest small fix per the issue) and pin the real guarantee — "a mutation is always followed by an explicit reload" — with contract checks. Do NOT attempt the wire-through-FetchAsync alternative.

## Files allowed
- apps/desktop/native/Services/ApiCache.cs — delete `ApiCache` + `QueryEntry` classes; KEEP `WorkspaceContext` in this file, removing only its `Cache` property (and the now-unused usings).
- apps/desktop/native/Views/ViewKit.cs — remove the `WorkspaceView.Cache` helper property only.
- apps/desktop/native/Views/*.cs (AssetsView, GenerateView, HomeView, LibraryView, LocalEditWindow, ProjectSettingsView, ScriptView, SettingsView, SourceView, StoryboardView) — remove the 37 `Cache.Invalidate(...)` statements (4/7/2/2/2/2/5/2/5/6 per file at baseline). The surrounding explicit reloads (`LoadAsync`/`LoadXxxAsync` calls after mutations) must remain byte-identical — this refactor is strictly behavior-preserving on the reload paths.
- apps/desktop/native/MainWindow.xaml.cs — remove `Cache = new ApiCache(), ` from the WorkspaceContext construction in ActivateCurrentViewAsync (~line 267).
- apps/desktop/native-tests/NativeIssue441Checks.cs (new) — see acceptance.
- apps/desktop/native-tests/*.cs EXCEPT NativeInteractionChecks.cs and Program.cs — mechanical removal of `Cache = new ApiCache(), ` from Context constructions (17 files: NativeConsistencyChecks, NativeDockChecks, NativeIssue426/427/428/429/439/469/470/484/485Checks, NativeJobsChecks, NativeStateMatrixChecks, NativeStoryboardChecks, NativeStoryboardEditChecks, NativeWorkflowChecks, NativeWorkflowRunChecks). One line per construction site; nothing else in these files may change.

## Acceptance
1. `git grep -nE "ApiCache|QueryEntry|Cache\\.Invalidate" -- apps/desktop` returns NOTHING at your SHA.
2. NativeIssue441Checks.cs pins the mutation→reload contract on at least three distinct real flows (suggested: GenerateView accept-candidate → workbench reload; LibraryView delete → list reload; JobsView retry or stop → jobs reload — verify the actual reload call in each flow at baseline and assert a fresh read is issued after the mutation, counting requests on a fake handler). Mutation-verified: removing the explicit reload in at least one pinned flow must turn the check red; report which flows you mutation-verified.
3. Both required commands green at your SHA (the default run covers the compile after your NativeInteractionChecks.cs one-line fix).
4. Report lists: SHA, per-file change counts, the three pinned flows, mutation results, NOT RUN boundaries.

## Known risks (lead review focus)
- The temptation to "improve" reload semantics while touching those lines — any behavior change beyond deletion is a review blocker.
- Windows mixed-encoding output: verify `git diff` shows no mojibake in Chinese strings before pushing.
- Cleanup ownership: if the render run leaves artifacts under your worktree, keep them out of the commit.
"""

result = subprocess.run(
    ["gh", "issue", "create", "--repo", "coffe01-10/MangaFlow",
     "--title", "[Delegation][Wave3-A] Delete inert ApiCache and pin the mutation-reload contract (#441)",
     "--body", body],
    capture_output=True)
print((result.stdout or b"").decode("utf-8", "replace"), (result.stderr or b"").decode("utf-8", "replace"))
sys.exit(result.returncode)
