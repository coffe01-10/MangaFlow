import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backup_restore as backup_restore_mod  # noqa: E402
import backup_restore_fixture as backup_restore_fixture_mod  # noqa: E402
from backup_restore import (  # noqa: E402
    DATABASE_REL,
    OWNER_MARKER_NAME,
    FIXTURE_MARKER_NAME,
    BackupRestoreError,
    backup,
    cleanup_owned_fixture,
    hash_file,
    restore,
    snapshot_files,
    verify_restored,
)
from backup_restore_fixture import create_isolated_fixture  # noqa: E402

REPO_STORAGE = ROOT / "storage"


def _relatives(root: Path) -> list[str]:
    relatives = []
    for folder in (root / "storage", root / "uploads"):
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path.is_file():
                relatives.append(path.relative_to(root).as_posix())
    for name in (FIXTURE_MARKER_NAME, ".env", "manifest.json", OWNER_MARKER_NAME):
        if (root / name).is_file():
            relatives.append(name)
    return relatives


def _snapshot(root: Path) -> dict:
    return snapshot_files(root, _relatives(root))


def _fingerprint(root: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        for name in sorted(dirnames) + sorted(filenames):
            child = current / name
            rel = child.relative_to(root).as_posix()
            if child.is_symlink() or child.is_junction():
                result[rel] = {"kind": "reparse"}
            elif child.is_dir():
                result[rel] = {"kind": "dir"}
            elif child.is_file():
                digest, size = hash_file(child)
                result[rel] = {
                    "kind": "file",
                    "sha256": digest,
                    "bytes": size,
                    "mtime_ns": child.stat().st_mtime_ns,
                }
    return result


def _make_junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"cannot create junction: {completed.stderr or completed.stdout}")


