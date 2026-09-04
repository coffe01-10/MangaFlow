# V02-40 Director Command Journal Review

- Branch: `grok/v02-40-director-command-layer`
- Worktree: uncommitted (tracked edits + new files)
- Contract: `docs/v02-director-command-lineage-contract.md` (V02-03A) / Issue #94
- Tests run: `apps/api/.venv/Scripts/python.exe -m pytest tests/test_director_commands.py -q` → 8 passed
- Out of scope (not flagged as missing): V02-41 UI, V02-42 lineage, E8 PostgreSQL, E10 Worker, NL model parse, real providers

## Summary

Forward path for panel/dialogue commands is largely in the right shape: envelope `extra=forbid` + payload whitelist, independent `/director` mount (not workflow router), `command_id` unique on `(project_id, command_id)`, accept uses a nested savepoint so failed execute can persist journal error without keeping business writes (E6 actually checks this), and `regenerate_region` is structurally fail-closed (no provider/job/CandidateLineage import; always 422 before any paid call).

**There are P1 merge blockers.** Undo/redo is not a true inverse of execute:

1. `update_page_layout` redo restores from the undo row's `before_snapshot`, which is never copied, and falls back to `{panels:[], dialogues:[]}` — this deletes every panel/dialogue on the page.
2. Inverse payloads are only the preview-time payload keys. `apply_panel_fields` side effects (`outfits` / `expressions` / `character_presence`) are not captured, so `update_panel_cast` undo loses those fields.

E7 only exercises `update_panel_shot` and does not cover layout undo/redo, so both holes are green. Tests do not substitute for those paths.

Accept/propose transaction ownership for the forward path is acceptable: `apply_*` flush-only, layout called with `commit=False`, journal + business committed together. `update_page_layout(commit: bool = True)` remains a caller-owned-transaction footgun (P2). Scene writes were not extracted from `PATCH /scenes/{id}` (P2). Failed accept leaves `PREVIEWED`, so the same `command_id` can execute later (P2 idempotency).

## Issues

### Issue 1 -- Severity: P1
- File: apps/api/app/services/director_commands.py:773
- Description: `redo_command` is implemented as `undo_command` of an undo row. The layout branch copies `operation`/`payload` but **never copies `before_snapshot` onto the undo row**. Redo then calls `_restore_page_snapshot(db, page, row.before_snapshot or {"panels": [], "dialogues": []})`. After a successful layout accept+undo, the undo row has `before_snapshot is None`, so redo **deletes all current panels and dialogues and recreates none**. That is user-visible data loss on a contract undo/redo path. E7 never accepts/undoes `update_page_layout`, so the suite cannot catch this.
- Suggestion: On layout undo, snapshot the *post-execute* page onto the undo row (the state redo must restore), or reuse `_execute_operation` / `update_page_layout` for redo instead of snapshot-restore. Never default a missing snapshot to an empty page. Add an E7-style test: layout accept → undo restores prior panels/dialogues/ids → redo restores the post-layout page, not empty.
- Status: open

### Issue 2 -- Severity: P1
- File: apps/api/app/services/director_commands.py:315
- Description: Inverse commands are `{field: diff[field].before}` from **preview-time payload keys only** (`_inverse_from_diff`), then undo applies that dict through `apply_panel_fields`. Execute of `update_panel_cast` with `characters` also rewrites `character_presence` and filters `outfits`/`expressions` (storyboard_edits.py:103-115). Undo only puts `characters` back, then the same filter runs against the *current* (already stripped) outfits/expressions → those maps become `{}`. Same class of hole for `actions` (merge keeps extra keys from the forward write). Contract §5.3 requires an inverse that restores the pre-execute storyboard; this does not. E7 only undoes `shot_type`, which has no side effects.
- Suggestion: Capture a target-object field snapshot at execute time (or invert the post-`apply_*` dict, including side-effected keys). Undo must send the full restored field set, not the original payload keys. Test: cast change that drops an outfit/expression, undo, assert outfits/expressions/presence match pre-execute.
- Status: open

### Issue 3 -- Severity: P2
- File: apps/api/app/services/content_workflow.py:815
- Description: `update_page_layout` still owns `db.commit()` by default (`commit: bool = True`). Director accept correctly passes `commit=False` so journal + layout share one transaction, but any future nested caller that omits the flag will commit the outer unit of work (classic extra-commit that drops caller state). This is the opposite of `storyboard_edits.apply_*` (“flush but never commit”).
- Suggestion: Split a flush-only helper and keep commit in the PATCH route only; do not thread a default-True commit flag through the service.
- Status: open

