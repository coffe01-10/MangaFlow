# Repository Guidelines

## Project Structure & Module Organization

MangaFlow is a Windows-oriented monorepo with a FastAPI backend and a Next.js frontend. Backend code lives in `apps/api/app/`: API routes are under `api/routes/`, business logic under `services/`, model integrations under `model_adapters/`, and state rules under `domain/`. Alembic migrations live in `apps/api/migrations/`. Frontend routes are in `apps/web/app/`, reusable UI in `apps/web/components/`, and client utilities in `apps/web/lib/`. Python tests are in `tests/test_*.py`, Vitest files use `*.test.ts(x)`, and Playwright scenarios live in `tests/e2e/`. Keep architecture and schema decisions synchronized with `docs/architecture.md` and `docs/data-model.md`.

## Agent Delegation and Review Policy

The lead must classify work before assigning it. These levels describe merge risk, not the amount of code:

- **L1, bounded:** one module, explicit behavior, no transaction, process, migration, security, or resource-ownership changes. Examples: a dashboard count, a local component fix, focused tests, and documentation.
- **L2, cross-module:** several files or layers with a stable contract. Examples: UI workflow behavior, browser automation, performance diagnosis, and behavior-preserving module splits.
- **L3, critical:** database transaction ownership, concurrency, migrations, job state machines, Worker/process lifecycle, cleanup of external resources, authentication/security boundaries, or live PostgreSQL/Redis/provider integration.

Use the following project-specific performance profiles when distributing work. They summarize evidence from Issues #8/#12 and #9/#13 and must be revised after new evidence; they are not a general ranking of the underlying models.

| Agent | Default assignment | Demonstrated strengths | Mandatory review focus |
| --- | --- | --- | --- |
| **Gemini 3.7 Flash / Antigravity** | L1; selected, narrowly specified L2 | Fast first implementation, scaffolding, focused tests, and straightforward service changes | Transaction ownership, indirect commits, retry-time revalidation, realistic concurrency fixtures, live-environment guards, cleanup ownership, and claims made in reports. Do not give it sole ownership of L3 database/queue acceptance or production Worker changes. |
| **Grok 4.6 / Grok Build** | L2 UI/browser/performance work; L1 backend work | Browser automation, UI fixes, performance measurement, iterative reproduction, and producing substantial test tooling | Runtime directory ownership, canonical path checks, PID/process-tree identity, failure propagation during cleanup, deterministic measurement windows, and preserving failed runs rather than selecting the best result. L3 process/security orchestration requires a lead-owned design and review. |
| **GLM 5.3 Flash** | L2 and bounded L3 implementation; preferred for cross-module refactors and takeover after failed review rounds | Cross-module reasoning, consolidating prior review findings, refactoring, and completing complex corrective work | Windows mixed-encoding output, launcher PID versus real child identity, descendant shutdown, exact timing/sample windows, and cleanup failure recovery. L3 still requires independent lead review and cannot merge from the agent report alone. |

Assignment rules:

1. Put every delegated task in a GitHub Issue with the baseline commit, worktree/branch, exact scope, forbidden changes, acceptance cases, required commands, and known unverified boundaries.
2. Give one risky responsibility to one agent at a time. Split implementation, live-environment validation, and unrelated refactors into separate PRs. Do not use spare token budget as a reason to enlarge a PR.
3. Gemini should receive small, testable slices. Grok may own browser/performance slices with an exclusive port and load window. GLM may own larger refactors, but each extraction or behavior boundary must remain independently reviewable.
4. L3 work needs a lead-approved design before broad editing. Missing PostgreSQL, Redis, browser, container, or provider infrastructure must be reported as `BLOCKED` or `NOT RUN`; SQLite, fakeredis, mocks, and offline harnesses are not substitutes for live acceptance.
5. A green test run, an agent summary, or an opened PR is not acceptance. The lead reviews the exact SHA and diff, checks the tests for realistic failure construction, and independently runs checks proportional to risk before merge unless the user explicitly accepts a reduced-scope merge.
6. Return concrete GitHub review comments for defects. Allow one formal repair round. If the repaired delivery still has a merge blocker, stop delegating the same defect and let the lead take over; do not send a second repair round. Record that the original agent has stopped before editing its branch.
7. Agents must not edit `docs/roadmap.md` or `docs/development-progress.md` unless the lead assigns that exact documentation task. The lead updates status only from verified evidence.
8. Do not run two heavy suites, browser performance jobs, or live integration environments in parallel. Reserve ports and the performance window in the Issue, verify process/data ownership, and clean only resources proven to belong to that run.
9. After every merge, inspect all worktrees for uncommitted and unmerged work. Fast-forward or merge the latest `master` only when it cannot overwrite active edits; never force-push or delete a worktree as routine synchronization.

## Build, Test, and Development Commands

- `powershell -ExecutionPolicy Bypass -File .\scripts\setup-codex.ps1`: install Node/Python dependencies and apply migrations on a fresh checkout.
- `powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1`: migrate and start the web app plus API for normal local work.
- `npm run dev`: start Next.js and FastAPI; use `npm run dev:full` only when Redis and the RQ worker are required.
- `npm run check`: run ESLint, Ruff, Pytest, Vitest, and the production web build.
- `npm run test:e2e`: build the frontend and run Playwright against local web/API servers.

## Coding Style & Naming Conventions

Use four spaces in Python and follow Ruff's 100-character line limit, Python 3.12 rules, import sorting, and configured lint set. Use two spaces in TypeScript/TSX, functional React components, `PascalCase` component names, `camelCase` functions, and kebab-case component filenames such as `workflow-editor.tsx`. Keep API routes thin; place orchestration and domain decisions in services or domain modules.

## Testing Guidelines

Add regression tests with every behavior change. Prefer isolated SQLite fixtures for API tests and Testing Library/Vitest for component behavior. Name Python tests `test_<behavior>.py` and colocate frontend tests near the relevant library or component. Run targeted tests while iterating, then `npm run check` before review. Real Vertex image calls are excluded from default tests to prevent accidental cost.

## Commit & Pull Request Guidelines

History uses short, imperative, sentence-case subjects, for example `Improve storage card readability`. Keep commits focused and separate generated or migration changes when practical. Pull requests should explain user-visible behavior, data-model or migration impact, and verification performed; link the relevant issue or plan item and include screenshots for UI changes.

## Security & Configuration

Never commit `.env`, service-account JSON, generated media, or local SQLite files. Copy settings from `.env.example`, keep Vertex credentials server-side, and add schema changes through Alembic rather than editing databases manually.
