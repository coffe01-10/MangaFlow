"""Offline V02-14A acceptance for the Codex CLI image channel."""

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.model_adapters.base import ImageRequest, ProviderAdapterError
from app.model_adapters.codex_cli import (
    CodexCLIImageAdapter,
    CodexCLIProbeAdapter,
    CodexCLIRuntime,
    resolve_codex_executable,
)
from app.models import (
    AIModel,
    CLIExecutionRun,
    GenerationJob,
    ModelCallAttempt,
    Project,
    ProviderConnection,
    ProviderProfile,
)
from app.services.cli_executor import (
    CLIExecutionController,
    CLIProcessOutcome,
)
from app.services.model_router import AdapterBinding, ResolvedModel, bind_adapter
from app.services.provider_presets import ensure_provider_presets
from app.services.worker_handlers import provider as provider_handler


def _png_bytes() -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGB", (8, 8), "navy").save(buffer, format="PNG")
    return buffer.getvalue()


def _fake_probe_runner(argv: tuple[str, ...]) -> CLIProcessOutcome:
    if argv[-1] == "--version":
        return CLIProcessOutcome(0, stdout=b"codex-cli 1.2.3\n")
    if argv[-2:] == ("login", "status"):
        return CLIProcessOutcome(0, stdout=b"Logged in using ChatGPT")
    if argv[-1] == "--help" and "exec" not in argv[1:]:
        return CLIProcessOutcome(
            0,
            stdout=b"exec --add-dir --sandbox --ask-for-approval",
        )
    if argv[-2:] == ("exec", "--help"):
        return CLIProcessOutcome(
            0,
            stdout=(
                b"--image --ephemeral --ignore-user-config "
                b"--skip-git-repo-check --color"
            ),
        )
    if argv[-1] == "--help" and "exec" in argv[1:]:
        return CLIProcessOutcome(0, stdout=b"parsed automation argv")
    raise AssertionError(argv)


def _probe_adapter(**overrides) -> CodexCLIProbeAdapter:
    return CodexCLIProbeAdapter(
        Settings(),
        executable="codex",
        command_runner=overrides.get("command_runner", _fake_probe_runner),
        executable_finder=lambda _value: "C:/tools/codex.exe",
    )


def test_codex_probe_parses_official_version_login_and_exec_surfaces():
    adapter = _probe_adapter()

    assert adapter.presence().status == "PASSED"
    version = adapter.version()
    assert version.status == "PASSED" and version.metrics["version"] == "1.2.3"
    assert adapter.login().status == "PASSED"
    capability = adapter.capability()
    assert capability.status == "PASSED"
    assert capability.metrics["operations"] == ["image_generate", "image_edit"]


def test_codex_npm_shim_resolves_bounded_native_executable(tmp_path, monkeypatch):
    monkeypatch.setattr("app.model_adapters.codex_cli.platform.machine", lambda: "AMD64")
    shim = tmp_path / "codex.cmd"
    shim.write_text("official npm shim", encoding="utf-8")
    native = (
        tmp_path
        / "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64"
        / "vendor/x86_64-pc-windows-msvc/bin/codex.exe"
    )
    native.parent.mkdir(parents=True)
    native.write_bytes(b"fake native executable")

    assert resolve_codex_executable(str(shim)) == str(native.resolve())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("login", "UNAUTHENTICATED"), ("capability", "UNSUPPORTED")],
)
def test_codex_probe_fails_closed_for_missing_login_or_automation(failure, expected):
    def runner(argv: tuple[str, ...]) -> CLIProcessOutcome:
        if failure == "login" and argv[-2:] == ("login", "status"):
            return CLIProcessOutcome(1, stderr=b"Not logged in")
        if failure == "capability" and argv[-2:] == ("exec", "--help"):
            return CLIProcessOutcome(0, stdout=b"--ephemeral")
        return _fake_probe_runner(argv)

    adapter = _probe_adapter(command_runner=runner)
    assert adapter.presence().status == "PASSED"
    assert adapter.version().status == "PASSED"
    observation = adapter.login()
    if failure == "login":
        assert observation.error_code == expected
        return
    assert observation.status == "PASSED"
    assert adapter.capability().error_code == expected


