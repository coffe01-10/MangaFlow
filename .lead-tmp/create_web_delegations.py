# -*- coding: utf-8 -*-
import subprocess
import sys

def create(title, body):
    result = subprocess.run(
        ["gh", "issue", "create", "--repo", "coffe01-10/MangaFlow",
         "--title", title, "--body", body],
        capture_output=True)
    out = (result.stdout or b"").decode("utf-8", "replace") + (result.stderr or b"").decode("utf-8", "replace")
    print(title[:60], "->", out.strip())
    if result.returncode != 0:
        sys.exit(1)

header = """## Hard constraints (all agents)
- Work ONLY inside your assigned worktree. First: `git checkout -B <branch> origin/master` (verify origin/master is the wave baseline; do NOT fetch).
- File-exclusive scope: edit ONLY the files listed below. Another web agent owns a disjoint file set in parallel — any file outside your whitelist is a hard conflict.
- Forbidden: apps/desktop, apps/api, docs/roadmap.md, docs/development-progress.md, git stash, master, force-push, rebase.
- Preserve UTF-8 (Chinese strings); never round-trip files through PowerShell `>` redirects.
- Web tests: colocated `*.test.tsx`/`*.test.ts` (Vitest + Testing Library). Tests must construct realistic failures (spy on queryClient, fire the exact user sequence) and be mutation-verified (scratch-revert the fix → test fails).
- Required commands: `npx vitest run <your colocated tests>` while iterating; `npm run check` green before reporting (ESLint/Vitest/web build). Do NOT run Playwright/e2e (reserved window; report as NOT RUN).
- Push branch; report SHA, files, tests, NOT RUN.

"""

create(
    "[Delegation][Web-W1] Query invalidation fixes (#544 + #546 items 7/9)",
    header + """## Worktree / branch
D:/自媒体/漫画工作流-wt-b → fix/web-w1-invalidations

## Files allowed
- apps/web/components/storyboard-editor/index.tsx (+ dialogue-card.tsx if needed for item 7)
- apps/web/components/storyboard-editor/panel-inspector.tsx
- apps/web/components/project-workspace.tsx
- apps/web/components/project-workspace/use-assets-workspace.ts
- apps/web/components/project-workspace/use-generation-workspace.ts
- apps/web/components/generate-section.tsx
- colocated test files for the above (new or existing under apps/web/components/**)

## Tasks
1. #544 item1 (P2): storyboard `refresh()` + `geometrySave.onSuccess` must also invalidate `["generation-workbench"]` and `["candidates"]` (mirror the guarded sibling `assignOutfit` in project-workspace.tsx:267-274 — read its comment first); fix the stale banner version source in generate-section.tsx:196; add a 409-onError invalidation to `keepSelectedCandidate` (use-generation-workspace.ts:393-395) per the settings-page pattern.
2. #544 item2 (P3): add `["generation-workbench"]` invalidation to `updateOutfit`/`upload`+bind/`bindExistingCharacterReference`/`updateStyleMode` onSuccess (use-assets-workspace.ts:195-206,:260-269,:354-357,:441-445), matching `deleteOutfit`.
3. #544 item3 (P4): add `["candidates"]` to `assignOutfit.onSuccess`.
4. #546 item7 (P4): confirm before discarding a typed new-bubble draft on 取消 (index.tsx:731; only when `target_text` non-empty).
5. #546 item9 (P4 a11y): panel-inspector.tsx:167 bubble slot — role="button" + tabIndex + Enter/Space, or remove the onClick and rely on the layer list.

## Acceptance
- Tests assert the invalidation sets (spy on `queryClient.invalidateQueries`, exact query keys) for: storyboard save paths, the four asset mutations, keepSelectedCandidate 409 onError. Extend use-assets-workspace.test.tsx:154-169.
- Mutation-verified at least: revert the generation-workbench invalidation in refresh() → a test fails; revert one asset-mutation invalidation → a test fails.
- `npm run check` green. Report SHA/files/tests/NOT RUN.""",
)

create(
    "[Delegation][Web-W2] Error surfaces + dirty guards (#545 + #546 items 1-6,8,10)",
    header + """## Worktree / branch
D:/自媒体/漫画工作流-wt-c → fix/web-w2-errorsurfaces

## Files allowed
- apps/web/components/project-workspace/assets-section.tsx
- apps/web/components/project-workspace/character-package-workspace.tsx
- apps/web/components/project-workspace/scene-modal.tsx
- apps/web/components/project-workspace/scene-workspace.tsx
- apps/web/components/project-workspace/jobs-section.tsx
- apps/web/components/project-workspace/inspection-panel.tsx
- apps/web/components/asset-production-panel.tsx
- apps/web/components/workflow-studio.tsx
- apps/web/components/provider-management.tsx
- apps/web/components/provider-settings/** (connection-panel.tsx, connection-advanced.tsx, provider-card.tsx, provider-group.tsx)
- apps/web/components/usage/usage-dashboard.tsx
- apps/web/app/page.tsx
- apps/web/app/settings/page.tsx
- apps/web/app/projects/[id]/settings/page.tsx
- apps/web/lib/api.ts (ONLY the formatValidationError localization in #545 item9)
- colocated test files for the above

## Tasks
#545 (error-surface family, items 1-10 as written in the issue — follow its file:line evidence):
1 (P3) package detail pane error+retry branch; 2 (P3) workflow partial-failure recovery via Promise.allSettled + create-missing-on-retry; 3-10 (P4) the silent-empty/retry/English-422 fixes as specified.
#546 (dirty-guard family):
- item1 (P2): character-chip switch confirm covering outfit form + character editor + concept panel drafts (assets-section.tsx:213).
- item2 (P2): lift PackageSpecEditor `specDirty` via onDirtyChange; confirm before character switch, click AND arrow-key paths (character-package-workspace.tsx:634,:551-560,:803).
- item3 (P2): scene modal backdrop/Escape/取消 dirty confirm (scene-modal.tsx + scene-workspace.tsx consumers).
- item4 (P3): project-settings dirty guard (beforeunload + anchor capture, pattern from script-editor.tsx:85-109).
- item5 (P3): system-settings tree — confirm-gate card/group collapse + search-driven unmount when a panel is dirty; beforeunload on app/settings/page.tsx.
- item6 (P3): style palette remount — gate re-sync on `!paletteDirty` (asset-production-panel.tsx) or drop version from the key (assets-section.tsx:267).
- item8 (P4 a11y): jobs-section.tsx:76 row keyboard access.
- item10 (P4 a11y): inspection-panel.tsx:27 role="status" aria-live="polite".

## Acceptance
- Every fixed item gets a colocated test that constructs the real failure (e.g. render detail pane with a rejecting query → assert error UI + retry calls refetch; character switch with dirty state → assert confirm consulted and drafts retained on decline; workflow partial failure → assert retry creates only the missing one).
- Mutation-verified at least: P2 items 1/2/3 (revert the confirm → test fails) and #545 items 1/2.
- `npm run check` green. Report SHA/files/tests/NOT RUN.""",
)
print("DONE")
