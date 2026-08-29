"""Consistency backup and restore drill for local SQLite + generated + uploads.

Operator and test entry point. Never deletes an existing destination, never walks
unknown trees, and never loads `.env` or credentials. Tests must pass isolated
fixture roots; this module does not default to the repository data directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

MANIFEST_NAME = "manifest.json"
OWNER_MARKER_NAME = ".mangaflow-backup-restore-owner"
OWNER_KIND = "mangaflow-backup-restore-owner"
FIXTURE_MARKER_NAME = ".mangaflow-backup-fixture"
FIXTURE_KIND = "mangaflow-backup-fixture"
BACKUP_KIND = "mangaflow-consistency-backup"
DATABASE_REL = "storage/mangaflow.db"
GENERATED_REL = "storage/generated"
UPLOADS_REL = "uploads"
EXPORT_REL = "storage/exports/_restore-drill"
CHUNK_SIZE = 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

EXCLUDED_NAMES = {
    ".env",
    ".provider-credential-master-key",
    "credentials.json",
    "service-account.json",
}
EXCLUDED_SUFFIXES = (".pem", ".key")

NOT_RUN = (
    "cloud upload",
    "scheduler",
    "encryption-key management",
    "live provider call",
    "real user or production data",
    "Issue #23",
)

_ENV_ALLOWLIST = {
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PATHEXT",
    "PATH",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
}

CopyHook = Callable[[Path, Path], None]


class BackupRestoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.report: Report | None = None


@dataclass
class Report:
    action: str
    dry_run: bool
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    outcome: str = "running"
    run_id: str = field(default_factory=lambda: secrets.token_hex(32))
    source: str | None = None
    archive: str | None = None
    destination: str | None = None
    schema_revision: str | None = None
    pre_alembic_db_sha256: str | None = None
    post_alembic_schema_revision: str | None = None
    post_alembic_db_sha256: str | None = None
    files: list[dict[str, object]] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    not_run: list[str] = field(default_factory=lambda: list(NOT_RUN))
    destination_created: bool = False
    incomplete: bool = False

    def fail(self, code: str, message: str) -> None:
        self.outcome = "failed"
        self.errors.append({"code": code, "message": message})
        self.checks[code.lower()] = "failed"

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "archive": self.archive,
            "checks": self.checks,
            "destination": self.destination,
            "destination_created": self.destination_created,
            "dry_run": self.dry_run,
            "errors": self.errors,
            "excluded": self.excluded,
            "files": self.files,
            "finished_at": self.finished_at,
            "incomplete": self.incomplete,
            "not_run": self.not_run,
            "outcome": self.outcome,
            "post_alembic_db_sha256": self.post_alembic_db_sha256,
            "post_alembic_schema_revision": self.post_alembic_schema_revision,
            "pre_alembic_db_sha256": self.pre_alembic_db_sha256,
            "run_id": self.run_id,
            "schema_revision": self.schema_revision,
            "source": self.source,
            "started_at": self.started_at,
        }


def is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_junction():
            return True
    except OSError:
        return True
    try:
        attrs = path.lstat().st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def _path_chain(path: Path) -> list[Path]:
    chain = []
    current = path
    seen: set[str] = set()
    while True:
        key = os.path.normcase(str(current))
        if key in seen:
            break
        seen.add(key)
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    return chain


def _reject_reparse(path: Path, *, label: str) -> None:
    if is_link_or_reparse(path):
        raise BackupRestoreError(
            "REPARSE",
            f"{label} is a symlink, junction, or reparse point: {path}",
        )


def canonicalize_existing(path: Path, *, label: str) -> Path:
    if path is None or not str(path).strip():
        raise BackupRestoreError("PATH_INVALID", f"{label} path is missing")
    absolute = path.expanduser().absolute()
    for hop in _path_chain(absolute):
        if hop.exists() or hop.is_symlink() or hop.is_junction():
            _reject_reparse(hop, label=label)
    if not absolute.exists():
        raise BackupRestoreError("PATH_MISSING", f"{label} does not exist: {absolute}")
    resolved = absolute.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(absolute)):
        raise BackupRestoreError(
            "REPARSE",
            f"{label} is not canonical: {absolute}",
        )
    return resolved


def require_absent_destination(path: Path) -> Path:
    if path is None or not str(path).strip():
        raise BackupRestoreError("PATH_INVALID", "destination path is missing")
    absolute = path.expanduser().absolute()
    parent = canonicalize_existing(absolute.parent, label="destination parent")
    candidate = parent / absolute.name
    if candidate.exists() or candidate.is_symlink() or candidate.is_junction():
        raise BackupRestoreError(
            "DESTINATION_EXISTS",
            "destination already exists; refuse to reuse, empty, or delete it",
        )
    _reject_reparse(candidate, label="destination")
    return candidate


def create_new_directory(path: Path) -> Path:
    destination = require_absent_destination(path)
    try:
        destination.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise BackupRestoreError(
            "DESTINATION_EXISTS",
            "destination already exists; refuse to reuse or delete it",
        ) from exc
    return canonicalize_existing(destination, label="destination")


def refuse_overlap(left: Path, right: Path, *, label: str) -> None:
    first = left.expanduser().absolute()
    second = right.expanduser().absolute()
    if os.path.normcase(str(first)) == os.path.normcase(str(second)):
        raise BackupRestoreError("PATH_OVERLAP", f"{label} overlap: {first} and {second}")
    if first == second or first.is_relative_to(second) or second.is_relative_to(first):
        raise BackupRestoreError("PATH_OVERLAP", f"{label} overlap: {first} and {second}")


def is_excluded(relative: str) -> bool:
    name = Path(relative).name
    lowered = name.lower()
    if lowered in {item.lower() for item in EXCLUDED_NAMES}:
        return True
    if lowered.startswith(".env"):
        return True
    return lowered.endswith(EXCLUDED_SUFFIXES)


def _posix(relative: Path) -> str:
    return relative.as_posix().lstrip("./")


def parse_manifest_relative(relative: object) -> str:
    if not isinstance(relative, str) or not relative:
        raise BackupRestoreError("PATH_INVALID", "manifest path must be a non-empty string")
    if relative.strip() != relative or any(ord(character) < 32 for character in relative):
        raise BackupRestoreError("PATH_INVALID", "manifest path contains whitespace or control data")
    if "\\" in relative or ":" in relative or relative.startswith("/") or relative.startswith("//"):
        raise BackupRestoreError(
            "PATH_INVALID",
            f"manifest path must be a relative POSIX path without drive, UNC, ADS, or root: {relative}",
        )
    if unicodedata.normalize("NFC", relative) != relative:
        raise BackupRestoreError("PATH_INVALID", f"manifest path must be Unicode NFC: {relative}")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise BackupRestoreError("PATH_INVALID", f"manifest path has empty, dot, or parent segments: {relative}")
    if is_excluded(relative):
        raise BackupRestoreError("PATH_INVALID", f"manifest includes an excluded path: {relative}")
    return relative


def _relative_key(relative: str) -> str:
    return os.path.normcase(unicodedata.normalize("NFC", relative))


def resolve_manifest_target(
    root: Path,
    relative: str,
    seen_relative: dict[str, str],
    seen_target: dict[str, str],
) -> Path:
    relative = parse_manifest_relative(relative)
    rel_key = _relative_key(relative)
    previous = seen_relative.get(rel_key)
    if previous is not None:
        raise BackupRestoreError(
            "PATH_CONFLICT",
            f"duplicate or case/Unicode-conflicting manifest path: {relative} vs {previous}",
        )
    target = root.joinpath(*relative.split("/")).absolute()
    if not target.is_relative_to(root):
        raise BackupRestoreError("PATH_INVALID", f"manifest target escaped destination: {relative}")
    for hop in _path_chain(target):
        if hop == root.parent:
            break
        if hop.exists() or hop.is_symlink() or hop.is_junction():
            if hop != root:
                _reject_reparse(hop, label="manifest target")
    tgt_key = os.path.normcase(str(target))
    previous_target = seen_target.get(tgt_key)
    if previous_target is not None:
        raise BackupRestoreError(
            "PATH_CONFLICT",
            f"manifest paths collide on one target: {relative} vs {previous_target}",
        )
    seen_relative[rel_key] = relative
    seen_target[tgt_key] = relative
    return target


def validate_manifest_paths(root: Path, manifest: dict[str, object]) -> list[tuple[str, Path, dict[str, object]]]:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise BackupRestoreError("MANIFEST_INVALID", "manifest files list is missing")
    seen_relative: dict[str, str] = {}
    seen_target: dict[str, str] = {}
    resolved: list[tuple[str, Path, dict[str, object]]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise BackupRestoreError("MANIFEST_INVALID", "manifest file entry is invalid")
        relative = parse_manifest_relative(entry.get("path"))
        target = resolve_manifest_target(root, relative, seen_relative, seen_target)
        resolved.append((relative, target, entry))
    return resolved


def _reject_tree_reparse(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        _reject_reparse(current, label="directory")
        for name in (*dirnames, *filenames):
            _reject_reparse(current / name, label="path")


def hash_file(path: Path) -> tuple[str, int]:
    _reject_reparse(path, label="hash target")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def file_identity(path: Path) -> dict[str, int | str]:
    _reject_reparse(path, label="source file")
    digest, size = hash_file(path)
    stats = path.lstat()
    return {"sha256": digest, "bytes": size, "mtime_ns": stats.st_mtime_ns}


def snapshot_files(root: Path, relatives: list[str]) -> dict[str, dict[str, int | str]]:
    snapshot: dict[str, dict[str, int | str]] = {}
    for relative in relatives:
        path = root / relative
        if not path.exists() and not path.is_symlink() and not path.is_junction():
            continue
        snapshot[relative] = file_identity(path)
    return snapshot


def assert_unchanged(
    root: Path, before: dict[str, dict[str, int | str]], relatives: list[str]
) -> None:
    after = snapshot_files(root, relatives)
    if after != before:
        raise BackupRestoreError("SOURCE_CHANGED", "source tree changed during the operation")


def read_schema_revision(database: Path) -> str:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.Error as exc:
        raise BackupRestoreError("SCHEMA_REVISION_INVALID", f"cannot read alembic_version: {exc}") from exc
    finally:
        connection.close()
    if len(rows) != 1 or not rows[0][0]:
        raise BackupRestoreError("SCHEMA_REVISION_INVALID", "alembic_version must contain one revision")
    return str(rows[0][0])


def sqlite_consistent_backup(source: Path, destination: Path) -> None:
    _reject_reparse(source, label="database")
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise BackupRestoreError("DESTINATION_EXISTS", f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse(destination.parent, label="database parent")
    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def foreign_key_violations(database: Path) -> list[tuple]:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        return list(connection.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        connection.close()


def isolated_process_env(destination: Path, repo_root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() in _ENV_ALLOWLIST}
    database = destination / "storage" / "mangaflow.db"
    env.update(
        PYTHONPATH=str(repo_root / "apps" / "api"),
        MANGAFLOW_DISABLE_DOTENV="1",
        DATABASE_URL="sqlite:///" + database.resolve().as_posix(),
        STORAGE_ROOT=str(destination / "storage"),
        UPLOAD_ROOT=str(destination / "uploads"),
        QUEUE_ENABLED="false",
        ENVIRONMENT="restore-drill",
        GOOGLE_GENAI_USE_VERTEXAI="false",
    )
    return env


def run_alembic_upgrade(destination: Path, repo_root: Path) -> str:
    alembic_ini = repo_root / "apps" / "api" / "alembic.ini"
    if not alembic_ini.is_file():
        raise BackupRestoreError("ALEMBIC_MISSING", f"alembic.ini not found at {alembic_ini}")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
        cwd=str(repo_root),
        env=isolated_process_env(destination, repo_root),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "alembic failed").strip()[-1500:]
        raise BackupRestoreError("ALEMBIC_FAILED", detail)
    return read_schema_revision(destination / "storage" / "mangaflow.db")


def iter_scoped_files(source_root: Path) -> tuple[list[tuple[str, Path]], list[str]]:
    selected: list[tuple[str, Path]] = []
    excluded: list[str] = []
    for relative_root in (GENERATED_REL, UPLOADS_REL):
        folder = source_root / relative_root
        canonicalize_existing(folder, label=relative_root)
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            current = Path(dirpath)
            _reject_reparse(current, label=relative_root)
            for name in list(dirnames):
                _reject_reparse(current / name, label=relative_root)
            for name in filenames:
                child = current / name
                _reject_reparse(child, label=relative_root)
                relative = _posix(child.relative_to(source_root))
                if is_excluded(relative):
                    excluded.append(relative)
                    continue
                if not child.is_file():
                    raise BackupRestoreError("PATH_INVALID", f"refusing non-regular file: {child}")
                selected.append((relative, child))
    selected.sort(key=lambda item: item[0])
    return selected, excluded


def _copy_file(source: Path, destination: Path, *, root: Path) -> tuple[str, int]:
    _reject_reparse(source, label="copy source")
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise BackupRestoreError("DESTINATION_EXISTS", f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse(destination.parent, label="copy parent")
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        while chunk := incoming.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    _reject_reparse(destination, label="copy destination")
    copied = destination.resolve(strict=True)
    if not copied.is_relative_to(root.resolve(strict=True)):
        raise BackupRestoreError("REPARSE", f"copied file escaped destination: {copied}")
    return digest.hexdigest(), size


def _maybe_interrupt(
    copied: int, interrupt_after: int | None, hook: CopyHook | None, source: Path, dest: Path
) -> None:
    if hook is not None:
        hook(source, dest)
    if interrupt_after is not None and copied >= interrupt_after:
        raise BackupRestoreError("INTERRUPTED", "copy interrupted before completion")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    if path.exists() or path.is_symlink() or path.is_junction():
        raise BackupRestoreError("DESTINATION_EXISTS", f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())


def write_owner_marker(destination: Path, report: Report, *, status: str) -> None:
    payload = {
        "action": report.action,
        "created_at": report.started_at,
        "kind": OWNER_KIND,
        "pid": os.getpid(),
        "run_id": report.run_id,
        "status": status,
        "version": 1,
    }
    _write_json(destination / OWNER_MARKER_NAME, payload)


def update_owner_marker(destination: Path, report: Report, *, status: str) -> None:
    marker = destination / OWNER_MARKER_NAME
    if is_link_or_reparse(marker) or not marker.is_file():
        return
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if payload.get("kind") != OWNER_KIND or payload.get("run_id") != report.run_id:
        return
    payload["status"] = status
    pending = destination / (OWNER_MARKER_NAME + ".pending")
    if pending.exists() or pending.is_symlink() or pending.is_junction():
        return
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with pending.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, marker)


def mark_incomplete(report: Report, destination: Path | None) -> None:
    report.incomplete = True
    report.outcome = "failed"
    report.checks["incomplete"] = "INCOMPLETE"
    if destination is not None and report.destination_created:
        update_owner_marker(destination, report, status="incomplete")


def write_report(report: Report, report_path: Path | None) -> None:
    report.finished_at = datetime.now(UTC).isoformat()
    if report.dry_run or report_path is None:
        return
    absolute = report_path.expanduser().absolute()
    parent = canonicalize_existing(absolute.parent, label="report parent")
    target = parent / absolute.name
    _reject_reparse(target, label="report path")
    target.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _finish(report: Report, report_path: Path | None) -> Report:
    try:
        write_report(report, report_path)
    except BackupRestoreError as exc:
        report.fail(exc.code, str(exc))
        raise
    except OSError as exc:
        report.fail("REPORT_WRITE_FAILED", str(exc))
        raise BackupRestoreError("REPORT_WRITE_FAILED", str(exc)) from exc
    return report


def _created_destination(report: Report) -> Path | None:
    if report.destination_created and report.destination:
        return Path(report.destination)
    return None


def _run(action: Callable[[Report], None], report: Report, report_path: Path | None) -> Report:
    try:
        action(report)
        if report.outcome == "running":
            report.outcome = "success"
        if report.destination_created and report.outcome == "success":
            update_owner_marker(Path(report.destination), report, status="complete")
        return _finish(report, report_path)
    except KeyboardInterrupt:
        report.fail("INTERRUPTED", "operation interrupted")
        mark_incomplete(report, _created_destination(report))
        _finish(report, report_path)
        error = BackupRestoreError("INTERRUPTED", "operation interrupted")
        error.report = report
        raise error from None
    except BackupRestoreError as exc:
        if not any(item["code"] == exc.code for item in report.errors):
            report.fail(exc.code, str(exc))
        if report.destination_created:
            mark_incomplete(report, _created_destination(report))
        _finish(report, report_path)
        exc.report = report
        raise
    except OSError as exc:
        report.fail("IO_FAILED", str(exc))
        if report.destination_created:
            mark_incomplete(report, _created_destination(report))
        _finish(report, report_path)
        error = BackupRestoreError("IO_FAILED", str(exc))
        error.report = report
        raise error from exc


def _source_layout(source_root: Path) -> tuple[Path, Path, Path, Path]:
    root = canonicalize_existing(source_root, label="source root")
    database = canonicalize_existing(root / "storage" / "mangaflow.db", label="database")
    generated = canonicalize_existing(root / "storage" / "generated", label="generated")
    uploads = canonicalize_existing(root / "uploads", label="uploads")
    if not database.is_file():
        raise BackupRestoreError("PATH_INVALID", f"database is not a file: {database}")
    if not generated.is_dir() or not uploads.is_dir():
        raise BackupRestoreError("PATH_INVALID", "generated and uploads must be directories")
    return root, database, generated, uploads


def _load_manifest(archive: Path) -> dict[str, object]:
    manifest_path = canonicalize_existing(archive / MANIFEST_NAME, label="manifest")
    _reject_reparse(manifest_path, label="manifest")
    if not manifest_path.is_file():
        raise BackupRestoreError("MANIFEST_INVALID", "manifest must be a regular file")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("kind") != BACKUP_KIND or payload.get("version") != 1:
        raise BackupRestoreError("MANIFEST_INVALID", "manifest kind or version mismatch")
    return payload


def verify_tree_bytes(
    root: Path,
    manifest: dict[str, object],
    report: Report,
    *,
    skip_hash: set[str] | None = None,
) -> str | None:
    skipped = skip_hash or set()
    db_digest: str | None = None
    report.files = []
    for relative, target, entry in validate_manifest_paths(root, manifest):
        if not target.exists():
            raise BackupRestoreError("MISSING_FILE", f"missing {relative}")
        _reject_reparse(target, label="manifest file")
        if not target.is_file():
            raise BackupRestoreError("PATH_INVALID", f"manifest file is not a regular file: {relative}")
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise BackupRestoreError("REPARSE", f"file escaped tree: {relative}")
        if relative in skipped:
            report.files.append(
                {"path": relative, "sha256": "not_compared_after_alembic", "bytes": resolved.stat().st_size}
            )
            continue
        expected = str(entry.get("sha256", ""))
        digest, size = hash_file(resolved)
        if digest != expected:
            raise BackupRestoreError("HASH_MISMATCH", f"hash mismatch for {relative}")
        if entry.get("bytes") not in {None, size} and int(entry["bytes"]) != size:
            raise BackupRestoreError("HASH_MISMATCH", f"size mismatch for {relative}")
        report.files.append({"path": relative, "sha256": digest, "bytes": size})
        if relative == DATABASE_REL:
            db_digest = digest
    report.checks["hashes"] = "passed"
    report.checks["manifest_paths"] = "passed"
    return db_digest


def _copy_archive(
    archive: Path,
    destination: Path,
    manifest: dict[str, object],
    interrupt_after: int | None,
    after_file: CopyHook | None,
) -> None:
    copied = 0
    for relative, source, entry in validate_manifest_paths(archive, manifest):
        target = destination.joinpath(*relative.split("/"))
        digest, size = _copy_file(source, target, root=destination)
        if digest != entry["sha256"] or (entry.get("bytes") not in {None, size} and int(entry["bytes"]) != size):
            raise BackupRestoreError("HASH_MISMATCH", f"restore hash mismatch for {relative}")
        copied += 1
        _maybe_interrupt(copied, interrupt_after, after_file, source, target)
    _write_json(destination / MANIFEST_NAME, manifest)


def offline_page_export(destination: Path) -> dict[str, object]:
    database = destination / "storage" / "mangaflow.db"
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            """
            SELECT manga_pages.id, manga_pages.page_number, assets.storage_key, assets.source,
                   assets.sha256, assets.mime_type
            FROM manga_pages
            JOIN page_candidates ON page_candidates.id = manga_pages.selected_candidate_id
            JOIN assets ON assets.id = page_candidates.asset_id
            WHERE manga_pages.selected_candidate_id IS NOT NULL
            ORDER BY manga_pages.page_number
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise BackupRestoreError("EXPORT_FAILED", "restored database has no selected page to export")
    page_id, page_number, storage_key, source, sha256, mime_type = row
    parse_manifest_relative(str(storage_key).replace("\\", "/"))
    root = destination / ("uploads" if source == "USER_UPLOAD" else "storage")
    asset_path = canonicalize_existing(root / storage_key, label="export asset")
    if not asset_path.is_relative_to(root.resolve(strict=True)):
        raise BackupRestoreError("REPARSE", f"asset escaped storage root: {storage_key}")
    digest, size = hash_file(asset_path)
    if digest != sha256:
        raise BackupRestoreError("HASH_MISMATCH", f"selected asset hash mismatch for {storage_key}")
    export_dir = destination / EXPORT_REL
    if export_dir.exists() or export_dir.is_symlink() or export_dir.is_junction():
        export_dir = canonicalize_existing(export_dir, label="export dir")
        if not export_dir.is_dir():
            raise BackupRestoreError("PATH_INVALID", f"export path is not a directory: {export_dir}")
    else:
        export_dir.mkdir(parents=True, exist_ok=False)
    png_name = f"page-{int(page_number):04d}.png"
    exported = export_dir / png_name
    if exported.exists() or exported.is_symlink() or exported.is_junction():
        existing_hash, existing_size = hash_file(canonicalize_existing(exported, label="export png"))
        if existing_hash != digest or existing_size != size:
            raise BackupRestoreError(
                "DESTINATION_EXISTS",
                "restore-drill export already exists with different content",
            )
    else:
        _copy_file(asset_path, exported, root=destination.resolve(strict=True))
    document = {
        "schema_version": "1.0",
        "kind": "restore-drill-page-export",
        "page_id": page_id,
        "page_number": page_number,
        "asset_path": _posix(Path(storage_key)),
        "sha256": digest,
        "bytes": size,
        "mime_type": mime_type,
        "file": png_name,
    }
    sidecar = export_dir / "page-export.json"
    serialized = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if sidecar.exists() or sidecar.is_symlink() or sidecar.is_junction():
        existing = canonicalize_existing(sidecar, label="export json").read_text(encoding="utf-8")
        if existing != serialized:
            raise BackupRestoreError(
                "DESTINATION_EXISTS",
                "restore-drill export JSON already exists with different content",
            )
    else:
        _write_json(sidecar, document)
    return document