def test_unconfigured_codex_connection_can_run_read_only_probe(
    client, db_session, monkeypatch
):
    ensure_provider_presets(db_session, get_settings(), auto_commit=True)
    profile = db_session.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "codex-cli")
    )
    connection = db_session.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    assert connection.enabled is False and connection.health_state == "UNKNOWN"
    monkeypatch.setattr(
        "app.services.connection_verifier.CodexCLIProbeAdapter",
        lambda *_args, **_kwargs: _probe_adapter(),
    )

    response = client.post(
        f"/api/v1/providers/connections/{connection.id}/verify",
        json={"level": "CREDENTIALS"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["health"]["health_state"] == "AVAILABLE"
    assert payload["health"]["configured"] is True
    assert payload["probe"]["probe_type"] == "CLI_CAPABILITY"
    db_session.refresh(connection)
    assert connection.enabled is False
    assert connection.nonsecret_config["cli_version"] == "1.2.3"


def test_codex_executable_update_rejects_relative_path_and_requires_reprobe(
    client, db_session
):
    ensure_provider_presets(db_session, get_settings(), auto_commit=True)
    profile = db_session.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "codex-cli")
    )
    connection = db_session.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    connection.health_state = "AVAILABLE"
    db_session.commit()

    rejected = client.patch(
        f"/api/v1/providers/connections/{connection.id}",
        json={
            "version": connection.version,
            "nonsecret_config": {"cli_executable": "tools/codex.exe"},
        },
    )
    assert rejected.status_code == 422

    updated = client.patch(
        f"/api/v1/providers/connections/{connection.id}",
        json={
            "version": connection.version,
            "nonsecret_config": {"cli_executable": "C:\\tools\\codex.exe"},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["health_state"] == "UNKNOWN"
    assert updated.json()["nonsecret_config"]["cli_executable"] == "C:\\tools\\codex.exe"


def test_available_enabled_codex_model_binds_without_api_key(db_session):
    settings = get_settings()
    ensure_provider_presets(db_session, settings, auto_commit=True)
    profile = db_session.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "codex-cli")
    )
    connection = db_session.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    model = db_session.scalar(
        select(AIModel).where(AIModel.connection_id == connection.id)
    )
    connection.enabled = True
    connection.health_state = "AVAILABLE"
    db_session.commit()

    binding = bind_adapter(
        db_session,
        settings,
        operation="image_generate",
        explicit_reference=model.id,
    )

    assert isinstance(binding.adapter, CodexCLIImageAdapter)
    assert binding.selected_key is None


def _cli_rows(db_session, tmp_path):
    factory = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    settings = Settings(
        storage_root=tmp_path / "storage",
        upload_root=tmp_path / "uploads",
        cli_run_timeout_seconds=30,
    )
    settings.ensure_directories()
    project = Project(name="Codex CLI 离线项目")
    profile = ProviderProfile(
        preset_key="codex-cli", name="Codex CLI", enabled=True, built_in=True
    )
    db_session.add_all([project, profile])
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="默认连接",
        protocol="CLI_CODEX",
        base_url="cli://codex",
        enabled=True,
        health_state="AVAILABLE",
        nonsecret_config={"cli_executable": "codex"},
    )
    db_session.add(connection)
    db_session.flush()
    model = AIModel(
        connection_id=connection.id,
        provider_model_id="codex-imagegen",
        display_name="Codex CLI ImageGen",
        model_type="IMAGE",
        input_modalities=["TEXT", "IMAGE"],
        output_modalities=["IMAGE"],
        operations=["image_generate", "image_edit"],
        capabilities={"resolutions": ["1K"], "max_reference_images": 5},
        confidence="DECLARED",
        enabled=True,
    )
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="target-1",
        job_type="PAGE_GENERATE",
        status="GENERATING",
        attempt_count=1,
    )
    db_session.add_all([model, job])
    db_session.commit()
    return factory, settings, profile, connection, model, job


