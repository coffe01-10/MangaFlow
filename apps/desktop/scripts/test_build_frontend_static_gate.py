"""Contract tests for the referenced-chunks consistency gate (#734).

The gate (chunk-consistency-gate.sh, sourced by build-frontend-static.sh
around its smoke section) fails the build when the entry document
references /_next/static chunks that are absent on disk — the
internally-inconsistent-export failure mode that only a full browser run
caught. The gate is exercised through bash subprocesses against fixtures:
no build, no worktree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_GATE = Path(__file__).resolve().parent / "chunk-consistency-gate.sh"


def _run_gate(frontend_dir: Path, index_html: Path) -> subprocess.CompletedProcess:
    # as_posix + quoting: on a Windows-hosted venv the bare str(path) hands
    # bash backslash paths whose escapes get eaten before source resolves
    # them (every gate test failed with "No such file or directory"); Git
    # Bash accepts D:/... forward-slash spellings.
    script = (
        f'source "{_GATE.as_posix()}" && '
        f'check_referenced_chunks "{frontend_dir.as_posix()}" "{index_html.as_posix()}"'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def _make_frontend(root: Path, chunks: dict[str, bool]) -> tuple[Path, Path]:
    frontend = root / "frontend"
    fragments = []
    for rel, present in chunks.items():
        fragments.append(f'<script src="{rel}"></script>')
        if present:
            target = frontend / rel.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"chunk")
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text(
        "<html><head>" + "".join(fragments) + "</head><body></body></html>",
        encoding="utf-8",
    )
    return frontend, frontend / "index.html"


def test_gate_passes_when_every_referenced_chunk_exists(tmp_path):
    frontend, index_html = _make_frontend(tmp_path, {
        "/_next/static/chunks/a.js": True,
        "/_next/static/css/b.css": True,
    })
    done = _run_gate(frontend, index_html)
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""


def test_gate_refuses_and_lists_missing_chunks(tmp_path):
    frontend, index_html = _make_frontend(tmp_path, {
        "/_next/static/chunks/a.js": True,
        "/_next/static/chunks/never-written.js": False,
        "/_next/static/css/also-missing.css": False,
    })
    done = _run_gate(frontend, index_html)
    assert done.returncode == 1, done.stdout
    assert "never-written.js" in done.stderr
    assert "also-missing.css" in done.stderr


def test_gate_tolerates_an_unreferenced_extra_chunk(tmp_path):
    """The gate is subset-shaped: the entry document's references must
    exist; extra unreferenced chunks on disk are not its business (the
    route/file smoke gate covers the rest)."""
    frontend, index_html = _make_frontend(tmp_path, {
        "/_next/static/chunks/a.js": True,
    })
    (frontend / "_next" / "static" / "chunks" / "extra.js").write_bytes(b"x")
    done = _run_gate(frontend, index_html)
    assert done.returncode == 0, done.stderr


def test_gate_ignores_query_strings_via_the_char_class(tmp_path):
    """The extraction char class stops at '?' — a queried reference is
    checked by its bare path (Next writes hashed names without queries;
    the class must not swallow the query into a phantom path)."""
    frontend = tmp_path / "frontend"
    (frontend / "_next" / "static" / "chunks").mkdir(parents=True)
    (frontend / "_next" / "static" / "chunks" / "app.js").write_bytes(b"x")
    index_html = frontend / "index.html"
    index_html.write_text(
        '<script src="/_next/static/chunks/app.js?v=abc"></script>',
        encoding="utf-8",
    )
    done = _run_gate(frontend, index_html)
    assert done.returncode == 0, done.stderr


def test_gate_refuses_a_missing_index_html(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    done = _run_gate(frontend, frontend / "index.html")
    assert done.returncode == 1
    assert "not found" in done.stderr


def test_build_script_gates_before_the_destructive_copy():
    """Ordering pin (DS-04): the smoke gate must run against the freshly
    built out/ tree BEFORE the destructive dist/frontend replace. A gate
    that only runs after the copy fails the build but leaves the known-
    broken export sitting in the shippable path, and gating the dist copy
    after its lock was released can observe another writer's torn state."""
    script = (_GATE.parent / "build-frontend-static.sh").read_text(
        encoding="utf-8"
    )
    gate_at = script.index('check_referenced_chunks "$WORKTREE/apps/web/out"')
    copy_at = script.index('rm -rf "$DESKTOP_ROOT/dist/frontend"')
    assert gate_at < copy_at, (
        "smoke gate must run before the destructive dist/frontend replace"
    )
    # The gate must not be re-anchored at the dist copy (post-lock read).
    assert 'check_referenced_chunks "$DESKTOP_ROOT/dist/frontend"' not in script