def backup(
    *,
    source_root: Path,
    destination: Path,
    dry_run: bool = False,
    report_path: Path | None = None,
    interrupt_after: int | None = None,
    after_file: CopyHook | None = None,
) -> Report:
    report = Report(action="backup", dry_run=dry_run)

    def body(current: Report) -> None:
        root, database, _generated, _uploads = _source_layout(source_root)
        current.source = str(root)
        dest = require_absent_destination(destination)
        refuse_overlap(root, dest, label="source and backup destination")
        current.destination = str(dest)
        scoped, excluded = iter_scoped_files(root)
        current.excluded = excluded
        planned_relatives = [DATABASE_REL, *[relative for relative, _ in scoped]]
        before = snapshot_files(root, planned_relatives + excluded)
        current.schema_revision = read_schema_revision(database)
        current.checks["path_safety"] = "passed"
        current.checks["dry_run"] = "passed" if dry_run else "not_applicable"
        if dry_run:
            current.files = [
                {"path": DATABASE_REL, "bytes": database.stat().st_size, "planned": True},
                *[
                    {"path": relative, "bytes": path.stat().st_size, "planned": True}
                    for relative, path in scoped
                ],
            ]
            assert_unchanged(root, before, planned_relatives + excluded)
            current.checks["source_unchanged"] = "passed"
            current.checks["writes"] = "none"
            return
        created = create_new_directory(destination)
        current.destination = str(created)
        current.destination_created = True
        write_owner_marker(created, current, status="in_progress")
        copied = 0
        if file_identity(database) != before[DATABASE_REL]:
            raise BackupRestoreError("SOURCE_CHANGED", "database changed before snapshot copy")
        sqlite_consistent_backup(database, created / "storage" / "mangaflow.db")
        db_hash, db_size = hash_file(created / "storage" / "mangaflow.db")
        current.files.append({"path": DATABASE_REL, "sha256": db_hash, "bytes": db_size})
        copied += 1
        _maybe_interrupt(
            copied, interrupt_after, after_file, database, created / "storage" / "mangaflow.db"
        )
        for relative, path in scoped:
            if file_identity(path) != before[relative]:
                raise BackupRestoreError("SOURCE_CHANGED", f"source file changed during backup: {relative}")
            target = created / relative
            digest, size = _copy_file(path, target, root=created)
            if digest != before[relative]["sha256"] or size != before[relative]["bytes"]:
                raise BackupRestoreError("SOURCE_CHANGED", f"copied bytes diverged from source snapshot: {relative}")
            current.files.append({"path": relative, "sha256": digest, "bytes": size})
            copied += 1
            _maybe_interrupt(copied, interrupt_after, after_file, path, target)
        assert_unchanged(root, before, planned_relatives + excluded)
        current.checks["source_unchanged"] = "passed"
        manifest = {
            "version": 1,
            "kind": BACKUP_KIND,
            "created_at": current.started_at,
            "schema_revision": current.schema_revision,
            "files": current.files,
            "excluded": excluded,
            "excluded_patterns": sorted(EXCLUDED_NAMES) + list(EXCLUDED_SUFFIXES) + [".env*"],
        }
        _write_json(created / MANIFEST_NAME, manifest)
        current.checks["manifest"] = "written"
        current.archive = str(created)

    return _run(body, report, report_path)


