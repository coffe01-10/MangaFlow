"""Common, provider-neutral controller for optional external CLI image channels."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.model_adapters.base import ProviderAdapterError
from app.models import CLIExecutionRun
from app.services.media import inspect_upload_image

CLI_FAILURE_CODES = frozenset(
    {
        "CANCELLED",
        "CONFIGURATION",
        "CRASH",
        "INVALID_OUTPUT",
        "PARTIAL_OUTPUT",
        "RATE_LIMIT",
        "TIMEOUT",
        "UNAUTHENTICATED",
        "UNAVAILABLE",
        "UNKNOWN_RESULT",
        "UNSUPPORTED",
        "UPSTREAM",
    }
)
_RETRYABLE = frozenset({"RATE_LIMIT", "TIMEOUT", "UPSTREAM"})
_RETAIN = frozenset({"CRASH", "INVALID_OUTPUT", "PARTIAL_OUTPUT", "UNKNOWN_RESULT"})
_TOKEN_LINE = re.compile(
    r"(?:authorization\s*:|\btoken\b|\bapi[_-]?key\b|\bsk-[a-z0-9_-]+)", re.I
)
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
_DIAGNOSTIC_LIMIT = 8 * 1024


@dataclass(frozen=True)
class CLIExecutionRequest:
    operation: str
    prompt: str
    parameters: dict = field(default_factory=dict)
    reference_files: tuple[Path, ...] = ()
    reference_payloads: tuple[bytes, ...] = ()
    reference_mime_types: tuple[str, ...] = ()
    output_images: tuple[str, ...] = ("output/images/out_001.png",)
    max_images: int = 1


@dataclass(frozen=True)
class CLIProcessOutcome:
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""
    stdout_checksum: str | None = None
    stderr_checksum: str | None = None
    timed_out: bool = False
    cancelled: bool = False
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CLIExecutionResult:
    run_id: str
    images: tuple[bytes, ...]
    image_metadata: tuple[dict, ...]
    usage: dict
    cleanup_error: str | None = None


class CLIProcessRunner(Protocol):
    def run(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: int,
        cancel_requested: Callable[[], bool],
    ) -> CLIProcessOutcome: ...


class CLIExecutionController:
    def __init__(self, settings: Settings, session_factory: Callable[[], Session]) -> None:
        self.settings = settings
        self.session_factory = session_factory

    def prepare(
        self,
        *,
        job_id: str,
        model_call_attempt_id: str,
        connection_id: str,
        catalog_model_id: str,
        request: CLIExecutionRequest,
    ) -> str:
        _validate_request(request)
        run_id, token = str(uuid4()), uuid4().hex
        run_directory = self._create_directory(run_id)
        try:
            references = self._copy_references(run_directory, request.reference_files)
            references.extend(
                self._copy_reference_payloads(
                    run_directory,
                    request.reference_payloads,
                    request.reference_mime_types,
                )
            )
            payload = {
                "schema_version": 1,
                "operation": request.operation,
                "prompt": request.prompt,
                "parameters": request.parameters,
                "reference_images": references,
                "output_spec": {
                    "images": list(request.output_images),
                    "max_images": request.max_images,
                },
            }
            encoded = _canonical_json(payload)
            if len(encoded) > self.settings.max_provider_metadata_bytes:
                raise ProviderAdapterError("CONFIGURATION", "CLI 结构化请求超过大小上限")
            request_path = run_directory / "input" / "request.json"
            _write_bytes(request_path, encoded)
            request_path.chmod(stat.S_IREAD)
            _write_json(
                run_directory / "journal.json",
                {"version": 1, "run_id": run_id, "token": token, "state": "PREPARING"},
            )
            relative = run_directory.relative_to(
                self.settings.storage_root.resolve(strict=True)
            ).as_posix()
            self._claim(
                run_id=run_id,
                job_id=job_id,
                attempt_id=model_call_attempt_id,
                connection_id=connection_id,
                model_id=catalog_model_id,
                token=token,
                relative_path=relative,
                operation=request.operation,
                request_checksum=hashlib.sha256(encoded).hexdigest(),
            )
            return run_id
        except BaseException:
            # A locked leftover file (indexer, AV) must not replace the real
            # error with an unrelated OSError; the orphaned directory is
            # harmless (no DB row) and better leaked than masked.
            if run_directory.exists():
                _make_inputs_writable(run_directory)
                shutil.rmtree(run_directory, ignore_errors=True)
            raise

    def request_manifest(self, run_id: str) -> dict:
        """Return the immutable, checksum-verified request for adapter argv mapping."""

        row = self._load(run_id)
        run_directory, _journal = self._validate_directory(row)
        request_path = run_directory / "input" / "request.json"
        _reject_link(request_path)
        try:
            encoded = request_path.read_bytes()
            payload = json.loads(encoded)
        except (OSError, json.JSONDecodeError) as error:
            raise ProviderAdapterError("CONFIGURATION", "CLI 结构化请求无法读取") from error
        if hashlib.sha256(encoded).hexdigest() != row.request_checksum:
            raise ProviderAdapterError("CONFIGURATION", "CLI 结构化请求已被修改")
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ProviderAdapterError("CONFIGURATION", "CLI 结构化请求 schema 无效")
        return payload

    def fail_prepared(self, run_id: str, error: ProviderAdapterError) -> None:
        """Close a prepared run when adapter-specific argv mapping cannot continue."""

        self._finish_failure(run_id, error, None)
        cleanup_error = self._cleanup(run_id, retain=error.code in _RETAIN)
        if cleanup_error:
            error.add_note(f"CLI run cleanup failed: {type(cleanup_error).__name__}")

    def execute(
        self,
        run_id: str,
        *,
        runner: CLIProcessRunner,
        argv: tuple[str, ...],
        allowed_environment: tuple[str, ...] = (),
        environment_overrides: dict[str, str] | None = None,
        output_encoding: str = "utf-8",
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> CLIExecutionResult:
        _validate_argv(argv)
        row = self._load(run_id)
        run_directory, journal = self._validate_directory(row)
        outcome: CLIProcessOutcome | None = None
        try:
            identity_getter = getattr(runner, "controller_identity", None)
            identity = identity_getter() if callable(identity_getter) else None
            self._mark_running(run_id, run_directory, journal, identity)
            outcome = runner.run(
                argv=argv,
                cwd=run_directory / "workspace",
                environment=self._environment(
                    run_directory / "workspace",
                    allowed_environment,
                    environment_overrides,
                ),
                timeout_seconds=self.settings.cli_run_timeout_seconds,
                cancel_requested=cancel_requested,
            )
            stdout_checksum, stderr_checksum = self._diagnostics(
                run_directory, outcome, output_encoding
            )
            if outcome.cancelled or cancel_requested():
                raise ProviderAdapterError("CANCELLED", "CLI 任务已取消")
            if outcome.timed_out:
                raise ProviderAdapterError("TIMEOUT", "CLI 图片任务执行超时", retryable=True)
            if outcome.error_code:
                if outcome.error_code not in CLI_FAILURE_CODES:
                    raise ProviderAdapterError("INVALID_OUTPUT", "CLI 返回了无效错误码")
                raise ProviderAdapterError(
                    outcome.error_code,
                    _sanitize_message(outcome.error_message or "CLI 图片任务执行失败"),
                    retryable=outcome.error_code in _RETRYABLE,
                )
            if outcome.exit_code:
                raise ProviderAdapterError("UPSTREAM", "CLI 图片任务执行失败", retryable=True)
            result = self._read_result(run_id, run_directory)
            self._finish(
                run_id,
                state="COMPLETED",
                exit_code=outcome.exit_code,
                output_manifest={"images": list(result.image_metadata)},
                stdout_checksum=stdout_checksum,
                stderr_checksum=stderr_checksum,
            )
            cleanup_error = self._cleanup(run_id, retain=False)
            if cleanup_error is not None:
                # Contract §9.4: one immediate, bounded retry for a successful
                # run (no sleep, no queue); token, canonical-path and link
                # revalidation rerun inside _cleanup itself.
                cleanup_error = self._cleanup(run_id, retain=False)
            if cleanup_error is None:
                return result
            return CLIExecutionResult(
                run_id=result.run_id,
                images=result.images,
                image_metadata=result.image_metadata,
                usage=result.usage,
                cleanup_error=type(cleanup_error).__name__,
            )
        except ProviderAdapterError as error:
            self._finish_failure(run_id, error, outcome)
            cleanup_error = self._cleanup(run_id, retain=error.code in _RETAIN)
            if cleanup_error:
                error.add_note(f"CLI run cleanup failed: {type(cleanup_error).__name__}")
            raise
        except BaseException as unexpected:
            error = ProviderAdapterError("CRASH", "CLI controller 异常终止")
            self._finish_failure(run_id, error, outcome)
            self._cleanup(run_id, retain=True)
            raise error from unexpected

    def recover_abandoned(
        self,
        *,
        controller_is_active: Callable[[CLIExecutionRun, dict], bool],
        preparing_grace_seconds: float | None = None,
    ) -> list[str]:
        """Release only runs whose recorded controller ownership is proven dead.

        A row whose liveness probe raises (incomplete journal identity, e.g. a
        PREPARING row that never reached ``_mark_running``) is decided by age:
        a live controller spends at most seconds in PREPARING, so rows older
        than the grace window are provably abandoned while fresh rows are
        left for a later pass instead of aborting the whole scan.
        """

        grace = (
            preparing_grace_seconds
            if preparing_grace_seconds is not None
            else float(max(getattr(self.settings, "job_lease_seconds", 120) * 2, 300))
        )
        with self.session_factory() as db:
            rows = list(
                db.scalars(
                    select(CLIExecutionRun).where(
                        CLIExecutionRun.state.in_(("PREPARING", "RUNNING")),
                        CLIExecutionRun.lease_slot.is_not(None),
                    )
                )
            )
            for row in rows:
                db.expunge(row)
        recovered: list[str] = []
        for row in rows:
            try:
                run_directory, journal = self._validate_directory(row)
            except (ProviderAdapterError, OSError):
                # An unverifiable directory (unparseable journal, link attack)
                # is treated as abandoned like the original behavior — but a
                # transient storage error must not kill a live run, so only
                # rows past the grace window are released here too.
                if not self._row_is_past_grace(row, grace):
                    continue
                self._finish_recoverable(
                    row.id,
                    error_message="CLI controller 已停止，且 run 目录无法验证",
                )
                self._set_cleanup(row.id, "FAILED")
                recovered.append(row.id)
                continue
            try:
                active = controller_is_active(row, journal)
            except Exception:
                if not self._row_is_past_grace(row, grace):
                    continue
                active = False
            if active:
                continue
            if not self._finish_recoverable(
                row.id, error_message="CLI controller 已停止，输出未采用"
            ):
                # The run reached a terminal state (e.g. the controller
                # completed it) between our SELECT and this write; keep its
                # result and its slot release untouched.
                continue
            journal["state"] = "RETAINED"
            _write_json(run_directory / "journal.json", journal)
            self._set_cleanup(row.id, "RETAINED")
            recovered.append(row.id)
        return recovered

    @staticmethod
    def _row_is_past_grace(row: CLIExecutionRun, grace: float) -> bool:
        created_at = getattr(row, "created_at", None)
        if created_at is None:
            return False
        if created_at.tzinfo is None:
            # SQLite returns naive datetimes despite timezone=True.
            created_at = created_at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - created_at).total_seconds() >= grace

    def _finish_recoverable(self, run_id: str, *, error_message: str) -> bool:
        """Fail an abandoned run only if it is still in a recoverable state."""

        with self.session_factory() as db:
            row = db.get(CLIExecutionRun, run_id)
            if row is None:
                return False
            if row.state not in ("PREPARING", "RUNNING") or row.lease_slot is None:
                return False
            row.state, row.lease_slot = "FAILED", None
            row.finished_at = datetime.now(UTC)
            row.error_code = "CRASH"
            row.error_message = _sanitize_message(error_message)
            db.commit()
        return True

    def _create_directory(self, run_id: str) -> Path:
        self.settings.storage_root.mkdir(parents=True, exist_ok=True)
        _reject_link(self.settings.storage_root)
        storage_root = self.settings.storage_root.resolve(strict=True)
        cli_root = storage_root / "cli_runs"
        cli_root.mkdir(exist_ok=True)
        _reject_link(cli_root)
        run_directory = cli_root / run_id
        run_directory.mkdir()
        for relative in ("input/references", "workspace", "output/images"):
            (run_directory / relative).mkdir(parents=True)
        return run_directory.resolve(strict=True)

    def _copy_references(self, run_directory: Path, sources: tuple[Path, ...]) -> list[str]:
        names: list[str] = []
        for source in sources:
            resolved = _validate_reference(
                source, self.settings.upload_root, self.settings.storage_root
            )
            if resolved.stat().st_size > self.settings.max_upload_bytes:
                raise ProviderAdapterError("CONFIGURATION", "CLI 参考图超过大小上限")
            try:
                _, _, _, suffix = inspect_upload_image(
                    resolved,
                    max_pixels=self.settings.max_image_pixels,
                    max_side=self.settings.max_image_side,
                )
            except ValueError as error:
                raise ProviderAdapterError("CONFIGURATION", str(error)) from error
            name = f"input/references/{_file_sha256(resolved)}{suffix}"
            target = run_directory / name
            if not target.exists():
                shutil.copyfile(resolved, target)
                target.chmod(stat.S_IREAD)
            names.append(name)
        return names

    def _copy_reference_payloads(
        self,
        run_directory: Path,
        payloads: tuple[bytes, ...],
        mime_types: tuple[str, ...],
    ) -> list[str]:
        if mime_types and len(mime_types) != len(payloads):
            raise ProviderAdapterError("CONFIGURATION", "CLI 参考图类型数量不匹配")
        names: list[str] = []
        for index, payload in enumerate(payloads):
            if not payload or len(payload) > self.settings.max_upload_bytes:
                raise ProviderAdapterError("CONFIGURATION", "CLI 参考图大小无效")
            digest = hashlib.sha256(payload).hexdigest()
            pending = run_directory / "input" / "references" / f"{digest}.image"
            _write_bytes(pending, payload)
            try:
                _width, _height, detected_mime, suffix = inspect_upload_image(
                    pending,
                    max_pixels=self.settings.max_image_pixels,
                    max_side=self.settings.max_image_side,
                )
            except ValueError as error:
                pending.unlink(missing_ok=True)
                raise ProviderAdapterError("CONFIGURATION", str(error)) from error
            expected_mime = mime_types[index] if mime_types else None
            if expected_mime and expected_mime != detected_mime:
                pending.unlink(missing_ok=True)
                raise ProviderAdapterError("CONFIGURATION", "CLI 参考图类型与内容不一致")
            relative = f"input/references/{digest}{suffix}"
            target = run_directory / relative
            if target.exists():
                pending.unlink()
            else:
                pending.replace(target)
                target.chmod(stat.S_IREAD)
            names.append(relative)
        return names

    def _claim(self, **values) -> None:
        last_error: IntegrityError | None = None
        for slot in range(1, self.settings.cli_channel_max_concurrency + 1):
            with self.session_factory() as db:
                db.add(
                    CLIExecutionRun(
                        id=values["run_id"],
                        job_id=values["job_id"],
                        model_call_attempt_id=values["attempt_id"],
                        connection_id=values["connection_id"],
                        catalog_model_id=values["model_id"],
                        run_token=values["token"],
                        relative_path=values["relative_path"],
                        operation=values["operation"],
                        state="PREPARING",
                        cleanup_state="PENDING",
                        lease_slot=slot,
                        request_checksum=values["request_checksum"],
                        output_manifest={},
                    )
                )
                try:
                    db.commit()
                    return
                except IntegrityError as error:
                    db.rollback()
                    last_error = error
        with self.session_factory() as db:
            occupied = set(
                db.scalars(
                    select(CLIExecutionRun.lease_slot).where(
                        CLIExecutionRun.connection_id == values["connection_id"],
                        CLIExecutionRun.lease_slot.is_not(None),
                    )
                )
            )
        if len(occupied) >= self.settings.cli_channel_max_concurrency:
            raise ProviderAdapterError(
                "CONCURRENCY_LIMIT", "CLI 通道并发名额已满，请稍后重试", retryable=True
            )
        if last_error:
            raise last_error
        raise RuntimeError("CLI run slot allocation failed")

    def _load(self, run_id: str) -> CLIExecutionRun:
        with self.session_factory() as db:
            row = db.get(CLIExecutionRun, run_id)
            if row is None:
                raise ProviderAdapterError("CONFIGURATION", "CLI run 不存在")
            db.expunge(row)
            return row

    def _validate_directory(self, row: CLIExecutionRun) -> tuple[Path, dict]:
        storage_root = self.settings.storage_root.resolve(strict=True)
        candidate = (storage_root / row.relative_path).absolute()
        _reject_link(candidate)
        resolved = candidate.resolve(strict=True)
        expected = (storage_root / "cli_runs" / row.id).resolve(strict=True)
        if resolved != expected or not resolved.is_relative_to(storage_root):
            raise ProviderAdapterError("CONFIGURATION", "CLI run 目录归属校验失败")
        journal_path = resolved / "journal.json"
        _reject_link(journal_path)
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProviderAdapterError("CONFIGURATION", "CLI run journal 无法验证") from error
        if (
            journal.get("version") != 1
            or journal.get("run_id") != row.id
            or journal.get("token") != row.run_token
        ):
            raise ProviderAdapterError("CONFIGURATION", "CLI run 所有权标记已改变")
        return resolved, journal

    def _mark_running(
        self, run_id: str, run_directory: Path, journal: dict, identity: dict | None
    ) -> None:
        identity = identity or {"pid": os.getpid()}
        if type(identity.get("pid")) is not int or identity["pid"] <= 0:
            raise ProviderAdapterError("CONFIGURATION", "CLI controller identity 无效")
        with self.session_factory() as db:
            row = db.get(CLIExecutionRun, run_id)
            if row is None or row.state != "PREPARING" or row.lease_slot is None:
                raise ProviderAdapterError("CONFIGURATION", "CLI run 状态不可执行")
            row.state, row.started_at = "RUNNING", datetime.now(UTC)
            db.commit()
        journal.update(state="RUNNING", controller_pid=identity["pid"])
        if type(identity.get("created")) is int:
            journal["controller_created"] = identity["created"]
        _write_json(run_directory / "journal.json", journal)

    def _environment(
        self,
        workspace: Path,
        allowed: tuple[str, ...],
        overrides: dict[str, str] | None,
    ) -> dict[str, str]:
        return build_cli_environment(workspace, allowed, overrides)

    def _diagnostics(
        self, run_directory: Path, outcome: CLIProcessOutcome, encoding: str
    ) -> tuple[str, str]:
        stdout_checksum = outcome.stdout_checksum or hashlib.sha256(outcome.stdout).hexdigest()
        stderr_checksum = outcome.stderr_checksum or hashlib.sha256(outcome.stderr).hexdigest()
        _write_bytes(
            run_directory / "output" / "stdout.log",
            _sanitize_diagnostic(outcome.stdout, encoding),
        )
        _write_bytes(
            run_directory / "output" / "stderr.log",
            _sanitize_diagnostic(outcome.stderr, encoding),
        )
        return stdout_checksum, stderr_checksum

    def _read_result(self, run_id: str, run_directory: Path) -> CLIExecutionResult:
        request_path = run_directory / "input" / "request.json"
        _reject_link(request_path)
        try:
            request_bytes = request_path.read_bytes()
        except OSError as error:
            raise ProviderAdapterError("INVALID_OUTPUT", "CLI 结构化请求无法复核") from error
        row = self._load(run_id)
        if hashlib.sha256(request_bytes).hexdigest() != row.request_checksum:
            raise ProviderAdapterError("INVALID_OUTPUT", "CLI 结构化请求已被修改")
        try:
            request = json.loads(request_bytes)
        except json.JSONDecodeError as error:
            raise ProviderAdapterError("INVALID_OUTPUT", "CLI 结构化请求已损坏") from error
        result_path = run_directory / "output" / "result.json"
        if not result_path.exists():
            raise ProviderAdapterError("UNKNOWN_RESULT", "CLI 未返回结构化结果")
        _reject_link(result_path)
        if result_path.stat().st_size > self.settings.max_provider_metadata_bytes:
            raise ProviderAdapterError("INVALID_OUTPUT", "CLI 结果文件超过大小上限")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProviderAdapterError("INVALID_OUTPUT", "CLI 结果不是有效 JSON") from error
        if not isinstance(result, dict) or result.get("schema_version") != 1:
            raise ProviderAdapterError("INVALID_OUTPUT", "CLI 结果 schema 不受支持")
        status = result.get("status")
        if status == "FAILED":
            failure = result.get("error") if isinstance(result.get("error"), dict) else {}
            code = str(failure.get("code") or "UPSTREAM").upper()
            if not _SAFE_CODE.fullmatch(code) or code not in CLI_FAILURE_CODES:
                code = "UPSTREAM"
            raise ProviderAdapterError(
                code,
                _sanitize_message(str(failure.get("message") or "CLI 执行失败")),
                retryable=code in _RETRYABLE,
            )
        if status == "PARTIAL":
            raise ProviderAdapterError("PARTIAL_OUTPUT", "CLI 只返回了部分图片")
        if status != "SUCCEEDED":
            raise ProviderAdapterError("INVALID_OUTPUT", "CLI 结果状态无效")
        output_spec = request.get("output_spec", {})
        registered, max_images, images = (
            output_spec.get("images"),
            output_spec.get("max_images"),
            result.get("images"),
        )
        if (
            not isinstance(registered, list)
            or not isinstance(max_images, int)
            or not isinstance(images, list)
            or not images
            or len(images) != len(registered)
            or len(images) > max_images
            or any(not isinstance(path, str) for path in images)
            or any(path not in registered for path in images)
        ):
            # Contract §7.4: a SUCCEEDED envelope must carry every registered
            # image — a strict subset is a partial output and is never adopted.
            raise ProviderAdapterError("PARTIAL_OUTPUT", "CLI 输出清单不完整")
        _reject_link_chain(run_directory / "output", run_directory)
        output_root = (run_directory / "output").resolve(strict=True)
        payloads: list[bytes] = []
        metadata: list[dict] = []
        for relative in images:
            candidate = (run_directory / relative).absolute()
            _reject_link(candidate)
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as error:
                raise ProviderAdapterError("PARTIAL_OUTPUT", "CLI 登记的图片不存在") from error
            if not resolved.is_relative_to(output_root) or not resolved.is_file():
                raise ProviderAdapterError("INVALID_OUTPUT", "CLI 输出路径越界")
            if resolved.stat().st_size > self.settings.max_upload_bytes:
                raise ProviderAdapterError("INVALID_OUTPUT", "CLI 输出图片超过大小上限")
            try:
                width, height, mime, _ = inspect_upload_image(
                    resolved,
                    max_pixels=self.settings.max_image_pixels,
                    max_side=self.settings.max_image_side,
                )
            except ValueError as error:
                raise ProviderAdapterError("INVALID_OUTPUT", str(error)) from error
            content = resolved.read_bytes()
            payloads.append(content)
            metadata.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                    "width": width,
                    "height": height,
                    "mime_type": mime,
                }
            )
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        usage = {**usage, "estimated_cost": None, "cost_source": "CLI_EXTERNAL"}
        return CLIExecutionResult(run_id, tuple(payloads), tuple(metadata), usage)

    def _finish_failure(
        self, run_id: str, error: ProviderAdapterError, outcome: CLIProcessOutcome | None
    ) -> None:
        self._finish(
            run_id,
            state="FAILED",
            exit_code=outcome.exit_code if outcome else None,
            output_manifest={},
            stdout_checksum=(
                outcome.stdout_checksum or hashlib.sha256(outcome.stdout).hexdigest()
                if outcome
                else None
            ),
            stderr_checksum=(
                outcome.stderr_checksum or hashlib.sha256(outcome.stderr).hexdigest()
                if outcome
                else None
            ),
            error_code=error.code,
            error_message=error.user_message,
        )

    def _finish(
        self,
        run_id: str,
        *,
        state: str,
        exit_code: int | None,
        output_manifest: dict,
        stdout_checksum: str | None,
        stderr_checksum: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.session_factory() as db:
            row = db.get(CLIExecutionRun, run_id)
            if row is None:
                raise RuntimeError("CLI run disappeared during finalization")
            row.state, row.lease_slot = state, None
            row.exit_code, row.output_manifest = exit_code, output_manifest
            row.stdout_checksum, row.stderr_checksum = stdout_checksum, stderr_checksum
            row.finished_at = datetime.now(UTC)
            row.error_code = error_code
            row.error_message = _sanitize_message(error_message) if error_message else None
            db.commit()

    def _set_cleanup(self, run_id: str, state: str) -> None:
        with self.session_factory() as db:
            row = db.get(CLIExecutionRun, run_id)
            if row:
                row.cleanup_state = state
                db.commit()

    def _cleanup(self, run_id: str, *, retain: bool) -> BaseException | None:
        try:
            row = self._load(run_id)
            run_directory, journal = self._validate_directory(row)
            if retain:
                journal["state"] = "RETAINED"
                _write_json(run_directory / "journal.json", journal)
                state = "RETAINED"
            else:
                _make_inputs_writable(run_directory)
                shutil.rmtree(run_directory)
                state = "CLEANED"
            self._set_cleanup(run_id, state)
            return None
        except BaseException as error:
            self._set_cleanup(run_id, "FAILED")
            return error


def _validate_request(request: CLIExecutionRequest) -> None:
    if request.operation not in {"image_generate", "image_edit"}:
        raise ProviderAdapterError("CONFIGURATION", "CLI 操作类型不受支持")
    if not request.prompt.strip():
        raise ProviderAdapterError("CONFIGURATION", "CLI 图片提示词不能为空")
    if request.reference_files and request.reference_payloads:
        raise ProviderAdapterError("CONFIGURATION", "CLI 参考图来源不能混用")
    if request.reference_mime_types and not request.reference_payloads:
        raise ProviderAdapterError("CONFIGURATION", "CLI 参考图类型缺少对应内容")
    if not 1 <= request.max_images <= 4:
        raise ProviderAdapterError("CONFIGURATION", "CLI 输出图片数量不受支持")
    if not request.output_images or len(request.output_images) > request.max_images:
        raise ProviderAdapterError("CONFIGURATION", "CLI 输出清单数量无效")
    for value in request.output_images:
        path = Path(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 3
            or path.parts[:2] != ("output", "images")
        ):
            raise ProviderAdapterError("CONFIGURATION", "CLI 输出清单路径无效")


def build_cli_environment(
    workspace: Path,
    allowed: tuple[str, ...],
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the explicit environment shared by run and read-only probe processes."""

    protected = {"PYTHONHOME", "PYTHONPATH"}
    if any(name.upper() in protected for name in allowed):
        raise ProviderAdapterError("CONFIGURATION", "CLI 环境白名单包含受保护变量")
    names = {"PATH", "SYSTEMROOT", "WINDIR", *allowed}
    environment = {
        name: os.environ[name]
        for name in names
        if name in os.environ and name.upper() not in protected
    }
    allowed_names = {name.upper(): name for name in allowed}
    for name, value in (overrides or {}).items():
        normalized = name.upper()
        if normalized not in allowed_names or normalized in protected:
            raise ProviderAdapterError("CONFIGURATION", "CLI 环境覆盖变量不在白名单")
        if not isinstance(value, str) or not value or len(value) > 2000 or "\0" in value:
            raise ProviderAdapterError("CONFIGURATION", "CLI 环境覆盖值无效")
        environment[allowed_names[normalized]] = value
    environment.update(TEMP=str(workspace), TMP=str(workspace))
    return environment


