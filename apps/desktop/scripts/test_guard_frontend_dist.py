"""Contract tests for the pre-bundle frontend-dist guard (#444).

`tauri build` bundles whatever sits at apps/desktop/dist/frontend
(frontendDist in tauri.conf.json). The tracked index.html is a PLACEHOLDER
whose `_next/static/chunks/*` references are all gitignored, so building
from a clean clone would ship a white-screen installer. The guard wired as
tauri's beforeBuildCommand must refuse dangling references and pass only a
complete export.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
GUARD = _SCRIPTS / "guard-frontend-dist.mjs"
CONF = _SCRIPTS.parent / "src-tauri" / "tauri.conf.json"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="the guard itself is a node script"
)


def _run_guard(dist: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(GUARD), str(dist)],
        capture_output=True,
        text=True,
    )


def _complete_export(root: Path) -> Path:
    dist = root / "frontend"
    (dist / "_next" / "static" / "chunks").mkdir(parents=True)
    (dist / "_next" / "static" / "chunks" / "app.js").write_text(
        "// chunk", encoding="utf-8"
    )
    (dist / "_next" / "static" / "chunks" / "app.css").write_text(
        "/* chunk */", encoding="utf-8"
    )
    (dist / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="/_next/static/chunks/app.css"/>'
        '<script src="/_next/static/chunks/app.js"></script></head></html>',
        encoding="utf-8",
    )
    return dist


def test_guard_refuses_the_dangling_reference_placeholder(tmp_path):
    """The exact clean-clone shape: an index.html whose referenced chunks
    are absent must refuse with the build-frontend-static.sh remedy."""

    dist = tmp_path / "frontend"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<html><head><script src="/_next/static/chunks/missing.js" async="">'
        "</script></head></html>",
        encoding="utf-8",
    )
    result = _run_guard(dist)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "build-frontend-static.sh" in result.stderr


def test_guard_passes_a_complete_export(tmp_path):
    result = _run_guard(_complete_export(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all present" in result.stdout


def test_guard_refuses_a_missing_frontend_dir(tmp_path):
    result = _run_guard(tmp_path / "frontend")
    assert result.returncode == 1
    assert "missing" in result.stderr


def test_tauri_conf_wires_the_guard_as_before_build_command():
    """The guard must actually run in `tauri build`: the object form pins
    cwd to the desktop root (tauri runs hooks from src-tauri by default),
    independent of where the CLI was invoked."""

    conf = json.loads(CONF.read_text(encoding="utf-8"))
    hook = conf["build"]["beforeBuildCommand"]
    assert hook == {
        "script": "node scripts/guard-frontend-dist.mjs",
        "cwd": "..",
    }
    assert conf["build"]["frontendDist"] == "../dist/frontend"


def test_guard_ignores_remote_data_hash_and_root_references(tmp_path):
    """The reference filter's skip list is load-bearing in BOTH directions:
    remote/data/hash/root references must not be resolved against disk (a
    CDN script or an anchor is not a missing asset), while a RELATIVE
    reference still is. Pin the whole skip table plus one relative control
    that must stay checked."""

    dist = tmp_path / "frontend"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<html><head>"
        '<script src="https://cdn.example.com/app.js"></script>'
        "<script src='//cdn.example.com/two.js'></script>"
        '<script src="data:text/javascript,console.log(1)"></script>'
        '<link rel="stylesheet" href="#anchor-only"/>'
        '<link rel="stylesheet" href="/"/>'
        '<script src="http://insecure.example.com/old.js"></script>'
        "</head><body></body></html>",
        encoding="utf-8",
    )
    result = _run_guard(dist)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 local assets" in result.stdout

    # Control: an identical page plus ONE relative reference that is missing
    # must refuse — proving the skips are a skip list, not a blanket pass.
    (dist / "index.html").write_text(
        (dist / "index.html").read_text(encoding="utf-8")
        + '<script src="local/relative.js"></script>'
    )
    result = _run_guard(dist)
    assert result.returncode == 1
    assert "local/relative.js" in result.stderr


def test_guard_strips_query_strings_before_resolving(tmp_path):
    """Next's hashed URLs carry query strings (?v=...); the guard strips
    them before resolving, so a hashed-and-queried chunk that EXISTS on
    disk must pass — the raw reference with the query would not."""

    dist = tmp_path / "frontend"
    dist.mkdir()
    (dist / "_next" / "static" / "chunks").mkdir(parents=True)
    (dist / "_next" / "static" / "chunks" / "app.js").write_text("// chunk")
    (dist / "index.html").write_text(
        '<script src="/_next/static/chunks/app.js?v=42"></script>'
    )
    result = _run_guard(dist)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 local assets" in result.stdout


def test_guard_covers_unquoted_attributes(tmp_path):
    """HTML5 also allows UNQUOTED attribute values (`src=/_next/x.js`) —
    the same tool-transform channel as the single-quote case (a
    quote-stripping minifier pass over a static export is lossless HTML).
    A regex that captures only quoted values extracts an empty set there
    and passes a dangling placeholder vacuously. Pin both a refusal (dangling
    unquoted refs) and a pass (unquoted refs that exist on disk)."""

    dist = tmp_path / "frontend"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<html><head>"
        "<script src=_next/static/chunks/unquoted-missing.js></script>"
        "<link rel=stylesheet href=_next/static/chunks/unquoted-missing.css>"
        "</head><body></body></html>",
        encoding="utf-8",
    )
    result = _run_guard(dist)
    assert result.returncode == 1, (
        f"unquoted dangling refs must refuse, got rc=0: {result.stdout}"
    )
    assert "unquoted-missing.js" in result.stderr

    (dist / "_next" / "static" / "chunks").mkdir(parents=True)
    (dist / "_next" / "static" / "chunks" / "unquoted-missing.js").write_text(
        "// chunk", encoding="utf-8"
    )
    (dist / "_next" / "static" / "chunks" / "unquoted-missing.css").write_text(
        "/* chunk */", encoding="utf-8"
    )
    result = _run_guard(dist)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 local assets" in result.stdout


def test_guard_unquoted_value_matches_browser_url_not_a_prefix(tmp_path):
    """Tokenizer fidelity for the unquoted form (round-8 review F1): quotes
    and non-ASCII spaces are PARSE ERRORS but part of an unquoted value, so
    the extracted ref must be the full browser-requested name. A regex that
    stops early (JS \\s matches U+00A0; excluding quotes) would resolve a
    PREFIX — if that prefix exists on disk the guard passes while the page
    white-screens. So each run plants ONLY the truncated prefix and must
    still refuse: the truncated capture would find it and pass."""

    for full_name in ('chunk"quoted.js', "chunk\u00a0nbsp.js"):
        dist = tmp_path / "frontend"
        dist.mkdir(exist_ok=True)
        (dist / "chunk").write_text("// truncated prefix exists", encoding="utf-8")
        (dist / "index.html").write_text(
            f"<html><head><script src={full_name}></script></head></html>",
            encoding="utf-8",
        )
        result = _run_guard(dist)
        assert result.returncode == 1, (
            f"a truncated prefix capture would pass over {full_name!r}: "
            + result.stdout
        )
        assert full_name in result.stderr, result.stderr

        # Positive control: the real (full) name on disk passes — the refusal
        # above is the name mismatch, not the syntax.
        (dist / full_name).write_text("// chunk", encoding="utf-8")
        result = _run_guard(dist)
        assert result.returncode == 0, result.stdout + result.stderr


def test_guard_covers_single_quoted_attributes(tmp_path):
    """Both HTML quote forms are legal (Next emits double quotes; a
    hand-edited page may carry single ones). A regex that captures only
    one form would extract an empty reference set and pass a dangling
    placeholder vacuously — the exact white-screen path this guard
    blocks. Pin both forms on the same page."""

    dist = tmp_path / "frontend"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<html><head>'
        "<script src='_next/static/chunks/single-missing.js'></script>"
        "<link rel='stylesheet' href='_next/static/chunks/single-missing.css'/>"
        "</head><body></body></html>",
        encoding="utf-8",
    )
    result = _run_guard(dist)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "single-missing.js" in result.stderr