### Issue 4 -- Severity: P2
- File: apps/api/app/services/storyboard_edits.py:167
- Description: `apply_scene_context` is a new write path, not an extraction of `PATCH /scenes/{scene_id}` (`apps/api/app/api/routes/sources.py:358`). Field lock / setattr / `scene.version += 1` / `mark_pages_for_review(..., reference_kind="scene")` are duplicated. Director additionally `mark_storyboard_changed` on every referencing page from the earliest (contract §4/§10 requires this); PATCH still does not. data-model.md claims “不新增第二条写路径”. Scene PATCH and director can now drift independently (locks, extra fields, invalidation).
- Suggestion: Extract the existing scene PATCH body into a flush-only helper; director should call that helper then apply the contract-required storyboard bump. Keep PATCH semantics unchanged except for the extracted commit.
- Status: open

### Issue 5 -- Severity: P2
- File: apps/api/app/services/storyboard_edits.py:142
- Description: PATCH dialogue used to always run `_validate_dialogue_speaker`, which maps empty/`None` to `None`. The extracted helper only validates when the value is truthy, so `speaker_character_id=""` is written through as an empty string. That is a PATCH semantic regression on the shared write path, not just a director quirk.
- Suggestion: Restore the old helper: missing/blank → `None`, otherwise `validate_character_ids`. Cover with a PATCH dialogue test.
- Status: open

### Issue 6 -- Severity: P2
- File: apps/api/app/services/director_commands.py:482
- Description: §4 whitelist for `update_scene_context` includes `background`. Preview drops it (`_preview_command` excludes `background`); execute writes scene fields except `background`, then only writes panel background if `target.panel_id` is set. Envelope target rules **forbid** `panel_id` on this operation (`OPERATION_TARGETS` is `{page_id, scene_id}`), so the panel branch is dead. Accepting a command whose payload contains `background` is a no-op for that field. `Scene` has no `background` column; silently ignoring a whitelist field is worse than 422.
- Suggestion: Either reject `background` on this operation (and drop it from the payload model) or require `panel_id` and write `Panel.background` via `apply_panel_fields`. Do not accept-and-ignore.
- Status: open

### Issue 7 -- Severity: P2
- File: apps/api/app/services/director_commands.py:677
- Description: Failed accept (422 metrics, 409 lock/cast, 422 regenerate) rolls back the savepoint, writes `row.error`, **leaves status `PREVIEWED`**, and commits. A later POST `/accept` on the same `command_id` will execute again if the page has changed. Contract §6.4: retry of a failed result must use a new `command_id` + `retry_of_command_id`, not re-run the same id. §6.2 only asks 409 version conflict to return to PREVIEWED (and that path cannot succeed later because `expected_version` is immutable).
- Suggestion: Terminalize failed execute as `FAILED` (or keep PREVIEWED only for VERSION_CONFLICT). Re-accept of FAILED/same id should replay the first error with `idempotent_replay: true`.
- Status: open

### Issue 8 -- Severity: P2
- File: apps/api/app/services/director_commands.py:530
- Description: Propose idempotency is **group_id** replay of *current* group state, not frozen first result. `DirectorCommandGroup.first_result` is never written. Same `command_id` in a different group is `409 command_id 已存在`, not HTTP 200 + first result. After accept, re-POST of the original group returns EXECUTED rows with `idempotent_replay: true`, which is not the first preview response. Unique key still prevents a second row; the contract wording is not met.
- Suggestion: Persist the first propose response in `first_result` (or equivalent) and return it on group/command_id replay. Duplicate `command_id` across groups should replay the original command/group, not 409. Catch `IntegrityError` on the command unique key the same way as the group key (avoid 500 on a race).
- Status: open

### Issue 9 -- Severity: P2
- File: tests/test_director_commands.py:365
- Description: E7 claims “撤销期间并发编辑 → SUPERSEDED”, but it undoes the **original** command after undo+redo already advanced `storyboard_version`. Original `storyboard_version_after` is stale before the PATCH, so SUPERSEDED would fire without the concurrent edit. The version-mismatch branch is real (execute → PATCH → undo original would hit it); this test does not isolate that. Layout undo/redo, cast undo, and scene execute are untested — the P1 holes above are invisible to the suite.
- Suggestion: Concurrent case: accept, PATCH another field (no undo), undo original → SUPERSEDED, storyboard unchanged by the failed undo. Add layout and cast inverse tests (Issue 1–2).
- Status: open