def _validate_argv(argv: tuple[str, ...]) -> None:
    if not argv or any(not isinstance(item, str) or not item or "\0" in item for item in argv):
        raise ProviderAdapterError("CONFIGURATION", "CLI argv 无效")


def _validate_reference(source: Path, upload_root: Path, storage_root: Path) -> Path:
    _reject_link(source)
    try:
        absolute, resolved = source.absolute(), source.resolve(strict=True)
    except OSError as error:
        raise ProviderAdapterError("CONFIGURATION", "CLI 参考图不存在") from error
    if absolute != resolved:
        raise ProviderAdapterError("CONFIGURATION", "CLI 参考图路径不能经过链接")
    roots = []
    for root in (upload_root, storage_root):
        if root.exists():
            _reject_link(root)
            roots.append(root.resolve(strict=True))
    if not any(resolved.is_relative_to(root) for root in roots):
        raise ProviderAdapterError("CONFIGURATION", "CLI 参考图路径越界")
    cli_root = storage_root.resolve(strict=True) / "cli_runs"
    if cli_root.exists() and resolved.is_relative_to(cli_root):
        raise ProviderAdapterError("CONFIGURATION", "CLI run 输出不能作为参考图源")
    if not resolved.is_file():
        raise ProviderAdapterError("CONFIGURATION", "CLI 参考图不是普通文件")
    return resolved


