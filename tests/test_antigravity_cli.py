"""Offline V02-14B acceptance for the Antigravity CLI image channel."""

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.model_adapters.antigravity_cli import (
    AntigravityCLIImageAdapter,
    AntigravityCLIProbeAdapter,
    AntigravityCLIRuntime,
    _owned_image_candidates,
    resolve_antigravity_executable,
)
from app.model_adapters.base import ImageRequest, ProviderAdapterError
from app.models import (
    AIModel,
    CLIExecutionRun,
    GenerationJob,
    ModelCallAttempt,
    Project,
    ProviderConnection,
    ProviderProfile,
)
from app.services.cli_executor import CLIExecutionController, CLIProcessOutcome
from app.services.model_router import AdapterBinding, ResolvedModel, bind_adapter
from app.services.provider_presets import ensure_provider_presets
from app.services.worker_handlers import provider as provider_handler


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "purple").save(output, format="PNG")
    return output.getvalue()


def _fake_probe_runner(argv: tuple[str, ...]) -> CLIProcessOutcome:
    if argv[-1] == "--version":
        return CLIProcessOutcome(0, stdout=b"1.1.22\n")
    if argv[-1] == "models" and "--help" not in argv:
        return CLIProcessOutcome(0, stdout=b"gemini-3.1-pro\n")
    if argv[-1] == "--help" and "models" not in argv:
        return CLIProcessOutcome(
            0,
            stdout=(
                b"--print --output-format --json-schema --sandbox --add-dir "
                b"--print-timeout --disable-slash-commands"
            ),
        )
    if argv[-2:] == ("models", "--help"):
        return CLIProcessOutcome(0, stdout=b"List available models")
    raise AssertionError(argv)


def _probe_adapter(**overrides) -> AntigravityCLIProbeAdapter:
    return AntigravityCLIProbeAdapter(
        Settings(),
        executable="agy",
        command_runner=overrides.get("command_runner", _fake_probe_runner),
        executable_finder=lambda _value: "C:/tools/agy.exe",
    )


def test_antigravity_probe_parses_version_login_and_headless_surfaces():
    adapter = _probe_adapter()

    assert adapter.presence().status == "PASSED"
    version = adapter.version()
    assert version.status == "PASSED" and version.metrics["version"] == "1.1.22"
    assert adapter.login().status == "PASSED"
    capability = adapter.capability()
    assert capability.status == "PASSED"
    assert capability.metrics["operations"] == ["image_generate", "image_edit"]
    assert capability.metrics["image_tool"] == "generate_image"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("login", "UNAUTHENTICATED"), ("capability", "UNSUPPORTED")],
)
def test_antigravity_probe_fails_closed(failure, expected):
    def runner(argv: tuple[str, ...]) -> CLIProcessOutcome:
        if failure == "login" and argv[-1] == "models" and "--help" not in argv:
            return CLIProcessOutcome(1, stderr=b"Authentication required; sign in")
        if failure == "capability" and argv[-1] == "--help" and "models" not in argv:
            return CLIProcessOutcome(0, stdout=b"--print --output-format")
        return _fake_probe_runner(argv)

    adapter = _probe_adapter(command_runner=runner)
    assert adapter.presence().status == "PASSED"
    assert adapter.version().status == "PASSED"
    login = adapter.login()
    if failure == "login":
        assert login.error_code == expected
    else:
        assert login.status == "PASSED"
        assert adapter.capability().error_code == expected


def test_antigravity_resolver_accepts_only_native_canonical_executable(tmp_path):
    native = tmp_path / "agy.exe"
    native.write_bytes(b"native")
    wrapper = tmp_path / "antigravity.cmd"
    wrapper.write_text("wrapper", encoding="utf-8")

    assert resolve_antigravity_executable(str(native)) == str(native.resolve())
    assert resolve_antigravity_executable(str(wrapper)) is None
    with pytest.raises(ValueError):
        resolve_antigravity_executable("antigravity")


def test_antigravity_preset_seeds_declared_disabled_cli_channel(client):
    providers = client.get("/api/v1/providers").json()
    provider = next(item for item in providers if item["preset_key"] == "antigravity-cli")
    connection = provider["connections"][0]

    assert connection["protocol"] == "CLI_ANTIGRAVITY"
    assert connection["credential_source"] == "CLI_SESSION"
    assert connection["enabled"] is False
    assert connection["nonsecret_config"]["cli_executable"] == "agy"
    models = client.get(
        f"/api/v1/providers/connections/{connection['id']}/models"
    ).json()
    assert [(item["provider_model_id"], item["confidence"]) for item in models] == [
        ("antigravity-imagegen", "DECLARED")
    ]
    assert models[0]["capabilities"]["max_reference_images"] == 1