def _owner(path: Path) -> dict:
    return json.loads((path / OWNER_MARKER_NAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fixture_template(tmp_path_factory):
    template = tmp_path_factory.mktemp("backup-fixture-template") / "fixture"
    meta = create_isolated_fixture(template, repo_root=ROOT)
    return Path(meta["root"]), meta


@pytest.fixture
def fixture_root(tmp_path, fixture_template):
    source, meta = fixture_template
    dest = tmp_path / "fixture"
    shutil.copytree(source, dest)
    yield dest, meta
    storage = dest / "storage"
    if storage.exists() and REPO_STORAGE.exists():
        assert not storage.samefile(REPO_STORAGE)


def test_backup_restore_roundtrip_preserves_source_and_exports(tmp_path, fixture_root):
    source, meta = fixture_root
    before = _snapshot(source)
    archive = tmp_path / "archive"
    restored = tmp_path / "restored"
    backup_report = tmp_path / "backup-report.json"
    restore_report = tmp_path / "restore-report.json"

    backup_result = backup(
        source_root=source, destination=archive, report_path=backup_report
    )
    restore_result = restore(
        archive=archive,
        destination=restored,
        repo_root=ROOT,
        report_path=restore_report,
    )

    assert backup_result.outcome == "success"
    assert restore_result.outcome == "success"
    assert restore_result.incomplete is False
    assert backup_result.checks["source_unchanged"] == "passed"
    assert restore_result.checks["source_unchanged"] == "passed"
    assert restore_result.checks["pre_alembic_bytes"] == "passed"
    assert restore_result.checks["foreign_keys"] == "passed"
    assert restore_result.checks["page_export"] == meta["page_id"]
    assert restore_result.pre_alembic_db_sha256
    assert restore_result.post_alembic_db_sha256
    assert restore_result.pre_alembic_db_sha256 == next(
        item["sha256"] for item in backup_result.files if item["path"] == DATABASE_REL
    )
    assert _snapshot(source) == before
    assert not (archive / ".env").exists()
    assert not (archive / "uploads" / ".env.local").exists()
    exported = restored / "storage" / "exports" / "_restore-drill" / "page-0001.png"
    generated = restored / "storage" / meta["generated_key"]
    assert exported.is_file()
    assert hash_file(exported)[0] == hash_file(generated)[0]
    sidecar = json.loads(
        (
            restored / "storage" / "exports" / "_restore-drill" / "page-export.json"
        ).read_text(encoding="utf-8")
    )
    assert sidecar["page_id"] == meta["page_id"]
    verify_result = verify_restored(
        destination=restored,
        repo_root=ROOT,
        report_path=tmp_path / "verify-report.json",
    )
    assert verify_result.outcome == "success"
    assert json.loads(restore_report.read_text(encoding="utf-8"))["errors"] == []
    assert "cloud upload" in restore_result.not_run
    assert _owner(archive)["status"] == "complete"
    assert _owner(restored)["status"] == "complete"


def test_dry_run_validates_without_any_writes(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    report_path = tmp_path / "dry-run.json"
    before = _fingerprint(tmp_path)
    result = backup(
        source_root=source, destination=archive, dry_run=True, report_path=report_path
    )
    assert result.outcome == "success"
    assert result.checks["writes"] == "none"
    assert not archive.exists()
    assert not report_path.exists()
    assert _fingerprint(tmp_path) == before

    filled = tmp_path / "filled-archive"
    backup(
        source_root=source,
        destination=filled,
        report_path=tmp_path / "filled-report.json",
    )
    restore_dest = tmp_path / "restored"
    restore_report = tmp_path / "restore-dry-run.json"
    after_backup = _fingerprint(tmp_path)
    restore_result = restore(
        archive=filled,
        destination=restore_dest,
        repo_root=ROOT,
        dry_run=True,
        report_path=restore_report,
    )
    assert restore_result.outcome == "success"
    assert restore_result.checks["writes"] == "none"
    assert not restore_dest.exists()
    assert not restore_report.exists()
    assert _fingerprint(tmp_path) == after_backup


def test_restore_refuses_existing_destination(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    existing = tmp_path / "already-there"
    existing.mkdir()
    (existing / "keep.txt").write_text("do-not-delete", encoding="utf-8")
    before = _fingerprint(existing)
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=existing,
            repo_root=ROOT,
            report_path=tmp_path / "restore.json",
        )
    assert raised.value.code == "DESTINATION_EXISTS"
    assert _fingerprint(existing) == before
    assert raised.value.report is not None
    assert raised.value.report.destination_created is False


def test_hash_mismatch_fail_closed(tmp_path, fixture_root):
    source, meta = fixture_root
    before = _snapshot(source)
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    target = archive / "storage" / meta["generated_key"]
    target.write_bytes(target.read_bytes() + b"\x00")
    restored = tmp_path / "restored"
    report_path = tmp_path / "restore.json"
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=restored,
            repo_root=ROOT,
            report_path=report_path,
        )
    assert raised.value.code == "HASH_MISMATCH"
    assert not restored.exists()
    assert _snapshot(source) == before
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "failed"
    assert payload["destination_created"] is False
    assert payload["errors"][0]["code"] == "HASH_MISMATCH"


def test_missing_file_fail_closed(tmp_path, fixture_root):
    source, meta = fixture_root
    before = _snapshot(source)
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    (archive / "storage" / meta["generated_key"]).unlink()
    restored = tmp_path / "restored"
    report_path = tmp_path / "restore.json"
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=restored,
            repo_root=ROOT,
            report_path=report_path,
        )
    assert raised.value.code == "MISSING_FILE"
    assert not restored.exists()
    assert _snapshot(source) == before


def test_interrupted_copy_fail_closed(tmp_path, fixture_root):
    source, _meta = fixture_root
    before = _snapshot(source)
    archive = tmp_path / "archive"
    report_path = tmp_path / "backup.json"
    with pytest.raises(BackupRestoreError) as raised:
        backup(
            source_root=source,
            destination=archive,
            report_path=report_path,
            interrupt_after=1,
        )
    assert raised.value.code == "INTERRUPTED"
    assert archive.exists()
    assert (archive / "storage" / "mangaflow.db").is_file()
    assert not (archive / "manifest.json").exists()
    assert _snapshot(source) == before
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "failed"
    assert payload["incomplete"] is True
    assert payload["checks"]["incomplete"] == "INCOMPLETE"
    assert _owner(archive)["status"] == "incomplete"
    assert _owner(archive)["run_id"] == payload["run_id"]
    with pytest.raises(BackupRestoreError) as repeated:
        backup(
            source_root=source,
            destination=archive,
            report_path=tmp_path / "backup-2.json",
        )
    assert repeated.value.code == "DESTINATION_EXISTS"
    assert _owner(archive)["status"] == "incomplete"
    assert _snapshot(source) == before


def test_interrupted_restore_fail_closed(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    archive_before = _snapshot(archive)
    restored = tmp_path / "restored"
    report_path = tmp_path / "restore.json"
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=restored,
            repo_root=ROOT,
            report_path=report_path,
            interrupt_after=1,
        )
    assert raised.value.code == "INTERRUPTED"
    assert restored.exists()
    assert _snapshot(archive) == archive_before
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["incomplete"] is True
    assert _owner(restored)["status"] == "incomplete"
    with pytest.raises(BackupRestoreError) as repeated:
        restore(
            archive=archive,
            destination=restored,
            repo_root=ROOT,
            report_path=tmp_path / "restore-2.json",
        )
    assert repeated.value.code == "DESTINATION_EXISTS"
    assert _snapshot(archive) == archive_before


def test_repeated_restore_to_same_destination_refused(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    restored = tmp_path / "restored"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    first = restore(
        archive=archive,
        destination=restored,
        repo_root=ROOT,
        report_path=tmp_path / "restore-1.json",
    )
    assert first.outcome == "success"
    before = _fingerprint(restored)
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=restored,
            repo_root=ROOT,
            report_path=tmp_path / "restore-2.json",
        )
    assert raised.value.code == "DESTINATION_EXISTS"
    assert _fingerprint(restored) == before
    second = tmp_path / "restored-again"
    repeated = restore(
        archive=archive,
        destination=second,
        repo_root=ROOT,
        report_path=tmp_path / "restore-3.json",
    )
    assert repeated.outcome == "success"


def test_reparse_point_rejected_even_without_escape(tmp_path, fixture_root):
    source, _meta = fixture_root
    inside = source / "storage" / "generated" / "inside"
    inside.mkdir()
    (inside / "ok.txt").write_text("inside", encoding="utf-8")
    _make_junction(source / "storage" / "generated" / "alias", inside)
    archive = tmp_path / "archive"
    with pytest.raises(BackupRestoreError) as raised:
        backup(
            source_root=source,
            destination=archive,
            report_path=tmp_path / "backup.json",
        )
    assert raised.value.code == "REPARSE"
    assert not archive.exists()


def test_reparse_point_escape_rejected(tmp_path, fixture_root):
    source, _meta = fixture_root
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("leave-me", encoding="utf-8")
    _make_junction(source / "storage" / "generated" / "escape", outside)
    archive = tmp_path / "archive"
    with pytest.raises(BackupRestoreError) as raised:
        backup(
            source_root=source,
            destination=archive,
            report_path=tmp_path / "backup.json",
        )
    assert raised.value.code == "REPARSE"
    assert not archive.exists()
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "leave-me"


def test_secrets_are_excluded_from_archive(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    result = backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    assert "uploads/.env.local" in result.excluded
    assert "uploads/credentials.json" in result.excluded
    assert "storage/generated/.provider-credential-master-key" in result.excluded
    archived = {entry["path"] for entry in result.files}
    assert "uploads/.env.local" not in archived
    assert (source / "uploads" / ".env.local").is_file()


def test_source_changed_during_backup_fail_closed(tmp_path, fixture_root):
    source, meta = fixture_root
    archive = tmp_path / "archive"
    mutated = {"done": False}

    def mutate(_src: Path, _dest: Path) -> None:
        if mutated["done"]:
            return
        mutated["done"] = True
        target = source / "storage" / meta["generated_key"]
        target.write_bytes(target.read_bytes() + b"x")

    with pytest.raises(BackupRestoreError) as raised:
        backup(
            source_root=source,
            destination=archive,
            report_path=tmp_path / "backup.json",
            after_file=mutate,
        )
    assert raised.value.code == "SOURCE_CHANGED"
    assert archive.exists()
    assert not (archive / "manifest.json").exists()
    assert raised.value.report is not None
    assert raised.value.report.incomplete is True
    assert _owner(archive)["status"] == "incomplete"
    assert not (archive / "manifest.json").exists()


def test_backup_refuses_overlapping_destination(tmp_path, fixture_root):
    source, _meta = fixture_root
    with pytest.raises(BackupRestoreError) as raised:
        backup(
            source_root=source,
            destination=source / "nested-archive",
            report_path=tmp_path / "backup.json",
        )
    assert raised.value.code == "PATH_OVERLAP"
    assert not (source / "nested-archive").exists()


@pytest.mark.parametrize(
    "bad_path",
    [
        "../secret.png",
        "C:/Windows/notepad.exe",
        "//server/share/file.png",
        "storage/mangaflow.db:zone.identifier",
        "storage//generated/x.png",
        "storage/./page.png",
    ],
)
def test_restore_rejects_unsafe_manifest_paths(tmp_path, fixture_root, bad_path):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    manifest_path = archive / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"].append({"path": bad_path, "sha256": "0" * 64, "bytes": 1})
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    restored = tmp_path / "restored"
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=restored,
            repo_root=ROOT,
            report_path=tmp_path / "r.json",
        )
    assert raised.value.code in {"PATH_INVALID", "PATH_CONFLICT"}
    assert not restored.exists()


def test_restore_rejects_duplicate_and_case_colliding_paths(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    manifest_path = archive / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    duplicate = dict(payload["files"][0])
    payload["files"].append(duplicate)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=tmp_path / "restored-dup",
            repo_root=ROOT,
            report_path=tmp_path / "r.json",
        )
    assert raised.value.code == "PATH_CONFLICT"

    payload["files"].pop()
    colliding = dict(payload["files"][-1])
    colliding["path"] = str(colliding["path"]).upper()
    payload["files"].append(colliding)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupRestoreError) as raised_case:
        restore(
            archive=archive,
            destination=tmp_path / "restored-case",
            repo_root=ROOT,
            report_path=tmp_path / "r2.json",
        )
    assert raised_case.value.code == "PATH_CONFLICT"
    assert not (tmp_path / "restored-dup").exists()
    assert not (tmp_path / "restored-case").exists()