class _SuccessfulCodexRunner:
    """Fake CLI agent that resolves paths the way the task text mandates.

    Registered ``output/`` paths are relative to the run root, so from the
    workspace cwd they must be written with a ``../`` prefix; ``result.json``
    keeps the original registered strings. Writing anywhere else makes the
    controller reject the output, so this runner cannot mask a wrong mapping.
    """

    def __init__(self):
        self.calls = 0

    def run(self, *, argv, cwd: Path, environment, **_kwargs):
        self.calls += 1
        assert argv[:2] == ("C:/tools/codex.exe", "--sandbox")
        assert argv.index("--ask-for-approval") < argv.index("exec")
        assert argv.index("--add-dir") < argv.index("exec")
        assert argv.index("--ephemeral") > argv.index("exec")
        assert argv.index("--color") > argv.index("exec")
        assert "漫画私密提示" not in argv
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv
        assert "--image" in argv
        assert set(environment) <= {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "CODEX_HOME",
            "TEMP",
            "TMP",
        }
        task = argv[-1]
        assert "registered relative to the run root" in task
        assert "prefixed with ../" in task
        request = json.loads((cwd / "../input/request.json").read_text("utf-8"))
        assert request["operation"] == "image_edit"
        assert request["prompt"] == "漫画私密提示"
        assert request["reference_images"][0].startswith("input/references/")
        registered = request["output_spec"]["images"]
        for path in registered:
            (cwd / ".." / path).write_bytes(_png_bytes())
        (cwd / "../output/result.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "SUCCEEDED",
                    "images": list(registered),
                }
            ),
            encoding="utf-8",
        )
        return CLIProcessOutcome(0, stdout=b"done")


class _FailingCodexRunner:
    def __init__(self):
        self.calls = 0

    def run(self, **_kwargs):
        self.calls += 1
        return CLIProcessOutcome(2, stderr=b"upstream failed")


def _adapter(factory, settings, connection, model, runner, executable_resolver=None):
    return CodexCLIImageAdapter(
        CodexCLIRuntime(
            settings=settings,
            connection_id=connection.id,
            catalog_model_id=model.id,
            provider_model_id=model.provider_model_id,
            session_factory=factory,
        ),
        controller=CLIExecutionController(settings, factory),
        runner_factory=lambda: runner,
        executable_resolver=executable_resolver
        or (lambda _value: "C:/tools/codex.exe"),
    )


class _ArgvCaptureRunner:
    def __init__(self):
        self.argv: tuple[str, ...] | None = None

    def run(self, *, argv, **_kwargs):
        self.argv = argv
        return CLIProcessOutcome(2, stderr=b"captured")


def test_codex_image_edit_maps_request_and_registered_output_through_controller(
    db_session, tmp_path
):
    factory, settings, _profile, connection, model, job = _cli_rows(db_session, tmp_path)
    attempt = ModelCallAttempt(
        job_id=job.id,
        project_id=job.project_id,
        job_attempt=1,
        dispatch_no=1,
        provider="codex-cli",
        model_id=model.provider_model_id,
        catalog_model_id=model.id,
        connection_id=connection.id,
    )
    db_session.add(attempt)
    db_session.commit()
    runner = _SuccessfulCodexRunner()
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    response = adapter.edit_region(
        ImageRequest(
            prompt="漫画私密提示",
            reference_images=(_png_bytes(),),
            reference_mime_types=("image/png",),
        )
    )

    assert runner.calls == 1 and response.images
    assert response.usage == {"estimated_cost": None, "cost_source": "CLI_EXTERNAL"}
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.cleanup_state, run.lease_slot) == (
        "COMPLETED",
        "CLEANED",
        None,
    )
    assert not (settings.storage_root / "cli_runs" / run.id).exists()


