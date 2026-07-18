# Repository Guidelines

## Project Structure & Module Organization

MangaFlow is a Windows-oriented monorepo with a FastAPI backend and a Next.js frontend. Backend code lives in `apps/api/app/`: API routes are under `api/routes/`, business logic under `services/`, model integrations under `model_adapters/`, and state rules under `domain/`. Alembic migrations live in `apps/api/migrations/`. Frontend routes are in `apps/web/app/`, reusable UI in `apps/web/components/`, and client utilities in `apps/web/lib/`. Python tests are in `tests/test_*.py`, Vitest files use `*.test.ts(x)`, and Playwright scenarios live in `tests/e2e/`. Keep architecture and schema decisions synchronized with `docs/architecture.md` and `docs/data-model.md`.

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
