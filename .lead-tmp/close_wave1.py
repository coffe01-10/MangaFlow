import subprocess

REPO = "coffe01-10/MangaFlow"

def close(num, comment):
    subprocess.run(["gh", "issue", "close", str(num), "-R", REPO, "-c", comment],
                   capture_output=True, text=True, encoding="utf-8")
    print(f"closed #{num}")

def comment(num, body):
    subprocess.run(["gh", "issue", "comment", str(num), "-R", REPO, "--body", body],
                   capture_output=True, text=True, encoding="utf-8")
    print(f"commented #{num}")

FIXED_BY = (
    "Fixed via PR #{pr} (commit {sha}), verified by lead review + mutation-tested regression "
    "checks now registered in the native interaction chain (lead wiring commit 400b2dd); "
    "full `dotnet run --project apps/desktop/native-tests` (54 checks) and the complete "
    "`--render` chain green on this machine."
)

close(426, FIXED_BY.format(pr=515, sha="7960601") + "\n\nWardrobe multi-save now computes the final merged map once from live selector state and sends a single PATCH carrying the scene version. Checks: NativeIssue426Checks (two-dirty-character save → one PATCH, removal absent, version present).")
close(427, FIXED_BY.format(pr=517, sha="da68375") + "\n\nFour seams: load seq token + atomic commit block, workflowId committed before leaving-flush, PATCH paired to canvas owner, Activate loads exactly once. Checks: NativeIssue427Checks (reversed-completion race, A→B→C pending-flush pairing, single Activate load). NativeWorkflowRunChecks fixture recalibrated getA>=3→getA>=2 for the removed duplicate load; the version-7 assertion is unchanged.")
close(439, FIXED_BY.format(pr=514, sha="e7af082") + "\n\nUploadAsync translates timeout (guard keeps genuine caller cancels as OCE); SourceView surfaces the localized timeout notice; OutfitWorkspace shows Chinese copy. Checks: NativeIssue439Checks.")
close(440, FIXED_BY.format(pr=515, sha="7960601") + "\n\nFlagDate helper matches the API's null|ISO-string deleted_at contract; variant dropdown filters archived variants. Checks: NativeIssue484Checks (timestamp/null/absent/legacy shapes + dropdown exclusion).")
close(442, FIXED_BY.format(pr=514, sha="e7af082") + "\n\nValidate applies the '..' traversal check to the path portion only; absolute/:// stay whole-string. Checks: NativeIssue442Checks (encoded '..' accepted as data; traversal paths still rejected).")
close(446, FIXED_BY.format(pr=516, sha="8de933a") + "\n\nPreferences.Save catches IOException/UnauthorizedAccessException (KeyValueStore contract); OnClosing always reaches lifetime.Cancel + backend.StopAsync. Checks: NativeIssue411Checks (real unwritable paths, no throw, no .tmp litter).")
close(448, FIXED_BY.format(pr=515, sha="7960601") + "\n\nScene/beat/wardrobe save handlers carry in-flight guards (busy flag + disabled button, finally reset). Checks: NativeIssue426Checks double-click guards.")
close(468, FIXED_BY.format(pr=514, sha="e7af082") + "\n\nSame root cause as #442: query values are exempt from the traversal substring check; delete-project with '..' in the name is sendable and arrives encoded.")
close(469, FIXED_BY.format(pr=518, sha="30d171a") + "\n\nDirty flag wired to all inputs incl. the delete-confirmation name; ConfirmLeaveAsync override covers sidebar/Ctrl+K/window-close; RefreshAsync (F5) skips reload while dirty. Checks: NativeIssue469Checks.")
close(470, FIXED_BY.format(pr=516, sha="8de933a") + "\n\nOCE surfaces unknown-outcome feedback + dashboard/projects invalidation before the version check. Checks: NativeIssue470Checks (real mid-POST cancel via Deactivate).")
close(471, FIXED_BY.format(pr="514/516/518", sha="e7af082/8de933a/30d171a") + "\n\nAll four items: (1) ProjectSettingsView save/delete OCE → unknown-outcome feedback (PR #518); (2) HomeView drawer failures route to State.Status when collapsed (PR #516); (3) MainWindow F5 dead-backend sets Error+retry cue (PR #516); (4) canonical-swap failure best-effort rebinds the original reference (PR #514). Checks: NativeIssue470Checks + NativeIssue439Checks cover items 2-4; item 1 covered by NativeIssue469Checks.")
close(484, FIXED_BY.format(pr=515, sha="7960601") + "\n\nFlagDate treats non-null string deleted_at as deleted (legacy boolean still accepted); SceneAssetItem.Deleted now correct in production data. Checks: NativeIssue484Checks.")
close(411, FIXED_BY.format(pr=516, sha="8de933a") + "\n\nStopAsync escalates timeout → Kill(entireProcessTree:true) via pure PlanStopEscalation decision table; process/started reset in finally on every path so Reconnect can spawn a fresh host. Checks: NativeIssue411Checks (decision table + real stubborn-process kill).")

for n, pr, sha in [(500, 514, "e7af082"), (501, 515, "7960601"), (502, 516, "8de933a"),
                   (503, 517, "da68375"), (504, 518, "30d171a")]:
    close(n, f"Delegation complete: merged via PR #{pr} (commit {sha}); lead wiring commit 400b2dd registered the new checks; full render chain green.")

comment(449, "Item 1 (startup boot-hang copy) fixed via PR #516 commit 8de933a: dedicated 本地服务启动超时 copy, WrapStartupFailure pure mapping, pinned by NativeIssue411Checks. Items 2-3 (image-surface error contract) are delegated to Wave 2 (#520/#522) — this issue stays open until those land.")
comment(429, "Item 2 (SettingsView/ConnectionPanel refresh wipes drafts) fixed via PR #518 commit 30d171a, pinned by NativeIssue469Checks. Items 1/3/4 (GenerateView director draft, StoryboardView chapter-switch epoch, LocalEditWindow close guard) are delegated to Wave 2 (#520) — this issue stays open until those land.")
comment(486, "All four items (dead-batch latch, stale dashboard card, Lightbox swallowed failure, package diff re-entrancy) are delegated to Wave 2 (#521/#522); issue stays open until those land.")