def test_restore_verifies_bytes_before_alembic(tmp_path, fixture_root, monkeypatch):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    original = backup_restore_mod.run_alembic_upgrade
    seen = {}

    def wrapped(destination: Path, repo_root: Path) -> str:
        db = destination / "storage" / "mangaflow.db"
        manifest = json.loads(
            (destination / "manifest.json").read_text(encoding="utf-8")
        )
        expected = next(
            item["sha256"] for item in manifest["files"] if item["path"] == DATABASE_REL
        )
        assert hash_file(db)[0] == expected
        seen["pre"] = expected
        revision = original(destination, repo_root)
        connection = sqlite3.connect(db)
        try:
            connection.execute("UPDATE projects SET name = name || '-migrated'")
            connection.commit()
        finally:
            connection.close()
        seen["post"] = hash_file(db)[0]
        assert seen["post"] != seen["pre"]
        return revision

    monkeypatch.setattr(backup_restore_mod, "run_alembic_upgrade", wrapped)
    restored = tmp_path / "restored"
    result = restore(
        archive=archive,
        destination=restored,
        repo_root=ROOT,
        report_path=tmp_path / "restore.json",
    )
    assert result.outcome == "success"
    assert result.checks["pre_alembic_bytes"] == "passed"
    assert result.pre_alembic_db_sha256 == seen["pre"]
    assert result.post_alembic_db_sha256 == seen["post"]
    assert result.post_alembic_db_sha256 != result.pre_alembic_db_sha256
    verify = verify_restored(
        destination=restored, repo_root=ROOT, report_path=tmp_path / "v.json"
    )
    assert verify.outcome == "success"
    assert verify.post_alembic_db_sha256 == seen["post"]