### Issue 10 -- Severity: P2
- File: tests/test_director_commands.py:410
- Description: E9 asserts 422 + `storyboard_version == 1` for missing mask and deleted parent. It does not assert absence of `GenerationJob`, `ModelCallAttempt`, or any lineage/candidate insert. Fail-closed is true in source (`_execute_regenerate` never reaches a provider and always 422s, including the “lineage not landed” path), but the test would still pass if a job row were created before the 422.
- Suggestion: After those accepts, assert zero jobs/attempts/new candidates. Optionally propose a valid mask+live parent and still assert 422 “禁止付费调用” with no job.
- Status: open

### Issue 11 -- Severity: P2
- File: apps/api/app/services/director_commands.py:264
- Description: Preview only checks ownership + `expected_version`. Character/outfit/lock/metrics/`update_page_layout` range checks run at accept. E3 therefore PREVIEWED (200) then accept 409. Contract §5.1 says PREVIEWED means the deterministic validator ran; failed commands should be annotated without blocking siblings. Today a PREVIEWED command is not guaranteed to be executable.
- Suggestion: Run the same validators in preview (dry-run or the existing 409/422 paths without persist) and mark those commands REJECTED with the reason, matching E3’s “复用现有校验” at the group boundary.
- Status: open

### Issue 12 -- Severity: P2
- File: apps/api/app/domain/director_commands.py:212
- Description: `RegenerateRegionPayload` adds `parent_candidate_id`, which is not in §4 (`instruction`, `target_regions`, `mask`, `model_alias`, `resolution`). Needed for the fail-closed parent check, but it is a whitelist expansion. `target.asset_id` is optional and unused as parent/mask identity.
- Suggestion: Keep parent identity out of payload unless the contract is amended; resolve parent from the page’s selected candidate (or an explicit, documented field). Reject unknown keys relative to the frozen table.
- Status: open

### Issue 13 -- Severity: P2
- File: apps/api/app/services/director_commands.py:327
- Description: Layout snapshot omits `Panel.version`, `bubble_regions`, page metrics, `selected_candidate_ack_version`, and `geometry_save_command`. Restore DELETE+INSERT recreates panels at `version=1` and does not `refresh_page_text_metrics`. Even a correct redo (Issue 1) would not round-trip PATCH-equivalent state. Snapshot restore is the contract-allowed inverse for shrinking layout, but it must be complete.
- Suggestion: Snapshot every panel/dialogue column plus page metrics/ack; after restore, refresh metrics and preserve panel versions. Bound snapshot size as §5.3 requires.
- Status: open

### Issue 14 -- Severity: P2
- File: apps/api/app/services/director_commands.py:128
- Description: Group status treats `{PREVIEWED, REJECTED}` as `PARTIALLY_ACCEPTED` because `REJECTED` is in `accepted_like` and `PREVIEWED` is pending. E5 asserts that after rejecting one command with the other still PREVIEWED. Contract diagram moves to PARTIALLY_ACCEPTED on 逐条接受, not on reject-only. `CommandStatus.ACCEPTED` is never assigned (PREVIEWED → EXECUTED).
- Suggestion: PARTIALLY_ACCEPTED only when at least one command is EXECUTED (or a real ACCEPTED) and some remain PREVIEWED. Align E5.
- Status: open

### Issue 15 -- Severity: nit
- File: apps/api/app/domain/director_commands.py:229
- Description: `command_id` / `command_group_id` only enforce length 36, not UUID format. `CommandStatus.ACCEPTED` is unused. `first_result` column is unused. Layout undo builds an envelope `undo_body` then ignores it for the snapshot path. Field undo parses an envelope then ignores `envelope_dict` and applies `payload` directly.
- Suggestion: Validate UUID shape; drop or use ACCEPTED/first_result; make undo a single `_execute_operation` on a persisted inverse row.
- Status: open

### Issue 16 -- Severity: nit
- File: apps/api/app/services/director_commands.py:589
- Description: `PAYLOAD_MODELS[...].model_validate(self.payload)` validates but does not replace `envelope.payload` with the parsed model dump. Harmless while `extra=forbid`; if a payload model later allows extras or coercions, execute would write the raw dict.
- Suggestion: Assign `self.payload = parsed.model_dump(exclude_none=True)` after validate.
- Status: open
