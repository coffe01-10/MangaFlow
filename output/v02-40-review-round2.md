# V02-40 Director Command Journal Review (Round 2)

Scope: previous P1 #1 (layout redo empty page), P1 #2 (cast undo drops outfits/expressions), and any new undo/redo data-loss P1. P2s from round 1 not restated unless they became P1.

## Summary

**P1 count: 0.** Previous P1s are fixed. No new undo/redo data-loss merge blocker.

P1 #1: layout undo now snapshots the **current** page onto `undo_row.before_snapshot` before restoring the original command’s snapshot. Redo is undo of that row, so it restores the post-execute page, not `{}`. Missing `before_snapshot`/`panels` is 409, not an empty default. `test_e7_layout_undo_redo_does_not_empty_the_page` fails if the copy is omitted or copies the wrong generation.

P1 #2: execute snapshots full `PANEL_RESTORE_FIELDS` (and dialogue/scene equivalents) **before** `apply_*`. Undo/redo use `restore_panel_snapshot` / setattr, not `apply_panel_fields`, so recast filtering cannot strip outfits/expressions. Undo row stores the post-execute snapshot in `inverse_payload` for redo. `test_e7_cast_undo_restores_outfit_and_expression_side_effects` covers the forward+undo side-effect case.

Residual layout snapshot gaps (`bubble_regions`, panel `version`) remain incomplete round-trip, not page-wipe; left as P2.

## Issues

No open P1 issues.