def test_cleanup_failure_is_reported(tmp_path, fixture_root, monkeypatch):
    source, _meta = fixture_root

    def boom(path):
        raise OSError("simulated lock")

    monkeypatch.setattr(shutil, "rmtree", boom)
    with pytest.raises(BackupRestoreError) as raised:
        cleanup_owned_fixture(source)
    assert raised.value.code == "CLEANUP_FAILED"
    assert source.is_dir()
    unknown = tmp_path / "unknown-dir"
    unknown.mkdir()
    (unknown / "file.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(BackupRestoreError) as refused:
        cleanup_owned_fixture(unknown)
    assert refused.value.code == "CLEANUP_REFUSED"
    assert (unknown / "file.txt").read_text(encoding="utf-8") == "keep"


def test_cleanup_owned_fixture_deletes_only_marked_tree(tmp_path, fixture_root):
    source, _meta = fixture_root
    cleanup_owned_fixture(source)
    assert not source.exists()


def test_powershell_wrapper_dry_run_zero_writes(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    report = tmp_path / "ps-dry-run.json"
    before = _fingerprint(tmp_path)
    powershell = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    completed = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "backup-restore.ps1"),
            "-Action",
            "backup",
            "-SourceRoot",
            str(source),
            "-Destination",
            str(archive),
            "-Report",
            str(report),
            "-DryRun",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    assert completed.returncode == 0, completed.stderr
    assert not archive.exists()
    assert not report.exists()
    payload = json.loads(completed.stdout)
    assert payload["dry_run"] is True
    assert payload["outcome"] == "success"
    assert payload["checks"]["writes"] == "none"
    assert _fingerprint(tmp_path) == before


def test_cli_nonzero_on_existing_destination(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    existing = tmp_path / "keep"
    existing.mkdir()
    (existing / "file.txt").write_text("keep", encoding="utf-8")
    before = _fingerprint(existing)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "backup_restore.py"),
            "--repo-root",
            str(ROOT),
            "restore",
            "--archive",
            str(archive),
            "--destination",
            str(existing),
            "--report",
            str(tmp_path / "cli.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    assert completed.returncode != 0
    assert "DESTINATION_EXISTS" in completed.stderr
    assert _fingerprint(existing) == before


def test_fixture_stays_inside_tmp_path(tmp_path, fixture_root):
    source, _meta = fixture_root
    assert source.is_relative_to(tmp_path)
    assert not source.is_relative_to(ROOT / "storage")
    assert not source.is_relative_to(ROOT / "uploads")
    assert source.resolve() != (ROOT / ".env").resolve()


def test_report_refuses_existing_file_and_protected_source(tmp_path, fixture_root):
    source, _meta = fixture_root
    existing = tmp_path / "existing-report.json"
    existing.write_text("KEEP\n", encoding="utf-8")
    source_before = _fingerprint(source)

    with pytest.raises(BackupRestoreError) as existing_error:
        backup(
            source_root=source,
            destination=tmp_path / "archive-existing-report",
            report_path=existing,
        )
    assert existing_error.value.code == "DESTINATION_EXISTS"
    assert existing.read_text(encoding="utf-8") == "KEEP\n"
    assert not (tmp_path / "archive-existing-report").exists()
    assert _fingerprint(source) == source_before

    protected_report = source / "uploads" / "operation-report.json"
    with pytest.raises(BackupRestoreError) as overlap_error:
        backup(
            source_root=source,
            destination=tmp_path / "archive-protected-report",
            report_path=protected_report,
        )
    assert overlap_error.value.code == "PATH_OVERLAP"
    assert not protected_report.exists()
    assert not (tmp_path / "archive-protected-report").exists()
    assert _fingerprint(source) == source_before


def test_new_file_during_backup_invalidates_inventory(tmp_path, fixture_root):
    source, _meta = fixture_root
    late = source / "uploads" / "late.png"

    def add_late(_source: Path, _destination: Path) -> None:
        if not late.exists():
            late.write_bytes(b"late")

    archive = tmp_path / "archive"
    report = tmp_path / "report.json"
    with pytest.raises(BackupRestoreError) as raised:
        backup(
            source_root=source,
            destination=archive,
            report_path=report,
            after_file=add_late,
        )
    assert raised.value.code == "SOURCE_CHANGED"
    assert late.is_file()
    assert archive.is_dir()
    assert not (archive / "manifest.json").exists()
    assert _owner(archive)["status"] == "incomplete"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["outcome"] == "failed"
    assert payload["checks"]["source_changed"] == "failed"


def test_excluded_secret_contents_are_never_hashed(tmp_path, fixture_root, monkeypatch):
    source, _meta = fixture_root
    original_hash = backup_restore_mod.hash_file

    def guarded_hash(path: Path):
        if backup_restore_mod.is_excluded(path.name):
            raise AssertionError(f"excluded content was read: {path}")
        return original_hash(path)

    monkeypatch.setattr(backup_restore_mod, "hash_file", guarded_hash)
    result = backup(
        source_root=source,
        destination=tmp_path / "archive",
        dry_run=True,
        report_path=tmp_path / "dry-run-report.json",
    )
    assert result.outcome == "success"
    assert "uploads/.env.local" in result.excluded
    assert "uploads/credentials.json" in result.excluded
    assert "storage/generated/.provider-credential-master-key" in result.excluded


@pytest.mark.parametrize(
    "case",
    ["invalid_json", "missing_hash", "negative_bytes", "out_of_scope"],
)
def test_malformed_manifest_has_structured_failure_report(tmp_path, fixture_root, case):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(
        source_root=source, destination=archive, report_path=tmp_path / "backup.json"
    )
    manifest_path = archive / "manifest.json"
    if case == "invalid_json":
        manifest_path.write_text("{", encoding="utf-8")
    else:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if case == "missing_hash":
            payload["files"][0].pop("sha256")
        elif case == "negative_bytes":
            payload["files"][0]["bytes"] = -1
        else:
            payload["files"][-1]["path"] = "storage/exports/out-of-scope.png"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    destination = tmp_path / "restored"
    report = tmp_path / "restore-report.json"
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=destination,
            repo_root=ROOT,
            report_path=report,
        )
    assert raised.value.code == "MANIFEST_INVALID"
    assert not destination.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["outcome"] == "failed"
    assert payload["errors"][0]["code"] == "MANIFEST_INVALID"


def test_owner_marker_update_failure_fails_closed(tmp_path, fixture_root, monkeypatch):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    report = tmp_path / "report.json"
    original_replace = backup_restore_mod.os.replace

    def fail_owner_replace(source_path, destination_path):
        if Path(destination_path).name == OWNER_MARKER_NAME:
            raise OSError("simulated owner marker lock")
        return original_replace(source_path, destination_path)

    monkeypatch.setattr(backup_restore_mod.os, "replace", fail_owner_replace)
    with pytest.raises(BackupRestoreError) as raised:
        backup(source_root=source, destination=archive, report_path=report)
    assert raised.value.code == "OWNER_MARKER_UPDATE_FAILED"
    assert archive.is_dir()
    assert _owner(archive)["status"] == "in_progress"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["outcome"] == "failed"
    assert payload["incomplete"] is True
    assert any(error["code"] == "OWNER_MARKER_FAILED" for error in payload["errors"])


def test_failed_fixture_is_owned_and_cleanup_remains_available(tmp_path, monkeypatch):
    destination = tmp_path / "failed-fixture"

    def fail_migration(_destination: Path, _repo_root: Path) -> str:
        raise BackupRestoreError("ALEMBIC_FAILED", "simulated migration failure")

    monkeypatch.setattr(
        backup_restore_fixture_mod,
        "run_alembic_upgrade",
        fail_migration,
    )
    with pytest.raises(BackupRestoreError):
        create_isolated_fixture(destination, repo_root=ROOT)
    marker = json.loads((destination / FIXTURE_MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["kind"] == backup_restore_mod.FIXTURE_KIND
    assert marker["status"] == "in_progress"
    cleanup_owned_fixture(destination)
    assert not destination.exists()


def test_report_write_failure_marks_completed_destination_incomplete(
    tmp_path, fixture_root, monkeypatch
):
    source, _meta = fixture_root
    archive = tmp_path / "archive"

    def fail_report(_report, _report_path):
        raise BackupRestoreError("REPORT_WRITE_FAILED", "simulated report lock")

    monkeypatch.setattr(backup_restore_mod, "write_report", fail_report)
    with pytest.raises(BackupRestoreError) as raised:
        backup(
            source_root=source,
            destination=archive,
            report_path=tmp_path / "report.json",
        )
    assert raised.value.code == "REPORT_WRITE_FAILED"
    assert raised.value.report is not None
    assert raised.value.report.outcome == "failed"
    assert raised.value.report.incomplete is True
    assert _owner(archive)["status"] == "incomplete"
