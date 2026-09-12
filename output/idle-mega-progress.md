# Idle mega round — 2026-09-12 (GLM-5.3, branch idle/mega-leftover-20260912)

Baseline `f10b844` → final master `fe66418`. All 13 assigned OPEN issues
processed; every one FIXED and closed. Twelve PRs merged this round
(#487–#498); prior-night PRs (#450/#451/#453/#454/#455/#463/#464/#466/#480)
were verified as the fix for six issues and credited in the closing
comments. `apps/desktop/native/**` and `apps/desktop/native-tests/**` were
not touched (verified empty diff over the round). No roadmap /
development-progress edits.

## Ledger

| Issue | Verdict | Evidence / PR |
| --- | --- | --- |
| #438 (P2) relay accept loop dies on transient accept() | FIXED | Fix in #463 (prior night, verified at `mangaflow_desktop_helper.py:217-244`); the issue's suggested regression test supplied in **#487** (ECONNABORTED/EPROTO/EMFILE survive + terminal-exit log pin). Issue auto-closed. |
| #443 sidecar edge paths (3 items) | FIXED | Item 1 pump partial-start: fix #480 (prior), connection-level pin **#488**. Item 2 `%` in user-data: fix #466 (prior) + `test_sqlalchemy_url_escapes_percent_interpolation`. Item 3 fake_channel residue: **#489** — `fake_channel.remove()` (idempotent, spares unrelated providers, zeros on unmigrated DB) + helper `--fake-channel-cleanup` + help-text caveat; 3 new tests; end-to-end smoke on a seeded SQLite user-data DB. Closed with per-item evidence comment. |
| #447 node child inherits MANGAFLOW_STATIC_EXPORT | FIXED | **#490** — added to `_node_child_env` strip list + pinned in the env-inheritance test. Windows inheritance leg NOT RUN (Linux sandbox; strip is platform-neutral). Auto-closed. |
| #460 api-root shadow scan misses alembic/uvicorn | FIXED (prior night) | #464 + #467: all six shadow forms rejected, lowercased scan, fail-closed probe fallback; 16 tests green. Closed with evidence comment, no new code needed. |
| #461 build-web-standalone strict rmtree + stale build-info fools e2e | FIXED | **#491** — best-effort clear + refuse-on-remnant for dist/web-standalone and the static copy; build-info written to temp name + `os.replace` after the move. 3 tests incl. locked-remnant refusal. Windows locked-file behavior NOT RUN. Auto-closed. |
| #457 assemble sweep contradicts docs; concurrent assembles unserialized | FIXED | **#492** — whole assemble under the shared dist lock (spec-loaded from build-web-standalone, one implementation); docstring + both recovery error texts now state the real sweep semantics; `test_assemble_web_resources.py` (merged in #454 but never registered — #343 trap) and `test_build_web_standalone.py` added to run-sidecar-e2e.sh. New subprocess contender test proves assemble blocks while the lock is held. Auto-closed. |
| #409 retired-copy cleanup pid-local | FIXED | Findings 1+3 already in #454 + #492; finding 2's named `_clear(staging)` abort surface closed in **#493** (best-effort + refuse; 2 new tests). Auto-closed via #493. |
| #430 shell-core lifecycle edges (3 items) | FIXED | Item 2 RunLog::create leak: #453 (prior, verified in `handshake.rs`). Items 1+3 in **#494**: export staging write failure now removes the truncated `.pending` (real-failure test: child process under RLIMIT_FSIZE=32, EFBIG mid-write; mutation-checked); planted directory at `<base>.log.rotating` absorbed by the name-gated staging clear (test). `cargo test` 149 green. Windows EFBIG leg NOT RUN (unix-only injection). Auto-closed. |
| #444 docs/tools: grandchild sweep / stale dist frontend / README | FIXED | README contradiction already reconciled by #434 (verified). **#495**: measure-native-startup.ps1 now snapshots the descendant tree recursively (dedup + image-compare pid-reuse guard) and carries a comment that the NUI-7 「5 秒内零残留」 rows (docs/native-ui-migration.md, docs/roadmap.md) were collected under the single-level sweep and need re-measurement before re-citation (docs untouched per assignment); new `guard-frontend-dist.mjs` refuses dangling-referenced dist/frontend, wired as tauri `beforeBuildCommand` (object form, `cwd: ".."` per the documented v2 hook contract), pinned by 4 tests + registered in run-sidecar-e2e.sh. PS1 execution and `tauri build` NOT RUN (no PowerShell/bundler in sandbox). Auto-closed. |
| #431 phase2_runner contract POSIX + Job-ordering string match | FIXED | Finding 1 already fixed by #455 (re-verified green). Finding 2 in **#496**: ordering guard now regex-matches ALL call sites (negative lookbehind excludes the helper's `def` line — which the old index/rindex pair silently mistook), asserts exactly-one per marker, orders full lists; mutation-checked (duplicated ResumeThread site fails new guard, passed old). Auto-closed. |
| #412 neutrality gate misses vertexai | FIXED | Core `vertexai` marker + vertex_credentials.py allowlist in #451 (prior). Marker-set completion in **#497**: `aiplatform` + `google-cloud-aiplatform` (both zero live hits — no allowlist churn), marker set documented in the script header, portable test pins `$patterns` to the exact list. Closed with evidence comment. |
| #462 neutrality test win32-gated + allowlist encoding | FIXED | **#497** — behavioral tests now gated on a powershell/pwsh BINARY (not OS); allowlist read pins `-Encoding UTF8` (PS 5.1 ANSI mojibake family); portable static tests run everywhere. PowerShell-binary legs NOT RUN in sandbox (skipped correctly). Auto-closed. |
| #413 start-dev.ps1 PS 5.1 ANSI .env read | FIXED (prior night) | #450 (verified: `Get-Content -LiteralPath ".env" -Encoding UTF8`); regression pin added in **#498** (`test_start_dev_ps1_contracts.py`). Auto-closed. |

Native issues #411 / #410: read-only comment 「本轮不修 WPF」 left on each;
no native code touched (hard rule respected).

## Verification summary (Linux-runnable, final master fe66418)

- `apps/desktop/scripts`: relay (14), api-root/env (16), build-web-standalone (3),
  assemble (10), frontend-dist guard (4) — all green (api-root/env run under
  `/tmp/mf-venv` python; system python lacks alembic).
- `tests/`: fake_channel cleanup (3), cli_process_windows (2 pass/1 win-skip),
  phase2_runner contract (2), neutrality gate (2 static, 6 pwsh-gated skips),
  start-dev pin (1) — green.
- `apps/desktop/shell-core`: `cargo test` — 149 passed, 0 failed.
- Guard greps simulated for all six neutrality markers: exit-0-equivalent,
  no new violations.
- NOT RUN (documented per issue): Windows PowerShell legs (measure script,
  neutrality behavioral legs, start-dev execution), Windows locked-file
  behavior for both dist-swap scripts, `tauri build` end-to-end, EFBIG
  injection on Windows.

## Residual notes for the lead

- NUI-7 「5 秒内零残留」 doc claims (docs/native-ui-migration.md:100-107,
  docs/roadmap.md:54-56) rest on the old single-level sweep; re-measure with
  the recursive measure-native-startup.ps1 on Windows before citing again.
- The e2e stop escalation note in #431 (node grandchild orphaning via
  direct-child-only kill) was listed as P4-related context; not addressed
  here (out of the assigned fix surface).