def test_codex_failure_finalizes_one_audit_without_http_fallback(db_session, tmp_path):
    factory, settings, profile, connection, model, job = _cli_rows(db_session, tmp_path)
    runner = _FailingCodexRunner()
    adapter = _adapter(factory, settings, connection, model, runner)
    binding = AdapterBinding(
        resolved=ResolvedModel(model=model, connection=connection, provider=profile),
        adapter=adapter,
        selected_key=None,
    )
    db_session.info["job_id"] = job.id
    callback_calls = 0

    def invoke(bound_adapter):
        nonlocal callback_calls
        callback_calls += 1
        return bound_adapter.generate_asset(ImageRequest(prompt="失败测试"))

    with pytest.raises(ProviderAdapterError) as caught:
        provider_handler._invoke_provider(db_session, binding, invoke)

    assert caught.value.code == "UPSTREAM"
    assert runner.calls == callback_calls == 1
    attempt = db_session.scalar(select(ModelCallAttempt))
    run = db_session.scalar(select(CLIExecutionRun))
    assert (attempt.outcome, attempt.error_code) == ("FAILED", "UPSTREAM")
    assert (run.state, run.cleanup_state) == ("FAILED", "CLEANED")


def test_codex_task_instructions_map_outputs_to_run_root_and_preserve_registration(
    db_session, tmp_path
):
    factory, settings, _profile, connection, model, job = _cli_rows(db_session, tmp_path)
    attempt = ModelCallAttempt(
        job_id=job.id,
        project_id=job.project_id,
        job_attempt=1,
        dispatch_no=1,
        provider="codex-cli",
        model_id=model.provider_model_id,
        catalog_model_id=model.id,
        connection_id=connection.id,
    )
    db_session.add(attempt)
    db_session.commit()
    runner = _ArgvCaptureRunner()
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    with pytest.raises(ProviderAdapterError):
        adapter.edit_region(
            ImageRequest(
                prompt="漫画私密提示",
                reference_images=(_png_bytes(),),
                reference_mime_types=("image/png",),
            )
        )

    task = runner.argv[-1]
    # Write mapping: registered output/ paths belong to the run root, so a
    # conforming agent must write them from the workspace with the ../ prefix.
    assert "registered relative to the run root" in task
    assert "prefixed with ../" in task
    assert "output/images/out_001.png is written as ../output/images/out_001.png" in task
    # result.json constraint: the original registered strings stay unchanged.
    assert "keep the registered output_spec.images strings unchanged" in task
    assert "start with output/ and never carry the ../ prefix" in task
    assert "../output/result.json" in task


def test_codex_cleanup_retry_after_transient_failure_cleans_without_warning(
    db_session, tmp_path, monkeypatch
):
    factory, settings, profile, connection, model, job = _cli_rows(db_session, tmp_path)
    runner = _SuccessfulCodexRunner()
    adapter = _adapter(factory, settings, connection, model, runner)
    binding = AdapterBinding(
        resolved=ResolvedModel(model=model, connection=connection, provider=profile),
        adapter=adapter,
        selected_key=None,
    )
    db_session.info["job_id"] = job.id
    real_rmtree = shutil.rmtree
    cleanup_attempts = {"count": 0}

    def flaky_rmtree(path, *args, **kwargs):
        cleanup_attempts["count"] += 1
        if cleanup_attempts["count"] == 1:
            raise PermissionError(32, "transient lock")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("app.services.cli_executor.shutil.rmtree", flaky_rmtree)

    def invoke(bound_adapter):
        return bound_adapter.edit_region(
            ImageRequest(
                prompt="漫画私密提示",
                reference_images=(_png_bytes(),),
                reference_mime_types=("image/png",),
            )
        )

    response = provider_handler._invoke_provider(db_session, binding, invoke)

    assert runner.calls == 1 and response.images
    assert "cleanup_warning" not in response.usage
    assert cleanup_attempts["count"] == 2
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.cleanup_state, run.lease_slot) == (
        "COMPLETED",
        "CLEANED",
        None,
    )
    assert not (settings.storage_root / "cli_runs" / run.id).exists()
    attempt = db_session.scalar(select(ModelCallAttempt))
    assert attempt.outcome == "SUCCEEDED"
    assert "cleanup_warning" not in (attempt.usage or {})


