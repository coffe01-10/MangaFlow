#!/usr/bin/env bash
# D2 (V02-53B evidence, V02-54 path): package the Python sidecar with PyInstaller (Linux form).
#
# Validates the packaging FORM the ADR requires (veto condition 1): can the
# FastAPI + SQLAlchemy + Pillow + Alembic app be frozen into a sidecar
# binary/directory that boots the real API? The bundle keeps alembic.ini and
# migrations/ beside the binary (the "app directory" shape); the frozen
# binary resolves app.* through the PyInstaller frozen importer.
#
# Windows PyInstaller output remains NOT RUN in this Linux sandbox; only the
# mechanism and dependency coverage are exercised here.
set -euo pipefail
DESKTOP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_ROOT/../.." && pwd)"
VENV="$REPO_ROOT/.venv-desktop"

if [ ! -x "$VENV/bin/pyinstaller" ]; then
  echo "run scripts/run-sidecar-e2e.sh once to create .venv-desktop (needs pyinstaller)" >&2
  exit 1
fi

cd "$REPO_ROOT"
"$VENV/bin/pyinstaller" --noconfirm --clean --onedir --name mangaflow-desktop-sidecar \
  --distpath "$DESKTOP_ROOT/dist/sidecar" --workpath /tmp/mangaflow-desktop-pyi --specpath /tmp/mangaflow-desktop-pyi \
  --paths apps/api --paths apps/desktop/sidecar \
  --hidden-import app --hidden-import app.main --hidden-import app.database \
  --hidden-import app.models --hidden-import app.worker_tasks --hidden-import app.config \
  --hidden-import app.api.router --hidden-import fake_channel \
  --collect-submodules app \
  apps/desktop/sidecar/mangaflow_desktop_helper.py

# Support files: the frozen app resolves alembic.ini relative to the frozen
# app.main location (`<bundle>/_internal/`), so alembic.ini + migrations/ live
# inside _internal; --api-root points there for the helper's own upgrade run.
LAYOUT="$DESKTOP_ROOT/dist/sidecar/mangaflow-desktop-sidecar"
mkdir -p "$LAYOUT/_internal"
cp "$REPO_ROOT/apps/api/alembic.ini" "$LAYOUT/_internal/"
cp -r "$REPO_ROOT/apps/api/migrations" "$LAYOUT/_internal/"
echo "sidecar bundle at $LAYOUT"
echo "smoke: $LAYOUT/mangaflow-desktop-sidecar app --api-root $LAYOUT/_internal --user-data <dir> --fake-channel"