def restore(
    *,
    archive: Path,
    destination: Path,
    repo_root: Path,
    dry_run: bool = False,
    report_path: Path | None = None,
    interrupt_after: int | None = None,
    after_file: CopyHook | None = None,
) -> Report:
    report = Report(action="restore", dry_run=dry_run)

    def body(current: Report) -> None:
        archive_root = canonicalize_existing(archive, label="archive")
        current.archive = str(archive_root)
        repo = canonicalize_existing(repo_root, label="repo root")
        dest = require_absent_destination(destination)
        refuse_overlap(archive_root, dest, label="archive and restore destination")
        current.destination = str(dest)
        manifest = _load_manifest(archive_root)
        current.schema_revision = str(manifest.get("schema_revision") or "")
        validate_manifest_paths(dest, manifest)
        current.checks["manifest_paths"] = "passed"
        relatives = [parse_manifest_relative(entry.get("path")) for entry in manifest["files"]]
        source_snapshot = snapshot_files(archive_root, relatives)
        db_digest = verify_tree_bytes(archive_root, manifest, current)
        current.pre_alembic_db_sha256 = db_digest
        current.checks["path_safety"] = "passed"
        if dry_run:
            current.checks["writes"] = "none"
            assert_unchanged(archive_root, source_snapshot, relatives)
            current.checks["source_unchanged"] = "passed"
            return
        created = create_new_directory(destination)
        current.destination = str(created)
        current.destination_created = True
        write_owner_marker(created, current, status="in_progress")
        _copy_archive(archive_root, created, manifest, interrupt_after, after_file)
        current.checks["copy"] = "passed"
        copied_digest = verify_tree_bytes(created, manifest, current)
        if copied_digest != current.pre_alembic_db_sha256:
            raise BackupRestoreError("HASH_MISMATCH", "copied database hash does not match archive")
        current.pre_alembic_db_sha256 = copied_digest
        current.checks["pre_alembic_bytes"] = "passed"
        upgraded = run_alembic_upgrade(created, repo)
        current.post_alembic_schema_revision = upgraded
        current.checks["alembic"] = upgraded
        db_path = created / "storage" / "mangaflow.db"
        post_digest, _post_size = hash_file(db_path)
        current.post_alembic_db_sha256 = post_digest
        current.checks["post_alembic_db"] = post_digest
        violations = foreign_key_violations(db_path)
        if violations:
            raise BackupRestoreError("FOREIGN_KEY_CHECK", f"foreign_key_check failed: {violations!r}")
        current.checks["foreign_keys"] = "passed"
        export = offline_page_export(created)
        current.checks["page_export"] = str(export["page_id"])
        assert_unchanged(archive_root, source_snapshot, relatives)
        current.checks["source_unchanged"] = "passed"

    return _run(body, report, report_path)