def _reject_link(path: Path) -> None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ProviderAdapterError("CONFIGURATION", "CLI 路径不能是链接")


def _reject_link_chain(path: Path, root: Path) -> None:
    """Reject a path whose intermediate components are links, not just the leaf.

    A junction or symlink at an interior component (e.g. ``<run>/output``
    itself) is not a symlink at the leaf, so per-file ``_reject_link`` checks
    pass while the resolved content lives outside the run directory.
    """

    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise ProviderAdapterError("INVALID_OUTPUT", "CLI 路径越界") from error
    current = root.absolute()
    for part in relative.parts:
        current /= part
        if current.is_symlink() or (
            hasattr(current, "is_junction") and current.is_junction()
        ):
            raise ProviderAdapterError("INVALID_OUTPUT", "CLI 路径不能经过链接")


def _make_inputs_writable(run_directory: Path) -> None:
    input_root = run_directory / "input"
    if not input_root.exists():
        return
    _reject_link(input_root)
    for path in input_root.rglob("*"):
        _reject_link(path)
        if path.is_file():
            path.chmod(stat.S_IREAD | stat.S_IWRITE)


def _canonical_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _write_json(path: Path, value: dict) -> None:
    _write_bytes(path, _canonical_json(value))


def _write_bytes(path: Path, value: bytes) -> None:
    _reject_link(path)
    pending = path.with_suffix(path.suffix + ".pending")
    _reject_link(pending)
    with pending.open("wb") as file:
        file.write(value)
        file.flush()
        os.fsync(file.fileno())
    pending.replace(path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_diagnostic(value: bytes, encoding: str) -> bytes:
    try:
        text = value.decode("utf-8-sig" if value.startswith(b"\xef\xbb\xbf") else encoding)
    except (LookupError, UnicodeDecodeError):
        try:
            text = value.decode("cp936")
        except UnicodeDecodeError:
            text = value.decode("utf-8", errors="replace")
    lines = ["[redacted]" if _TOKEN_LINE.search(line) else line for line in text.splitlines()]
    return "\n".join(lines).encode()[:_DIAGNOSTIC_LIMIT]


def _sanitize_message(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(
        "[redacted]" if _TOKEN_LINE.search(line) else line for line in value.splitlines()
    )[:500]


def posix_controller_is_active(_row: CLIExecutionRun, journal: dict) -> bool:
    """Best-effort POSIX liveness probe mirroring the Windows identity check.

    The product targets Windows CLI channels; this fallback keeps recovery
    correct on POSIX dev/test hosts. PID existence is checked with
    ``os.kill(pid, 0)``. ``controller_created`` is deliberately ignored: the
    only writer today is the Windows runner (FILETIME since 1601), which is
    dimensionally incompatible with ``/proc`` start ticks — comparing them
    would misjudge a live controller as dead. If a POSIX runner ever records
    its own start time, it must use ``/proc`` ticks for this check to adopt
    recycled-PID protection.
    """

    pid = journal.get("controller_pid")
    if type(pid) is not int or pid <= 0:
        raise RuntimeError("CLI controller identity is incomplete")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def platform_controller_is_active(row: CLIExecutionRun, journal: dict) -> bool:
    if sys.platform == "win32":
        from app.services.cli_process_windows import windows_controller_is_active

        return windows_controller_is_active(row, journal)
    return posix_controller_is_active(row, journal)


def recover_abandoned_cli_runs(
    settings: Settings | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> list[str]:
    """Production entry point for CLI slot recovery (contract §9.3).

    Called at API startup and from the periodic recovery pass: without it, a
    controller that died mid-run held its ``(connection_id, lease_slot)``
    slot forever and every later CLI task failed with CONCURRENCY_LIMIT.
    """

    from app.config import get_settings
    from app.database import SessionLocal

    controller = CLIExecutionController(
        settings or get_settings(), session_factory or SessionLocal
    )
    return controller.recover_abandoned(controller_is_active=platform_controller_is_active)
