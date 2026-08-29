import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backup_restore import (  # noqa: E402
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
    marker = root / FIXTURE_MARKER_NAME
    if marker.is_file():
        relatives.append(FIXTURE_MARKER_NAME)
    env = root / ".env"
    if env.is_file():
        relatives.append(".env")
    manifest = root / "manifest.json"
    if manifest.is_file():
        relatives.append("manifest.json")
    return relatives


def _snapshot(root: Path) -> dict:
    return snapshot_files(root, _relatives(root))


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

    backup_result = backup(source_root=source, destination=archive, report_path=backup_report)
    restore_result = restore(
        archive=archive,
        destination=restored,
        repo_root=ROOT,
        report_path=restore_report,
    )

    assert backup_result.outcome == "success"
    assert restore_result.outcome == "success"
    assert backup_result.checks["source_unchanged"] == "passed"
    assert restore_result.checks["source_unchanged"] == "passed"
    assert restore_result.checks["foreign_keys"] == "passed"
    assert restore_result.checks["page_export"] == meta["page_id"]
    assert _snapshot(source) == before
    assert not (archive / ".env").exists()
    assert not (archive / "uploads" / ".env.local").exists()
    assert not (archive / "uploads" / "credentials.json").exists()
    assert not (archive / "storage" / ".provider-credential-master-key").exists()
    exported = restored / "storage" / "exports" / "_restore-drill" / "page-0001.png"
    generated = restored / "storage" / meta["generated_key"]
    assert exported.is_file()
    assert hash_file(exported)[0] == hash_file(generated)[0]
    sidecar = json.loads(
        (restored / "storage" / "exports" / "_restore-drill" / "page-export.json").read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["page_id"] == meta["page_id"]
    verify_result = verify_restored(
        destination=restored, repo_root=ROOT, report_path=tmp_path / "verify-report.json"
    )
    assert verify_result.outcome == "success"
    assert json.loads(restore_report.read_text(encoding="utf-8"))["errors"] == []
    assert "cloud upload" in restore_result.not_run
    assert "Issue #23" in restore_result.not_run


def test_dry_run_validates_without_writes(tmp_path, fixture_root):
    source, _meta = fixture_root
    before = _snapshot(source)
    archive = tmp_path / "archive"
    report_path = tmp_path / "dry-run.json"
    result = backup(source_root=source, destination=archive, dry_run=True, report_path=report_path)
    assert result.outcome == "success"
    assert result.checks["writes"] == "none"
    assert not archive.exists()
    assert report_path.is_file()
    assert _snapshot(source) == before
    restore_dest = tmp_path / "restored"
    filled = tmp_path / "filled-archive"
    backup(source_root=source, destination=filled, report_path=tmp_path / "filled-report.json")
    filled_before = _snapshot(filled)
    restore_result = restore(
        archive=filled,
        destination=restore_dest,
        repo_root=ROOT,
        dry_run=True,
        report_path=tmp_path / "restore-dry-run.json",
    )
    assert restore_result.outcome == "success"
    assert restore_result.checks["writes"] == "none"
    assert not restore_dest.exists()
    assert _snapshot(source) == before
    assert _snapshot(filled) == filled_before


def test_restore_refuses_existing_destination(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(source_root=source, destination=archive, report_path=tmp_path / "backup.json")
    existing = tmp_path / "already-there"
    existing.mkdir()
    (existing / "keep.txt").write_text("do-not-delete", encoding="utf-8")
    before = list(existing.iterdir())
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=existing,
            repo_root=ROOT,
            report_path=tmp_path / "restore.json",
        )
    assert raised.value.code == "DESTINATION_EXISTS"
    assert [path.name for path in existing.iterdir()] == [path.name for path in before]
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "do-not-delete"


def test_hash_mismatch_fail_closed(tmp_path, fixture_root):
    source, meta = fixture_root
    before = _snapshot(source)
    archive = tmp_path / "archive"
    backup(source_root=source, destination=archive, report_path=tmp_path / "backup.json")
    target = archive / "storage" / meta["generated_key"]
    target.write_bytes(target.read_bytes() + b"\x00")
    restored = tmp_path / "restored"
    report_path = tmp_path / "restore.json"
    with pytest.raises(BackupRestoreError) as raised:
        restore(archive=archive, destination=restored, repo_root=ROOT, report_path=report_path)
    assert raised.value.code == "HASH_MISMATCH"
    assert not restored.exists()
    assert _snapshot(source) == before
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "failed"
    assert payload["errors"][0]["code"] == "HASH_MISMATCH"


def test_missing_file_fail_closed(tmp_path, fixture_root):
    source, meta = fixture_root
    before = _snapshot(source)
    archive = tmp_path / "archive"
    backup(source_root=source, destination=archive, report_path=tmp_path / "backup.json")
    (archive / "storage" / meta["generated_key"]).unlink()
    restored = tmp_path / "restored"
    report_path = tmp_path / "restore.json"
    with pytest.raises(BackupRestoreError) as raised:
        restore(archive=archive, destination=restored, repo_root=ROOT, report_path=report_path)
    assert raised.value.code == "MISSING_FILE"
    assert not restored.exists()
    assert _snapshot(source) == before
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["errors"][0]["code"] == "MISSING_FILE"


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
    assert payload["errors"][0]["code"] == "INTERRUPTED"
    assert payload["destination_created"] is True


def test_interrupted_restore_fail_closed(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    backup(source_root=source, destination=archive, report_path=tmp_path / "backup.json")
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
    assert payload["outcome"] == "failed"
    assert payload["errors"][0]["code"] == "INTERRUPTED"


def test_repeated_restore_to_same_destination_refused(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    restored = tmp_path / "restored"
    backup(source_root=source, destination=archive, report_path=tmp_path / "backup.json")
    first = restore(
        archive=archive,
        destination=restored,
        repo_root=ROOT,
        report_path=tmp_path / "restore-1.json",
    )
    assert first.outcome == "success"
    marker = restored / "storage" / "exports" / "_restore-drill" / "page-export.json"
    original = marker.read_text(encoding="utf-8")
    with pytest.raises(BackupRestoreError) as raised:
        restore(
            archive=archive,
            destination=restored,
            repo_root=ROOT,
            report_path=tmp_path / "restore-2.json",
        )
    assert raised.value.code == "DESTINATION_EXISTS"
    assert marker.read_text(encoding="utf-8") == original
    second = tmp_path / "restored-again"
    repeated = restore(
        archive=archive,
        destination=second,
        repo_root=ROOT,
        report_path=tmp_path / "restore-3.json",
    )
    assert repeated.outcome == "success"


def test_reparse_point_escape_rejected(tmp_path, fixture_root):
    source, _meta = fixture_root
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("leave-me", encoding="utf-8")
    _make_junction(source / "storage" / "generated" / "escape", outside)
    archive = tmp_path / "archive"
    report_path = tmp_path / "backup.json"
    with pytest.raises(BackupRestoreError) as raised:
        backup(source_root=source, destination=archive, report_path=report_path)
    assert raised.value.code == "REPARSE_ESCAPE"
    assert not archive.exists()
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "leave-me"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["errors"][0]["code"] == "REPARSE_ESCAPE"


def test_secrets_are_excluded_from_archive(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    result = backup(source_root=source, destination=archive, report_path=tmp_path / "backup.json")
    assert "uploads/.env.local" in result.excluded
    assert "uploads/credentials.json" in result.excluded
    assert "storage/generated/.provider-credential-master-key" in result.excluded
    archived = {entry["path"] for entry in result.files}
    assert "uploads/.env.local" not in archived
    assert (source / "uploads" / ".env.local").is_file()


def test_cleanup_failure_is_reported(tmp_path, fixture_root, monkeypatch):
    source, _meta = fixture_root
    report_path = tmp_path / "cleanup.json"

    def boom(path):
        raise OSError("simulated lock")

    monkeypatch.setattr(shutil, "rmtree", boom)
    with pytest.raises(BackupRestoreError) as raised:
        cleanup_owned_fixture(source)
    assert raised.value.code == "CLEANUP_FAILED"
    assert source.is_dir()
    assert (source / FIXTURE_MARKER_NAME).is_file()
    unknown = tmp_path / "unknown-dir"
    unknown.mkdir()
    (unknown / "file.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(BackupRestoreError) as refused:
        cleanup_owned_fixture(unknown)
    assert refused.value.code == "CLEANUP_REFUSED"
    assert (unknown / "file.txt").read_text(encoding="utf-8") == "keep"
    assert not report_path.exists()


def test_cleanup_owned_fixture_deletes_only_marked_tree(tmp_path, fixture_root):
    source, _meta = fixture_root
    cleanup_owned_fixture(source)
    assert not source.exists()


def test_powershell_wrapper_dry_run(tmp_path, fixture_root):
    source, _meta = fixture_root
    archive = tmp_path / "archive"
    report = tmp_path / "ps-dry-run.json"
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
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
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["outcome"] == "success"
    assert payload["checks"]["writes"] == "none"


def test_fixture_stays_inside_tmp_path(tmp_path, fixture_root):
    source, _meta = fixture_root
    assert source.is_relative_to(tmp_path)
    assert not source.is_relative_to(ROOT / "storage")
    assert not source.is_relative_to(ROOT / "uploads")
    assert source.resolve() != (ROOT / ".env").resolve()
