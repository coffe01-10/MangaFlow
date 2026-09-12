# -*- coding: utf-8 -*-
import subprocess
import sys

def create(title, body):
    result = subprocess.run(
        ["gh", "issue", "create", "--repo", "coffe01-10/MangaFlow",
         "--title", title, "--body", body, "--label", ""],
        capture_output=True)
    out = (result.stdout or b"").decode("utf-8", "replace") + (result.stderr or b"").decode("utf-8", "replace")
    print(title[:60], "->", out.strip())
    if result.returncode != 0:
        sys.exit(1)

found = "**Found:** web review round 2026-09-12 (three parallel read-only reviewers over apps/web at master 57aaca3); lead spot-verified the P2 anchors against the code."

create(
    "[P2][Web Red Team] React Query invalidation gaps: storyboard saves leave generation-workbench stale (cancelled job + 409 loop), asset blocker-fixes leave readiness stale, candidate labels lag",
    found + """
## Item 1 (P2) — storyboard saves never invalidate the generation workbench
`components/storyboard-editor/index.tsx:218-221` (`refresh()`), consumed by `savePanel`/`saveDialogue`/`addDialogue`/`removeDialogue`/`updateLayout`/`geometrySave`: invalidates only `["storyboard", pageId]` and `["pages", chapterId]`. Every save bumps `page.storyboard_version` server-side, but `["generation-workbench", pageId]` (staleTime 15s, providers.tsx:11) keeps the old version. The generate desk prefers the workbench page (`use-generation-workspace.ts:91`) and submits its version: 抽卡 queues a candidate the worker cancels (`StaleStoryboardVersionError`, page_generate.py:435-438 — one wasted FAILED card), and 沿用并重新检查 (`keepSelectedCandidate`, use-generation-workspace.ts:393-395) hard-409s in a loop (generation.py:525-526) because its `onError` invalidates nothing. The codebase's own guard for this exact hazard is the sibling `assignOutfit` (`project-workspace.tsx:267-274`, comment: 「不失效 pages 和 generation-workbench 时，工作台仍持旧 storyboard_version，首次抽卡即 409」). The stale banner also prints the wrong version (generate-section.tsx:196).
**Fix surface:** add `["generation-workbench"]` (+ `["candidates"]` for card `version_state` labels) to `refresh()` and `geometrySave.onSuccess`, mirroring `assignOutfit`; optionally a 409-onError invalidation on `keepSelectedCandidate`.

## Item 2 (P3) — blocker-fixing asset mutations leave 生成 disabled on stale readiness
`components/project-workspace/use-assets-workspace.ts:354-357` (`updateOutfit`), same class at `:195-206` (upload+bind), `:260-269` (`bindExistingCharacterReference`), `:441-445` (`updateStyleMode`): invalidate only their own list keys, never `["generation-workbench"]`. Readiness (`page_readiness.py:244-304`: MISSING_CHARACTER_REFERENCE / MISSING_OUTFIT_REFERENCE / STYLE_NOT_COLOR) gates the 生成 button (`generate-section.tsx:178`), so fixing a blocker leaves the desk wrong-blocked for the 15s window with no self-heal poll. Sibling `deleteOutfit` (`:374-384`) DOES invalidate the workbench (with comment) — inconsistent set.
**Fix surface:** add `["generation-workbench"]` invalidation to the four `onSuccess` hooks, matching `deleteOutfit`/`deleteAsset`/`reclassifyAsset`.

## Item 3 (P4) — candidate version_state labels lag after assignOutfit
`components/project-workspace.tsx:267-274`: `assignOutfit` bumps `storyboard_version` but not `["candidates"]`; card `version_state` labels (generate-section.tsx:258, query `["candidates", viewedBatch?.id]`) contradict the invalidated workbench banner for the freshness window.
**Fix surface:** add `["candidates"]` to `assignOutfit.onSuccess` (fold into the Item-1 fix).

Each item independent; existing test `use-assets-workspace.test.tsx:154-169` only asserts success — add invalidation assertions (spy on queryClient.invalidateQueries).""",
)