def test_codex_persistent_cleanup_failure_keeps_success_with_redacted_warning(
    db_session, tmp_path, monkeypatch
):
    factory, settings, profile, connection, model, job = _cli_rows(db_session, tmp_path)
    runner = _SuccessfulCodexRunner()
    adapter = _adapter(factory, settings, connection, model, runner)
    binding = AdapterBinding(
        resolved=ResolvedModel(model=model, connection=connection, provider=profile),
        adapter=adapter,
        selected_key=None,
    )
    db_session.info["job_id"] = job.id

    def locked_rmtree(path, *args, **kwargs):
        raise PermissionError(32, "locked")

    monkeypatch.setattr("app.services.cli_executor.shutil.rmtree", locked_rmtree)

    def invoke(bound_adapter):
        return bound_adapter.edit_region(
            ImageRequest(
                prompt="漫画私密提示",
                reference_images=(_png_bytes(),),
                reference_mime_types=("image/png",),
            )
        )

    response = provider_handler._invoke_provider(db_session, binding, invoke)

    assert runner.calls == 1 and response.images
    assert response.usage["cleanup_warning"] == {"error_type": "PermissionError"}
    attempt = db_session.scalar(select(ModelCallAttempt))
    run = db_session.scalar(select(CLIExecutionRun))
    assert attempt.outcome == "SUCCEEDED"
    assert attempt.usage["cleanup_warning"] == {"error_type": "PermissionError"}
    assert attempt.error_message is None
    assert (run.state, run.cleanup_state) == ("COMPLETED", "FAILED")
    blob = json.dumps(attempt.usage, ensure_ascii=False)
    assert "漫画私密提示" not in blob
    assert "out_001" not in blob
    assert "cli_runs" not in blob
    assert str(settings.storage_root) not in blob


@pytest.mark.parametrize(
    "resolver_error",
    [FileNotFoundError(2, "codex.exe removed"), ValueError("invalid executable name")],
    ids=["file-not-found", "value-error"],
)
def test_codex_unresolvable_executable_finalizes_audit_without_run(
    db_session, tmp_path, resolver_error
):
    factory, settings, profile, connection, model, job = _cli_rows(db_session, tmp_path)
    runner = _FailingCodexRunner()

    def broken_resolver(_value):
        raise resolver_error

    adapter = _adapter(
        factory, settings, connection, model, runner, executable_resolver=broken_resolver
    )
    binding = AdapterBinding(
        resolved=ResolvedModel(model=model, connection=connection, provider=profile),
        adapter=adapter,
        selected_key=None,
    )
    db_session.info["job_id"] = job.id
    callback_calls = 0

    def invoke(bound_adapter):
        nonlocal callback_calls
        callback_calls += 1
        return bound_adapter.generate_asset(ImageRequest(prompt="消失测试"))

    with pytest.raises(ProviderAdapterError) as caught:
        provider_handler._invoke_provider(db_session, binding, invoke)

    assert caught.value.code == "UNAVAILABLE" and not caught.value.retryable
    assert callback_calls == 1 and runner.calls == 0
    attempt = db_session.scalar(select(ModelCallAttempt))
    assert (attempt.outcome, attempt.error_code) == ("FAILED", "UNAVAILABLE")
    assert db_session.scalar(select(CLIExecutionRun)) is None
    assert not (settings.storage_root / "cli_runs").exists()