def test_antigravity_connection_probe_and_executable_update(
    client, db_session, monkeypatch
):
    ensure_provider_presets(db_session, get_settings(), auto_commit=True)
    profile = db_session.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "antigravity-cli")
    )
    connection = db_session.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    monkeypatch.setattr(
        "app.services.connection_verifier.AntigravityCLIProbeAdapter",
        lambda *_args, **_kwargs: _probe_adapter(),
    )

    verified = client.post(
        f"/api/v1/providers/connections/{connection.id}/verify",
        json={"level": "CREDENTIALS"},
    )
    assert verified.status_code == 200
    assert verified.json()["health"]["health_state"] == "AVAILABLE"
    assert verified.json()["probe"]["probe_type"] == "CLI_CAPABILITY"
    db_session.refresh(connection)
    assert connection.enabled is False
    assert connection.nonsecret_config["cli_version"] == "1.1.22"

    rejected = client.patch(
        f"/api/v1/providers/connections/{connection.id}",
        json={
            "version": connection.version,
            "nonsecret_config": {"cli_executable": "tools/agy.exe"},
        },
    )
    assert rejected.status_code == 422
    updated = client.patch(
        f"/api/v1/providers/connections/{connection.id}",
        json={
            "version": connection.version,
            "nonsecret_config": {"cli_executable": "C:\\tools\\agy.exe"},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["health_state"] == "UNKNOWN"


def test_available_antigravity_model_binds_without_api_key(db_session):
    settings = get_settings()
    ensure_provider_presets(db_session, settings, auto_commit=True)
    profile = db_session.scalar(
        select(ProviderProfile).where(ProviderProfile.preset_key == "antigravity-cli")
    )
    connection = db_session.scalar(
        select(ProviderConnection).where(ProviderConnection.provider_id == profile.id)
    )
    model = db_session.scalar(select(AIModel).where(AIModel.connection_id == connection.id))
    connection.enabled = True
    connection.health_state = "AVAILABLE"
    db_session.commit()

    binding = bind_adapter(
        db_session,
        settings,
        operation="image_generate",
        explicit_reference=model.id,
    )

    assert isinstance(binding.adapter, AntigravityCLIImageAdapter)
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
    project = Project(name="Antigravity CLI 离线项目")
    profile = ProviderProfile(
        preset_key="antigravity-cli",
        name="Antigravity CLI",
        enabled=True,
        built_in=True,
    )
    db_session.add_all([project, profile])
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="默认连接",
        protocol="CLI_ANTIGRAVITY",
        base_url="cli://antigravity",
        enabled=True,
        health_state="AVAILABLE",
        nonsecret_config={"cli_executable": "agy"},
    )
    db_session.add(connection)
    db_session.flush()
    model = AIModel(
        connection_id=connection.id,
        provider_model_id="antigravity-imagegen",
        display_name="Antigravity CLI ImageGen",
        model_type="IMAGE",
        input_modalities=["TEXT", "IMAGE"],
        output_modalities=["IMAGE"],
        operations=["image_generate", "image_edit"],
        capabilities={"resolutions": ["1K"], "max_reference_images": 1},
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
    db_session.flush()
    attempt = ModelCallAttempt(
        job_id=job.id,
        project_id=job.project_id,
        job_attempt=1,
        dispatch_no=1,
        provider="antigravity-cli",
        model_id=model.provider_model_id,
        catalog_model_id=model.id,
        connection_id=connection.id,
    )
    db_session.add(attempt)
    db_session.commit()
    return factory, settings, connection, model, job, attempt


class _SuccessfulAntigravityRunner:
    def __init__(self, *, images: int = 1):
        self.calls = 0
        self.images = images
        self.request = None

    def run(self, *, argv, cwd: Path, environment, **_kwargs):
        self.calls += 1
        assert argv[0] == "C:/tools/agy.exe"
        assert "--sandbox" in argv
        assert "--disable-slash-commands" in argv
        assert argv[argv.index("--add-dir") + 1] == "../input"
        assert argv[argv.index("--output-format") + 1] == "json"
        assert "--dangerously-skip-permissions" not in argv
        assert "漫画私密提示" not in argv
        assert "generate_image tool exactly once" in argv[-1]
        isolated_home = (cwd / ".agy-home").absolute()
        assert environment["HOME"] == str(isolated_home)
        assert environment["USERPROFILE"] == str(isolated_home)
        assert environment["TEMP"] == str(cwd)
        assert environment["TMP"] == str(cwd)
        self.request = json.loads((cwd / "../input/request.json").read_text("utf-8"))
        artifact_root = (
            isolated_home
            / ".gemini/antigravity-cli/brain/fake-conversation/artifacts"
        )
        artifact_root.mkdir(parents=True)
        for index in range(self.images):
            (artifact_root / f"mangaflow_output_{index}.png").write_bytes(_png_bytes())
        return CLIProcessOutcome(
            0,
            stdout=json.dumps(
                {
                    "status": "SUCCESS",
                    "usage": {"input_tokens": 7, "unsafe": "ignored"},
                }
            ).encode(),
        )


def _adapter(factory, settings, connection, model, runner):
    return AntigravityCLIImageAdapter(
        AntigravityCLIRuntime(
            settings=settings,
            connection_id=connection.id,
            catalog_model_id=model.id,
            provider_model_id=model.provider_model_id,
            session_factory=factory,
        ),
        controller=CLIExecutionController(settings, factory),
        runner_factory=lambda: runner,
        executable_resolver=lambda _value: "C:/tools/agy.exe",
    )


def test_antigravity_image_edit_adopts_one_private_artifact_through_controller(
    db_session, tmp_path
):
    factory, settings, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )
    runner = _SuccessfulAntigravityRunner()
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

    assert runner.calls == 1 and response.images == (_png_bytes(),)
    assert runner.request["operation"] == "image_edit"
    assert runner.request["prompt"] == "漫画私密提示"
    assert runner.request["reference_images"][0].startswith("input/references/")
    assert response.usage == {
        "input_tokens": 7,
        "estimated_cost": None,
        "cost_source": "CLI_EXTERNAL",
    }
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.cleanup_state, run.lease_slot) == (
        "COMPLETED",
        "CLEANED",
        None,
    )
    assert not (settings.storage_root / "cli_runs" / run.id).exists()