create(
    "[P3][Web Red Team] Error-surface family: silent-empty panels, missing retries, unrecoverable partial failure, English 422 text",
    found + """
Ten verified items in the silent-degradation class (each independent; severity per item):
1. **P3** `components/project-workspace/character-package-workspace.tsx:259-263,:657` — package detail query failure renders the right pane as `null` (no error, no retry); sibling `character-package-picker.tsx:48-50` handles the same query's error.
2. **P3** `components/workflow-studio.tsx:234-247,:671-679` — default-workflow pair via `Promise.all`: partial success is unrecoverable; 重试创建 only refetches, the effect's `.length` guard (`:235`) permanently blocks re-creating the missing 整章导出流程 (no manual create button exists). Use `Promise.allSettled` + create only missing ones.
3. **P4** `components/asset-production-panel.tsx:112-117,:207-208,:248-253,:344` — concept/style candidate fetch failures fall through to 「第一张草稿生成后会实时出现在这里…」 empty copy (error misreported as nothing-generated, invites re-running paid generation).
4. **P4** `components/project-workspace/assets-section.tsx:223` — 服装穿着图实时结果 grid silently empty when the asset-candidates query fails; mirror generate-section.tsx:279 error card + 重试读取.
5. **P4** `components/project-workspace/jobs-section.tsx:90` — only workspace list error without a 重试 button (polling stops on error; siblings all have one, e.g. storyboard-section.tsx:81).
6. **P4** `app/page.tsx:225-226` — dashboard error panel has no retry action (entry page).
7. **P4** `components/workflow-studio.tsx:222,:721,:795` — versions fetch failure shows 「尚未发布」 (false claim about publish state); `workflow-node-types` catalog failure empties NODE LIBRARY silently (`:211,:726`).
8. **P4** `components/provider-management.tsx:26,37` — `models.data ?? EMPTY_MODELS` swallows catalog failures; capability/counts silently degrade (providers.isError IS handled at :70-77).
9. **P4** `lib/api.ts:1392-1401,:1408-1417` — FastAPI 422 pydantic English `msg` shown verbatim (`name：Field required`) in an otherwise Chinese UI; map common pydantic v2 message types to Chinese with raw fallback.
10. **P4** `components/usage/usage-dashboard.tsx:84-87,:102-111,:180-183` — facet/project query failures render selects with only 全部 (filters silently narrow); add inline 「筛选选项读取失败」+retry.

**Fix surface:** additive error branches mirroring each file's existing guarded sibling; no behavior change on success paths.""",
)

create(
    "[P2][Web Red Team] Dirty-guard family: character chip/package spec/scene modal discard drafts; settings pages unguarded; style palette remount reset",
    found + """
Baseline guarded siblings: `script-editor.tsx` / `storyboard-editor/index.tsx` / `source-section.tsx` / `local-edit-workspace.tsx` (beforeunload + anchor capture + switch confirms), `character-package-workspace.tsx:131-149` (re-sync only when NOT dirty). All findings verified line-by-line:
1. **P2** `components/project-workspace/assets-section.tsx:213` — clicking a different character chip runs `resetOutfitForm()` (bare state wipe, use-assets-workspace.ts:453) and overwrites `editCharacterName/Aliases/LockedFeatures/ForbiddenChanges` with the new character's server values; `CharacterConceptPanel key={boundCharacter.id}` (`:217`) remounts and loses concept drafts — three concurrent drafts destroyed with no confirm. Mirror script-editor.tsx:146-148.
2. **P2** `components/project-workspace/character-package-workspace.tsx:803` — `PackageSpecEditor key={`spec:${pkg.id}`}` remounts on character switch (click `:634` or ArrowUp/Down `:551-560`), discarding up to 11 typed spec fields; `specDirty` (`:126`) is never lifted (no `onDirtyChange`). Lift the flag and confirm before switching.
3. **P2** `components/project-workspace/scene-modal.tsx:63,:34-38` + consumers `scene-workspace.tsx:819,863,850,910` — scene create/edit/variant modals close on backdrop click and Escape with no dirty check; half-typed long forms discarded.
4. **P3** `app/projects/[id]/settings/page.tsx:22,84-87,106` — project-settings `localDraft` has no dirty flag, no beforeunload, no anchor guard; 返回工作区 Link and reload drop unsaved mode/resolution/concurrency edits.
5. **P3** `app/settings/page.tsx:44-46`, `components/provider-settings/connection-panel.tsx:59-63`, `connection-advanced.tsx:33-38`, `provider-card.tsx:55`, `provider-group.tsx:54`, `provider-management.tsx:38-44` — collapsing a card/group or typing in the search filter unmounts ConnectionPanel and destroys the half-typed API key / manual-model / JSON drafts with no confirm; no beforeunload on the page.
6. **P3** `components/project-workspace/assets-section.tsx:267` + `use-assets-workspace.ts:99-103` + `asset-production-panel.tsx:241,342-343` — `StyleProductionPanel key={id}:{version}` remounts on any version bump; the 2500ms ANALYZING poll means 重新提议色板's completion silently replaces in-progress palette edits beside it. Drop version from the key or gate the re-sync on `!paletteDirty` (the PackageSpecEditor pattern).
7. **P4** `components/storyboard-editor/index.tsx:731` (`dialogue-card.tsx:42`) — new-bubble 取消 discards typed `target_text` without confirm (in-family inconsistency: `:718` confirms other exits).
8. **P4 a11y** `components/project-workspace/jobs-section.tsx:76` — clickable `<article>` row without role/tabIndex/keyboard (mitigated by the 查看结果 button).
9. **P4 a11y** `components/storyboard-editor/panel-inspector.tsx:167` — clickable div bubble slot without role/tabIndex/keyboard (layer list has real buttons).
10. **P4 a11y** `components/project-workspace/inspection-panel.tsx:27` — async inspection status has no `aria-live`/`role="status"` (siblings: storyboard-editor/index.tsx:632, project-workspace.tsx:407).

**Fix surface:** reuse the existing guard patterns (beforeunload + anchor capture + confirm helpers); each item independent.""",
)
print("DONE")