def verify_restored(
    *,
    destination: Path,
    repo_root: Path,
    report_path: Path | None = None,
) -> Report:
    report = Report(action="verify", dry_run=False)

    def body(current: Report) -> None:
        dest = canonicalize_existing(destination, label="destination")
        current.destination = str(dest)
        canonicalize_existing(repo_root, label="repo root")
        manifest = _load_manifest(dest)
        current.schema_revision = str(manifest.get("schema_revision") or "")
        validate_manifest_paths(dest, manifest)
        verify_tree_bytes(dest, manifest, current, skip_hash={DATABASE_REL})
        db_path = dest / "storage" / "mangaflow.db"
        _reject_reparse(db_path, label="database")
        current.post_alembic_db_sha256, _size = hash_file(db_path)
        current.post_alembic_schema_revision = read_schema_revision(db_path)
        current.checks["post_alembic_db"] = current.post_alembic_db_sha256
        violations = foreign_key_violations(db_path)
        if violations:
            raise BackupRestoreError("FOREIGN_KEY_CHECK", f"foreign_key_check failed: {violations!r}")
        current.checks["foreign_keys"] = "passed"
        export = offline_page_export(dest)
        current.checks["page_export"] = str(export["page_id"])
        current.checks["path_safety"] = "passed"

    return _run(body, report, report_path)