@pytest.mark.parametrize(
    ("outcome", "expected", "retryable"),
    [
        (CLIProcessOutcome(2, stderr=b"Authentication required"), "UNAUTHENTICATED", False),
        (CLIProcessOutcome(2, stderr=b"Permission denied"), "UNSUPPORTED", False),
        (CLIProcessOutcome(2, stderr=b"quota exhausted"), "RATE_LIMIT", True),
        (CLIProcessOutcome(0, stdout=b"not-json"), "INVALID_OUTPUT", False),
        (CLIProcessOutcome(0, stdout=b'{"status":"MAYBE"}'), "INVALID_OUTPUT", False),
    ],
)
def test_antigravity_failure_mapping_is_persisted(
    db_session, tmp_path, outcome, expected, retryable
):
    factory, settings, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )

    class Runner:
        def run(self, **_kwargs):
            return outcome

    adapter = _adapter(factory, settings, connection, model, Runner())
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.generate_asset(ImageRequest(prompt="失败测试"))

    assert caught.value.code == expected
    assert caught.value.retryable is retryable
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.error_code) == ("FAILED", expected)


def test_antigravity_rejects_ambiguous_private_artifacts(db_session, tmp_path):
    factory, settings, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )
    adapter = _adapter(
        factory,
        settings,
        connection,
        model,
        _SuccessfulAntigravityRunner(images=2),
    )
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.generate_asset(ImageRequest(prompt="歧义产物"))

    assert caught.value.code == "PARTIAL_OUTPUT"
    run = db_session.scalar(select(CLIExecutionRun))
    assert (run.state, run.cleanup_state) == ("FAILED", "RETAINED")


def test_antigravity_rejects_more_than_one_reference_before_launch(db_session, tmp_path):
    factory, settings, connection, model, job, attempt = _cli_rows(
        db_session, tmp_path
    )
    runner = _SuccessfulAntigravityRunner()
    adapter = _adapter(factory, settings, connection, model, runner)
    adapter.bind_execution_context(
        job_id=job.id,
        model_call_attempt_id=attempt.id,
        lease_owner=None,
    )

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.edit_region(
            ImageRequest(
                prompt="过多参考图",
                reference_images=(_png_bytes(), _png_bytes()),
                reference_mime_types=("image/png", "image/png"),
            )
        )

    assert caught.value.code == "UNSUPPORTED"
    assert runner.calls == 0
    assert db_session.scalar(select(CLIExecutionRun)) is None


def test_antigravity_private_artifact_walker_rejects_junctions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    brain = home / ".gemini/antigravity-cli/brain"
    trap = brain / "escape"
    trap.mkdir(parents=True)
    original = Path.is_junction
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == trap or original(path),
    )

    with pytest.raises(ProviderAdapterError) as caught:
        _owned_image_candidates(brain, home)

    assert caught.value.code == "INVALID_OUTPUT"


def test_antigravity_failure_finalizes_model_call_audit_without_fallback(
    db_session, tmp_path
):
    factory, settings, connection, model, job, seeded_attempt = _cli_rows(
        db_session, tmp_path
    )
    db_session.delete(seeded_attempt)
    db_session.commit()
    profile = db_session.get(ProviderProfile, connection.provider_id)

    class Runner:
        calls = 0

        def run(self, **_kwargs):
            self.calls += 1
            return CLIProcessOutcome(2, stderr=b"Authentication required")

    runner = Runner()
    binding = AdapterBinding(
        resolved=ResolvedModel(model=model, connection=connection, provider=profile),
        adapter=_adapter(factory, settings, connection, model, runner),
        selected_key=None,
    )
    db_session.info["job_id"] = job.id
    callback_calls = 0

    def invoke(bound_adapter):
        nonlocal callback_calls
        callback_calls += 1
        return bound_adapter.generate_asset(ImageRequest(prompt="审计失败"))

    with pytest.raises(ProviderAdapterError) as caught:
        provider_handler._invoke_provider(db_session, binding, invoke)

    assert caught.value.code == "UNAUTHENTICATED"
    assert callback_calls == runner.calls == 1
    attempt = db_session.scalar(select(ModelCallAttempt))
    assert (attempt.outcome, attempt.error_code) == ("FAILED", "UNAUTHENTICATED")