def cleanup_owned_fixture(root: Path) -> None:
    """Delete only a fixture directory created by this task, after ownership checks."""
    if root is None or not str(root).strip():
        raise BackupRestoreError("CLEANUP_REFUSED", "cleanup path is missing")
    absolute = root.expanduser().absolute()
    if not absolute.exists() and not absolute.is_symlink() and not absolute.is_junction():
        raise BackupRestoreError("CLEANUP_REFUSED", f"cleanup path does not exist: {absolute}")
    canonical = canonicalize_existing(absolute, label="fixture root")
    marker = canonical / FIXTURE_MARKER_NAME
    if is_link_or_reparse(marker) or not marker.is_file():
        raise BackupRestoreError(
            "CLEANUP_REFUSED",
            "refusing to delete a directory without this task's fixture marker",
        )
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupRestoreError("CLEANUP_REFUSED", "fixture marker is unreadable") from exc
    if payload.get("kind") != FIXTURE_KIND or payload.get("version") != 1:
        raise BackupRestoreError("CLEANUP_REFUSED", "fixture marker mismatch")
    _reject_tree_reparse(canonical)
    try:
        shutil.rmtree(canonical)
    except OSError as exc:
        raise BackupRestoreError("CLEANUP_FAILED", f"owned fixture cleanup failed: {exc}") from exc
    if canonical.exists():
        raise BackupRestoreError("CLEANUP_FAILED", "owned fixture still exists after cleanup")


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MangaFlow consistency backup and restore drill")
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    sub = parser.add_subparsers(dest="action", required=True)

    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--source-root", type=Path, required=True)
    backup_parser.add_argument("--destination", type=Path, required=True)
    backup_parser.add_argument("--report", type=Path)
    backup_parser.add_argument("--dry-run", action="store_true")

    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--archive", type=Path, required=True)
    restore_parser.add_argument("--destination", type=Path, required=True)
    restore_parser.add_argument("--report", type=Path)
    restore_parser.add_argument("--dry-run", action="store_true")

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--destination", type=Path, required=True)
    verify_parser.add_argument("--report", type=Path)

    fixture_parser = sub.add_parser("create-fixture")
    fixture_parser.add_argument("--destination", type=Path, required=True)
    fixture_parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("MANGAFLOW_DISABLE_DOTENV", "1")
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root
    try:
        if args.action == "backup":
            report = backup(
                source_root=args.source_root,
                destination=args.destination,
                dry_run=args.dry_run,
                report_path=args.report,
            )
        elif args.action == "restore":
            report = restore(
                archive=args.archive,
                destination=args.destination,
                repo_root=repo_root,
                dry_run=args.dry_run,
                report_path=args.report,
            )
        elif args.action == "verify":
            report = verify_restored(
                destination=args.destination,
                repo_root=repo_root,
                report_path=args.report,
            )
        else:
            from backup_restore_fixture import create_isolated_fixture

            created = create_isolated_fixture(args.destination, repo_root=repo_root)
            report = Report(action="create-fixture", dry_run=False, outcome="success")
            report.destination = str(created["root"])
            report.destination_created = True
            report.schema_revision = created["schema_revision"]
            report.checks["fixture"] = "created"
            _finish(report, args.report)
        if args.report is None or report.dry_run:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"{report.action} {report.outcome}", file=sys.stderr)
        return 0 if report.outcome == "success" else 1
    except BackupRestoreError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
