"""Desktop sidecar end-to-end (V02-54): real API sidecar + fake model channel.

Drives the exact startup protocol the Tauri shell uses, entirely over HTTP
against the real FastAPI app (Alembic-migrated SQLite, jobs executed by the
in-process local executor — the shipped default without Redis), with the fake
model channel proving the 生成→候选 loop makes zero provider calls.

Run via scripts/run-sidecar-e2e.sh (needs .venv-desktop with apps/api deps).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import httpx
import pytest

TOKEN_RE = re.compile(r"[0-9a-f]{32}")
READY_PREFIX = "MANGAFLOW_READY "
GO_PREFIX = "MANGAFLOW_GO "
REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "apps/desktop/sidecar/mangaflow_desktop_helper.py"
API_ROOT = REPO_ROOT / "apps/api"

SOURCE_PARAGRAPH = (
    "第{index}段，春雨落在京都旧宅的黑瓦上，苏清白握着父亲留下的钥匙推开纸门。"
    "她看见顾川站在昏暗走廊尽头，低声问他为什么没有离开。"
    "顾川没有立刻回答，只把沾着雨水的旧信封放在灯下，两个人都意识到今晚必须说出真相。"
)


def _png(color: tuple[int, int, int]) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (48, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


class DesktopShell:
    """Python-level stand-in for the Rust shell handshake (D3/D4 evidence)."""

    def __init__(
        self, user_data: Path, web_dist: Path | None = None, *, expect_web_origin: bool = True
    ) -> None:
        self.token = os.urandom(16).hex()
        self.user_data = user_data
        self.web_dist = web_dist
        self.expect_web_origin = expect_web_origin
        self.runtime = user_data / "runtime" / f"mangaflow-desktop-{self.token}"
        self.runtime.mkdir(parents=True)
        self.journal = self.runtime / "owner.json"
        self.stderr_log_path = self.runtime / "helper.stderr.log"
        self.stderr_log = self.stderr_log_path.open("wb")
        env = dict(
            os.environ,
            MANGAFLOW_DESKTOP_TOKEN=self.token,
            MANGAFLOW_DESKTOP_JOURNAL=str(self.journal),
            MANGAFLOW_DISABLE_DOTENV="1",
        )
        command = [
            sys.executable,
            str(HELPER),
            "app",
            "--api-root",
            str(API_ROOT),
            "--user-data",
            str(user_data),
            "--fake-channel",
        ]
        if web_dist is not None:
            command += ["--web-dist", str(web_dist)]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_log,
            text=True,
            env=env,
            start_new_session=True,  # mirrors setsid; shell can killpg the tree
        )
        # Windows: the launcher-style venv python re-execs the real
        # interpreter as a CHILD (#508) — process.kill() on the redirector
        # leaves that grandchild holding its API port and the stdout write
        # end. Assign the tree to a KILL_ON_JOB_CLOSE Job so stop() can
        # terminate the whole tree; POSIX needs none of this (killpg covers
        # the session via start_new_session).
        self._job_handle = None
        if os.name == "nt":
            import ctypes

            job = ctypes.windll.kernel32.CreateJobObjectW(None, None)
            if job:
                class _JobLimits(ctypes.Structure):
                    _fields_ = [
                        ("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32),
                    ]

                limits = _JobLimits()
                limits.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if ctypes.windll.kernel32.SetInformationJobObject(
                    job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                ):
                    handle = ctypes.windll.kernel32.OpenProcess(
                        0x1F0FFF, False, self.process.pid
                    )  # PROCESS_ALL_ACCESS
                    if handle:
                        try:
                            ctypes.windll.kernel32.AssignProcessToJobObject(job, handle)
                            self._job_handle = job
                        finally:
                            ctypes.windll.kernel32.CloseHandle(handle)

    def _assert_owned_pid(self, pid: int) -> None:
        """The READY announcer must be a process this shell spawned.

        Direct equality is the norm; on Windows, a launcher-style venv
        python (CPython 3.12) re-execs the real interpreter as a child, so
        the announcer is a grandchild — accept it when its parent is the
        spawned process (the Rust shell accepts it via Job membership).
        """

        assert isinstance(pid, int) and pid > 0, f"announcer pid must be a positive int, got {pid!r}"
        if pid == self.process.pid:
            return
        assert os.name == "nt", f"helper pid {pid} is not the spawned {self.process.pid}"
        query = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").ParentProcessId",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        parent = int(query.stdout.strip())
        assert parent == self.process.pid, (
            f"announcer pid {pid} parent {parent} is not the spawned {self.process.pid}"
        )

    def _read_ready_line(self, timeout: float) -> str:
        """Read one protocol line with a hard deadline.

        ``readline()`` itself blocks forever when a helper hangs before
        publishing readiness — a deadline assertion placed after it can
        never fire, and the suite dies on the harness timeout instead of
        the intended assertion. A daemon reader thread keeps the read
        portable (select() does not work on Windows pipes).
        """

        import queue

        lines: queue.Queue[str] = queue.Queue()

        def _reader() -> None:
            assert self.process.stdout is not None
            lines.put(self.process.stdout.readline())

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        try:
            return lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(
                f"helper did not publish readiness within {timeout}s"
            ) from None

    def handshake(self, timeout: float = 15.0) -> dict:
        line = self._read_ready_line(timeout)
        assert line.startswith(READY_PREFIX), f"unexpected helper output: {line!r}"
        payload = json.loads(line.removeprefix(READY_PREFIX))
        assert payload["token"] == self.token
        self._assert_owned_pid(payload["pid"])
        origin = payload["api_origin"]
        assert origin.startswith("http://127.0.0.1:"), origin
        port = int(origin.rsplit(":", 1)[1])
        record = json.loads(self.journal.read_text(encoding="utf-8"))
        assert record["state"] == "ready"
        # The journal is written by the announcer itself, so its pid is the
        # helper's real pid (a grandchild under a launcher-style venv python).
        assert record["pid"] == payload["pid"]
        assert record["api_origin"] == origin
        # Plan B (W-15): with --web-dist the helper manages a Next standalone
        # server and announces its loopback origin in READY and the journal —
        # unless the server failed to boot, in which case the helper must
        # fail closed (no web_origin anywhere) instead of announcing a port
        # it does not own.
        if self.web_dist is not None and self.expect_web_origin:
            web_origin = payload["web_origin"]
            assert web_origin.startswith("http://127.0.0.1:"), web_origin
            assert record["web_origin"] == web_origin
            self.web_origin = web_origin
        else:
            assert "web_origin" not in payload
            assert "web_origin" not in record
        # The pre-bound socket must answer nothing before GO (no traffic
        # before the shell verified ownership).
        probe = socket.create_connection(("127.0.0.1", port), timeout=2)
        probe.settimeout(0.5)
        try:
            first_bytes = probe.recv(16)
            raise AssertionError(f"helper served traffic before GO: {first_bytes!r}")
        except socket.timeout:
            pass  # no bytes served before GO — correct
        finally:
            probe.close()
        assert self.process.stdin is not None
        self.process.stdin.write(f"{GO_PREFIX}{self.token}\n")
        self.process.stdin.flush()
        self.origin = origin
        return record

    def wait_health(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.origin}/api/v1/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except Exception as error:  # noqa: BLE001 - poll until ready
                last_error = error
            time.sleep(0.2)
        raise AssertionError(f"health never became ready: {last_error}")

    def _close_job(self) -> None:
        """Release the KILL_ON_JOB_CLOSE Job (#508): closing the handle
        triggers the kill-on-close for anything still assigned (a last-
        resort backstop after the explicit terminate), and dropping the
        reference lets the handle be finalized."""
        if getattr(self, "_job_handle", None) is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._job_handle)
            self._job_handle = None

    def stop(self) -> int:
        if os.name == "nt":
            # The production Windows stop channel: closing the helper's stdin
            # makes its EOF watcher self-terminate (uvicorn graceful exit);
            # escalate to a hard kill of the direct child if it refuses.
            try:
                if self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.close()
                code = self.process.wait(timeout=20)
                self._close_job()
                self.stderr_log.close()
                return code
            except subprocess.TimeoutExpired:
                # Kill the whole tree via the Job (#508): a launcher-style
                # venv python means the real interpreter is a GRANDCHILD the
                # direct kill() never reached — it kept the API port bound
                # and the reader thread blocked. TerminateJobObject reaches
                # every member; the direct child then reaps normally.
                if self._job_handle is not None:
                    import ctypes

                    ctypes.windll.kernel32.TerminateJobObject(self._job_handle, 1)
                self.process.kill()
                code = self.process.wait(timeout=5)
                self._close_job()
                self.stderr_log.close()
                return code
        # SIGTERM reaches the whole session (uvicorn installs graceful
        # shutdown handlers); escalate to SIGKILL if it refuses.
        # ProcessLookupError means the helper died mid-test (the exact
        # scenario the fixture failure-surface additions exist to report):
        # reap the exit code and fall through so stop() still returns it
        # instead of masking the body failure the test actually hit.
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            code = self.process.wait(timeout=5)
            self.stderr_log.close()
            return code
        try:
            code = self.process.wait(timeout=15)
            self.stderr_log.close()
            return code
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = self.process.wait(timeout=5)
            self.stderr_log.close()
            return code


@pytest.fixture()
def desktop(tmp_path: Path):
    user_data = tmp_path / "user-data"
    (user_data / "data").mkdir(parents=True)
    shell = DesktopShell(user_data)
    body_error: BaseException | None = None
    try:
        record = shell.handshake()
        shell.wait_health()
        yield shell, user_data, record
    except BaseException as error:
        # An assert inside finally would REPLACE the in-flight setup error:
        # a helper that died during setup (handshake/health) must be
        # reported in addition to, not instead of, the failure the setup
        # actually hit. (Mid-TEST failures propagate through the yield and
        # never reach this except — pytest runs the teardown separately.)
        body_error = error
        raise
    finally:
        exit_code = shell.stop()
        if body_error is None:
            assert exit_code == 0, f"helper exited with {exit_code}"
        elif exit_code != 0:
            print(f"note: helper also exited with {exit_code} during the failing body")


class _ScriptedShell:
    """Process-free DesktopShell double for fixture-contract tests (#349).

    The e2e fixtures must surface the BODY's failure as-is and report the
    helper's stop-side exit code alongside it — never instead of it. These
    doubles script handshake/stop outcomes so the property can be pinned
    without booting a real helper.
    """

    def __init__(self, user_data: Path, *, stop_code: int, handshake_error=None) -> None:
        self.user_data = user_data
        self._stop_code = stop_code
        self._handshake_error = handshake_error
        self.stop_calls = 0

    def handshake(self) -> dict:
        if self._handshake_error is not None:
            raise self._handshake_error
        return {"state": "ready", "token": "stub"}

    def wait_health(self) -> None:
        return None

    def stop(self) -> int:
        self.stop_calls += 1
        return self._stop_code


def _desktop_fixture_function():
    """The raw generator behind the ``desktop`` fixture.

    pytest keeps the undecorated function reachable (directly, or via
    ``__wrapped__`` when the marker wraps it), which lets the tests below
    drive the fixture's control flow — setup, body failure, teardown —
    exactly the way pytest does.
    """

    return getattr(desktop, "__wrapped__", desktop)


def test_fixture_preserves_body_failure_over_stop_exit(monkeypatch, tmp_path: Path):
    """#349 red-team pin: an assert inside ``finally`` REPLACES the in-flight
    body exception — a helper that dies mid-test was reported as
    "helper exited with N" with the real failure point destroyed. The
    fixture must keep the original error and note the exit code instead.
    """

    scripted = _ScriptedShell(tmp_path, stop_code=1)
    monkeypatch.setattr(
        sys.modules[__name__], "DesktopShell", lambda user_data: scripted
    )
    generator = _desktop_fixture_function()(tmp_path)
    next(generator)  # advance through setup to the yield (the test body)
    # A failure thrown into the body must surface as ITSELF.
    with pytest.raises(AssertionError, match="BODY_STAGE_MARKER") as caught:
        generator.throw(AssertionError("BODY_STAGE_MARKER"))
    assert "helper exited" not in str(caught.value)
    assert scripted.stop_calls == 1, "fixture must still stop the shell"


def test_fixture_preserves_setup_failure_over_stop_exit(monkeypatch, tmp_path: Path):
    """The handshake failing (helper died pre-READY) is the fixture's own
    try body: its error must also survive the stop-side exit-code check."""

    monkeypatch.setattr(
        sys.modules[__name__],
        "DesktopShell",
        lambda user_data: _ScriptedShell(
            user_data, stop_code=1, handshake_error=AssertionError("HANDSHAKE_STAGE_MARKER")
        ),
    )
    with pytest.raises(AssertionError, match="HANDSHAKE_STAGE_MARKER"):
        next(_desktop_fixture_function()(tmp_path))


def test_fixture_flags_nonzero_stop_exit_after_successful_body(monkeypatch, tmp_path: Path):
    """The other half of the contract: when the body SUCCEEDS, the teardown
    path (pytest resumes the generator after the yield) must still assert
    the helper exited 0 — the unmask fix must not silently retire the
    exit-code check."""

    monkeypatch.setattr(
        sys.modules[__name__],
        "DesktopShell",
        lambda user_data: _ScriptedShell(user_data, stop_code=9),
    )
    generator = _desktop_fixture_function()(tmp_path)
    shell, user_data, record = next(generator)  # setup + yield
    assert shell.stop_calls == 0
    with pytest.raises(AssertionError, match="helper exited with 9"):
        next(generator)  # teardown resumption, exactly like pytest does


def test_fixture_accepts_clean_exit_after_successful_body(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        sys.modules[__name__],
        "DesktopShell",
        lambda user_data: _ScriptedShell(user_data, stop_code=0),
    )
    generator = _desktop_fixture_function()(tmp_path)
    next(generator)  # setup + yield
    with pytest.raises(StopIteration):
        next(generator)  # teardown completes cleanly


def _client(shell: DesktopShell) -> httpx.Client:
    return httpx.Client(base_url=f"{shell.origin}/api/v1", timeout=30.0)


def _wait_job(client: httpx.Client, job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            assert job["status"] == "COMPLETED", job
            return job
        time.sleep(0.3)
    raise AssertionError(f"job {job_id} did not finish in time")


def _upload(client: httpx.Client, project_id: str, kind: str, name: str, data: bytes) -> dict:
    response = client.post(
        "/assets/upload",
        data={"project_id": project_id, "kind": kind},
        files={"file": (name, data, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_sidecar_boot_and_fake_generate_candidate_loop(desktop):
    shell, user_data, record = desktop
    client = _client(shell)

    project = client.post(
        "/projects",
        json={"name": "V02-54 桌面壳", "workflow_mode": "AUTO", "default_concurrency": 2},
    )
    assert project.status_code == 201, project.text
    project = project.json()
    assert project["id"]

    # User-data discipline: DB/storage/uploads live under the shell's user
    # directory, never the install directory (ADR §4.1).
    assert (user_data / "data" / "mangaflow.db").exists()
    assert (user_data / "storage").is_dir()
    assert not (REPO_ROOT / "storage" / "mangaflow-desktop").exists()

    source_text = "\n\n".join(
        SOURCE_PARAGRAPH.format(index=index) for index in range(1, 17)
    )
    imported = client.post(
        f"/projects/{project['id']}/sources/import",
        json={"title": "雨夜旧信", "text": source_text},
    )
    assert imported.status_code == 201, imported.text
    chapter = imported.json()["chapters"][0]

    parse_job = client.post(f"/chapters/{chapter['id']}/parse")
    assert parse_job.status_code == 202, parse_job.text
    _wait_job(client, parse_job.json()["id"])

    script = client.get(f"/chapters/{chapter['id']}/script").json()
    assert script["status"] == "READY"
    assert script["coverage"]["ratio"] == 1

    characters = client.get(f"/projects/{project['id']}/characters").json()
    hero = next(item for item in characters if item["primary_name"] == "苏清白")
    locked = client.patch(
        f"/characters/{hero['id']}",
        json={
            "version": hero["version"],
            "primary_name": "苏清白",
            "aliases": ["小白"],
            "locked_features": ["黑色长发", "右眼下泪痣"],
            "forbidden_changes": ["不得改变发色", "不得改变泪痣位置"],
        },
    )
    assert locked.status_code == 200, locked.text

    character_asset = _upload(
        client, project["id"], "CHARACTER_REFERENCE", "character.png", _png((250, 250, 250))
    )
    bound = client.post(
        f"/characters/{hero['id']}/references",
        json={"asset_id": character_asset["id"], "angle": "front", "is_canonical": True},
    )
    assert bound.status_code == 201, bound.text
    outfit_asset = _upload(
        client, project["id"], "OUTFIT_REFERENCE", "uniform.png", _png((180, 180, 180))
    )
    outfit = client.post(
        f"/projects/{project['id']}/outfits",
        json={
            "character_id": hero["id"],
            "name": "深色冬季校服",
            "reference_asset_ids": [outfit_asset["id"]],
        },
    )
    assert outfit.status_code == 201, outfit.text
    style_asset = _upload(
        client, project["id"], "STYLE_REFERENCE", "style.png", _png((80, 80, 80))
    )
    style = client.post(
        f"/projects/{project['id']}/styles",
        json={
            "name": "B1 雨夜彩色漫画",
            "color_mode": "color",
            "locked_fields": ["线稿", "低饱和色板"],
            "reference_asset_ids": [style_asset["id"]],
        },
    )
    assert style.status_code == 201, style.text
    style_id = style.json()["id"]

    analyze_job = client.post(f"/styles/{style_id}/analyze")
    assert analyze_job.status_code == 202, analyze_job.text
    _wait_job(client, analyze_job.json()["id"])
    analyzed = next(
        item
        for item in client.get(f"/projects/{project['id']}/styles").json()
        if item["id"] == style_id
    )
    assert analyzed["profile"]["prompt_summary"].startswith("彩色日式漫画")

    palette = client.post(
        f"/styles/{style_id}/palette-approve",
        json={"version": analyzed["version"], "palette": analyzed["profile"]["palette_draft"]},
    )
    assert palette.status_code == 200, palette.text

    assets_before = {
        item["id"] for item in client.get("/assets", params={"project_id": project["id"]}).json()
    }
    sheet = client.post(
        f"/characters/{hero['id']}/complete-sheet",
        json={"model_alias": "image.nano_banana_2", "resolution": "1K"},
    )
    assert sheet.status_code == 202, sheet.text
    queued = sheet.json()
    assert queued["candidate"]["variant"] == "SHEET"
    job = _wait_job(client, queued["job_id"])

    attempts = client.get(f"/jobs/{queued['job_id']}/model-call-attempts").json()
    assert attempts, "the fake channel must leave a model call attempt"
    assert all(
        item.get("provider_model_id") or item.get("model_id") for item in attempts
    ), attempts

    # Candidate READY proven over HTTP: approving a reference is rejected with
    # 409 until the candidate is READY with a persisted asset.
    approved = client.post(
        f"/asset-candidates/{queued['candidate']['id']}/approve-reference",
        json={
            "character_id": hero["id"],
            "bind_character_reference": True,
            "set_canonical": True,
        },
    )
    assert approved.status_code == 200, approved.text

    assets_after = client.get("/assets", params={"project_id": project["id"]}).json()
    new_assets = [item for item in assets_after if item["id"] not in assets_before]
    assert new_assets, "generation must persist a new asset"
    asset_id = new_assets[0]["id"]
    with urllib.request.urlopen(f"{shell.origin}/api/v1/assets/{asset_id}/content", timeout=10) as content:
        image_bytes = content.read()
    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(image_bytes))
    image.verify()
    assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert job["progress"] == 100

    # D9: the journal carries identity only.
    final_journal = json.loads(shell.journal.read_text(encoding="utf-8"))
    assert set(final_journal).issubset(
        {"version", "token", "role", "state", "pid", "pid_starttime", "port",
         "api_origin", "started_at", "grandchild_pid"}
    ), final_journal


# The stable lock file every dist/ writer takes EXCLUSIVELY (#350) —
# build-web-standalone.py's DIST_LOCK_PATH and build-frontend-static.sh's
# DIST_LOCK resolve to this same path.
DIST_LOCK_PATH = REPO_ROOT / "apps/desktop/dist/.build.lock"


@contextlib.contextmanager
def _dist_read_lock(timeout: float = 60.0):
    """Shared reader side of the dist/ build lock (#372 item 2).

    The writers hold the exclusive lock around every destructive swap of
    the dist/ trees (build-web-standalone.py's fcntl.flock LOCK_EX branch,
    dist-build-lock.sh's ``flock -w`` form). ``fcntl.flock(fd, LOCK_SH)``
    here is the same flock(2) mechanism as ``flock -s``, so it excludes a
    writer's mid-rmtree/move window from the verification sampling the
    caller performs — #350 locked the writers; this closes the e2e's read
    window against them.

    flock-capable hosts only (POSIX/CI — the same platform split the
    writers use). Windows ships neither flock nor fcntl; the writers'
    O_EXCL lock-file fallback there is a DIFFERENT mechanism (the lock
    file itself is the mutex) with no shared mode, and a shared read
    cannot be pieced together from the two — so this reader takes no lock
    on Windows and the verification window stays unlocked there
    (residual risk recorded in #372).

    Deadlock surface (evaluated before adding): this is the only lock the
    e2e reader holds and every writer holds only the exclusive one, so no
    lock-order cycle exists; run-sidecar-e2e.sh rebuilds the bundle in a
    subprocess that releases its lock before pytest starts, so a single
    process never holds both sides of the same lock file.
    """

    try:
        import errno
        import fcntl
    except ImportError:
        yield  # Windows: no shared-lock primitive exists; see the docstring.
        return

    DIST_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(DIST_LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"dist read lock: timed out after {timeout}s waiting "
                        f"for {DIST_LOCK_PATH} (a writer holds the exclusive "
                        "lock)"
                    ) from error
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _web_dist_dir() -> Path:
    """The Next standalone bundle (plan B, W-15): produced and relocated to
    apps/desktop/dist/web-standalone by scripts/build-web-standalone.py
    (build + relay-manifest verify + static copy; the relocation keeps it
    out of reach of plain `next build`, which regenerates `.next` with the
    :8000 destination and no static copy)."""

    dist = REPO_ROOT / "apps/desktop/dist/web-standalone"
    # Reader side of the #350 lock: the whole verification window (bundle
    # shape, relay manifest, build provenance, tree-hash compare) samples
    # the very tree a writer's rmtree+move replaces, so it runs under the
    # shared lock (#372 item 2).
    with _dist_read_lock():
        assert (dist / "server.js").is_file(), (
            f"{dist} missing server.js — run scripts/build-web-standalone.py first"
        )
        assert (dist / ".next" / "static").is_dir(), (
            f"{dist} missing .next/static — run scripts/build-web-standalone.py first"
        )
        # A stale bundle built for the wrong target would make the loop pass
        # vacuously through some other listener; the relay destination is the
        # contract under test.
        manifest = dist / ".next" / "routes-manifest.json"
        assert "127.0.0.1:39443" in manifest.read_text(encoding="utf-8"), (
            f"{manifest} does not target the helper relay — rebuild with "
            "scripts/build-web-standalone.py"
        )
        # Build provenance: the bundle must come from THIS source tree. dist/ is
        # gitignored and survives for days, so a bundle built from an older
        # apps/web would silently test outdated UI code. Both the source commit
        # and the apps/web tree hash are stamped by the build script.
        build_info_path = dist / "build-info.json"
        assert build_info_path.is_file(), (
            f"{build_info_path} missing — rebuild with scripts/build-web-standalone.py"
        )
        build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
        def _git(*args: str) -> str:
            return subprocess.run(
                ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
            ).stdout.strip()
        # Branch-independent: the apps/web TREE hash is what the UI was built
        # from, identical across branches that share the same web source. The
        # source_commit stays in the stamp as provenance metadata only.
        expected_tree = _git("rev-parse", "HEAD:apps/web")
        assert build_info.get("apps_web_tree") == expected_tree, (
            f"stale web bundle: built from apps/web tree "
            f"{build_info.get('apps_web_tree')!r} but the tree is at "
            f"{expected_tree!r} — rerun scripts/build-web-standalone.py"
        )
    return dist


def _non_loopback_ipv4() -> str | None:
    """A routable local IPv4 address, or None when the host has none (the
    loopback-bind assertion then self-skips: there is no second adapter to
    probe). The UDP connect performs a route lookup without sending packets."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return None if address.startswith("127.") else address


def test_sidecar_plan_b_web_server_loop(tmp_path: Path):
    """Plan B (W-15): with --web-dist the helper spawns the Next standalone
    server as a child; READY carries the loopback web origin; the web server
    serves the UI and proxies /api/v1/* to the helper-owned API through its
    compiled rewrites; the cooperative stop reaps the web server with the
    helper."""
    if shutil.which("node") is None and not (
        (HELPER.parent / "node" / ("node.exe" if os.name == "nt" else "node"))
    ).exists():
        pytest.skip("no node runtime available for the standalone server")
    shell = DesktopShell(tmp_path / "user-data", web_dist=_web_dist_dir())
    (shell.user_data / "data").mkdir(parents=True, exist_ok=True)
    body_error: BaseException | None = None
    try:
        record = shell.handshake()
        shell.wait_health()
        web = shell.web_origin

        # The web server proxies the API through its compiled rewrites and
        # renders the UI (status 200 + HTML shell).
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{web}/api/v1/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:  # noqa: BLE001 - node may still be booting
                time.sleep(0.2)
        else:
            raise AssertionError("web server never proxied /api/v1/health")
        with urllib.request.urlopen(f"{web}/", timeout=10) as response:
            assert response.status == 200
            body = response.read(4096)
        assert b"<!DOCTYPE html>" in body or b"<html" in body.lower()

        # Data flows through the same web origin end to end (seed project
        # listed via the proxy = rewrites carry the helper's dynamic port).
        with urllib.request.urlopen(f"{web}/api/v1/projects", timeout=10) as response:
            assert response.status == 200
        # D9: the web server binds the loopback adapter only. While it is
        # live, the same port must refuse a non-loopback local address —
        # the helper passes HOSTNAME=127.0.0.1 to the Next standalone
        # server (whose own default is 0.0.0.0), and a regression to an
        # all-interfaces bind would silently expose the UI and its API
        # proxy to the LAN.
        external_ip = _non_loopback_ipv4()
        if external_ip is not None:
            external = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            external.settimeout(2.0)
            try:
                assert external.connect_ex((external_ip, int(web.rsplit(":", 1)[1]))) != 0, (
                    f"web server answered on the non-loopback address {external_ip}"
                )
            finally:
                external.close()
        assert record["web_origin"] == web
        # Direct, named contract check: the fixed relay port
        # (WEB_RELAY_PORT, 127.0.0.1:39443) is listening while the plan-B
        # web server serves. (The proxied requests above already exercise it
        # implicitly; this snapshot pins the socket contract itself.)
        relay_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        relay_probe.settimeout(1.0)
        try:
            assert relay_probe.connect_ex(("127.0.0.1", 39443)) == 0, (
                "the relay on the fixed port 39443 must listen while the "
                "plan-B web server is serving"
            )
        finally:
            relay_probe.close()
        # Journal identity-only: web fields are identity too, no commands/env.
        assert set(record).issubset(
            {"version", "token", "role", "state", "pid", "pid_starttime", "port",
             "api_origin", "web_origin", "web_port", "started_at", "grandchild_pid"}
        ), record
    except BaseException as error:
        # Report the body failure, not the stop-side exit code (same
        # masking hazard as the fixture above).
        body_error = error
        raise
    finally:
        exit_code = shell.stop()
        if body_error is None:
            assert exit_code == 0, f"helper exited with {exit_code}"
        elif exit_code != 0:
            print(f"note: helper also exited with {exit_code} during the failing body")
    # The web server must not survive the helper's cooperative exit: the
    # port it claimed must be closed now.
    web_port = int(shell.web_origin.rsplit(":", 1)[1])
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        assert probe.connect_ex(("127.0.0.1", web_port)) != 0, (
            "web server still listening after the helper stopped"
        )
    finally:
        probe.close()
    # E2e stability contract: a COOPERATIVE stop must never produce a
    # spurious "exited mid-session" alarm. The exit watcher's shutdown
    # event suppresses it. Precision: this assertion deterministically
    # catches milestones emitted on the stop path itself, and catches
    # disarming regressions whenever the watcher wins the exit race (a
    # disarmed watcher can still lose that race at the shipped 250ms
    # cadence, because the helper unwinds within tens of ms of node's
    # death) - a regression there would stamp a false lifecycle milestone
    # into healthy sessions' unified logs and poison forensics.
    stderr_text = shell.stderr_log_path.read_text(encoding="utf-8", errors="replace")
    assert "exited mid-session" not in stderr_text, (
        "healthy cooperative stop logged a spurious mid-session web exit"
    )


def test_web_exit_watch_settle_race_stays_silent(monkeypatch):
    """The R2 check-then-act half the unit pins above cannot reach: node's
    poll returns a code, the watcher enters the settle wait, and the
    shutdown event lands DURING that wait — the death is a deliberate-stop
    side effect, so the watcher must stay silent and must NOT close the
    announced socket (the helper's close path owns it). The event is set
    by a timer ~50ms into the settle wait."""

    import importlib.util
    import io
    import threading

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_settle", str(HELPER)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    class DyingAfterStart:
        def poll(self):
            return 0  # reaped before the watcher's first loop check

    class FakeSock:
        def __init__(self):
            self.closed = False
            self.shutdown_called = False

        def close(self):
            self.closed = True

        def shutdown(self, how):
            self.shutdown_called = True

    captured = io.StringIO()
    monkeypatch.setattr(helper, "_log", lambda message: captured.write(message + "\n"))
    sock = FakeSock()

    # The event lands while the watcher is inside shutdown.wait(interval):
    # start with the event UNSET, then set it shortly after (the watcher's
    # first poll observes the code and enters the settle wait).
    shutdown = threading.Event()
    threading.Timer(0.05, shutdown.set).start()

    thread = helper._start_web_exit_watch(DyingAfterStart(), shutdown, sock)
    thread.join(timeout=5)
    assert not thread.is_alive(), "the settle path must end the watcher"
    assert captured.getvalue() == "", (
        "a death explained by the shutdown event must not log a crash"
    )
    assert not sock.closed, (
        "the settle path must leave the announced socket to the close path"
    )


def test_sidecar_dead_web_dist_fails_closed_without_web_origin(tmp_path: Path):
    """Red team 2026-09-08: a web server that dies during boot must NOT leave
    web_origin in READY or the journal.

    Publishing a web origin for a port the session does not own hands the
    WebView — and, through the injected API origin, the unauthenticated
    loopback API — to whichever local process claims the free port instead.
    The helper must verify node is alive and accepting before announcing,
    and downgrade to the static-export form (no web_origin anywhere) when
    it cannot.
    """
    if shutil.which("node") is None:
        pytest.skip("no node runtime available for the standalone server")
    broken_dist = tmp_path / "broken-web-dist"
    broken_dist.mkdir()
    (broken_dist / "server.js").write_text("process.exit(1);\n", encoding="utf-8")

    shell = DesktopShell(
        tmp_path / "user-data", web_dist=broken_dist, expect_web_origin=False
    )
    (shell.user_data / "data").mkdir(parents=True, exist_ok=True)
    body_error: BaseException | None = None
    try:
        record = shell.handshake()
        shell.wait_health()
        assert "web_origin" not in record, record
        # The degraded session is still a fully working API session.
        with urllib.request.urlopen(f"{shell.origin}/api/v1/projects", timeout=10) as response:
            assert response.status == 200
        # A degraded session must not squat on the fixed relay port: with no
        # web server to feed, the helper has no business holding 39443 until
        # process exit (the next session would then see the port as taken).
        # Regression 2026-09-08 (N2 audit §2): _await_web_server_boot reaped the
        # dead node but left the relay bound.
        deadline = time.monotonic() + 5.0
        while True:
            relay_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            relay_probe.settimeout(1.0)
            try:
                if relay_probe.connect_ex(("127.0.0.1", 39443)) != 0:
                    break  # released
            finally:
                relay_probe.close()
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "39443 still answering: the degraded helper holds the "
                    "fixed relay port - or another process bound it"
                )
            time.sleep(0.1)
    except BaseException as error:
        # Same masking hazard as the fixture: report the body failure
        # instead of replacing it with the stop-side exit assert.
        body_error = error
        raise
    finally:
        exit_code = shell.stop()
        if body_error is None:
            assert exit_code == 0, f"helper exited with {exit_code}"
        elif exit_code != 0:
            print(f"note: helper also exited with {exit_code} during the failing body")


def test_sidecar_mid_session_node_exit_is_detected_and_logged(tmp_path: Path):
    """ADR §4.5 scope note (0.2.x = detection, no auto-restart): a web server
    that dies AFTER being announced must leave a forensic milestone in the
    unified logs, and the API session must be unaffected.

    Boot verification (#271) only proves node ownership at READY time; the
    mid-session exit previously vanished into node's own crash output (if
    any), leaving the WebView on a dead origin with no lifecycle evidence.
    """
    if shutil.which("node") is None:
        pytest.skip("no node runtime available for the standalone server")
    dying_dist = tmp_path / "dying-web-dist"
    dying_dist.mkdir()
    # Serve normally (so boot verification passes and web_origin is
    # announced), then die mid-session.
    (dying_dist / "server.js").write_text(
        "const http = require('http');\n"
        "const server = http.createServer((req, res) => res.end('ok'));\n"
        "server.listen(Number(process.env.PORT), '127.0.0.1', () => {\n"
        "  setTimeout(() => process.exit(3), 2500);\n"
        "});\n",
        encoding="utf-8",
    )

    shell = DesktopShell(tmp_path / "user-data", web_dist=dying_dist)
    (shell.user_data / "data").mkdir(parents=True, exist_ok=True)
    stderr_log = shell.stderr_log_path
    body_error: BaseException | None = None
    try:
        record = shell.handshake()
        shell.wait_health()
        assert "web_origin" in record, record
        # Mid-session death: the watcher must record it within ~1s of the
        # 700ms delayed exit, plus poll cadence and log flush.
        deadline = time.monotonic() + 8.0
        detected = False
        while time.monotonic() < deadline:
            if stderr_log.exists() and "exited mid-session" in stderr_log.read_text(
                encoding="utf-8", errors="replace"
            ):
                detected = True
                break
            time.sleep(0.1)
        assert detected, "mid-session web server exit was never logged"
        # Detection is log-only (no restart): the API session is untouched.
        with urllib.request.urlopen(f"{shell.origin}/api/v1/projects", timeout=10) as response:
            assert response.status == 200
        # The announced origin degrades to honest refusal (#507): the
        # exit watcher closes the helper-owned listening socket once node's
        # death is detected, so new WebView connections are REFUSED — they
        # must never be forwarded to whatever local process claims node's
        # freed ephemeral port. The API session keeps serving.
        web_port = int(shell.web_origin.rsplit(":", 1)[1])
        deadline = time.monotonic() + 5.0
        released = False
        while time.monotonic() < deadline:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.settimeout(1.0)
            try:
                if probe.connect_ex(("127.0.0.1", web_port)) != 0:
                    released = True
                    break
            finally:
                probe.close()
            time.sleep(0.1)
        assert released, (
            f"announced port {web_port} still answering after the mid-session "
            "death — the relay must not forward to the freed upstream (#507)"
        )
    except BaseException as error:
        # Same masking hazard as the fixture: report the body failure
        # instead of replacing it with the stop-side exit assert.
        body_error = error
        raise
    finally:
        exit_code = shell.stop()
        if body_error is None:
            assert exit_code == 0, f"helper exited with {exit_code}"
        elif exit_code != 0:
            print(f"note: helper also exited with {exit_code} during the failing body")


def test_node_child_env_strips_ownership_secrets_and_hooks(monkeypatch):
    """Red team 2026-09-09 (#312): the node child inherits the parent env
    minus the handshake secrets and node auto-load hooks — the long-lived
    process most exposed to web content must not carry the ownership token,
    the journal path, or injectable NODE_OPTIONS/NODE_PATH."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_env", str(HELPER)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    monkeypatch.setenv("MANGAFLOW_DESKTOP_TOKEN", "a" * 32)
    monkeypatch.setenv("MANGAFLOW_DESKTOP_JOURNAL", "/tmp/owner.json")
    monkeypatch.setenv("NODE_OPTIONS", "--require /evil")
    monkeypatch.setenv("NODE_PATH", "/evil")
    monkeypatch.setenv("MANGAFLOW_STATIC_EXPORT", "1")
    monkeypatch.setenv("PATH", "/usr/bin")
    for name in (
        "MANGAFLOW_DESKTOP_HELPER",
        "MANGAFLOW_DESKTOP_API_ROOT",
        "MANGAFLOW_DESKTOP_USER_DATA",
        "MANGAFLOW_DESKTOP_FAKE_CHANNEL",
        "MANGAFLOW_DESKTOP_WEB_DIST",
        "MANGAFLOW_DESKTOP_PYTHON",
    ):
        monkeypatch.setenv(name, f"/leaky/{name}")

    env = helper._node_child_env()

    assert "MANGAFLOW_DESKTOP_TOKEN" not in env
    assert "MANGAFLOW_DESKTOP_JOURNAL" not in env
    assert "NODE_OPTIONS" not in env
    assert "NODE_PATH" not in env
    # #447: a user-shell MANGAFLOW_STATIC_EXPORT (leftover from a static
    # export build session) must not ride in — the served app reads it at
    # runtime and would silently render in static-export form.
    assert "MANGAFLOW_STATIC_EXPORT" not in env
    for name in (
        "MANGAFLOW_DESKTOP_HELPER",
        "MANGAFLOW_DESKTOP_API_ROOT",
        "MANGAFLOW_DESKTOP_USER_DATA",
        "MANGAFLOW_DESKTOP_FAKE_CHANNEL",
        "MANGAFLOW_DESKTOP_WEB_DIST",
        "MANGAFLOW_DESKTOP_PYTHON",
    ):
        assert name not in env, f"{name} must not ride into the web-facing child"
    assert env["PATH"] == "/usr/bin", "unrelated parent env must ride along"


def test_web_exit_watch_logs_a_mid_session_crash_and_stays_silent_on_stop(monkeypatch):
    """Unit pins for the detection half the e2e covers only from the log
    side: (1) a node process that dies WITHOUT the shutdown event set must
    produce exactly one "exited mid-session" line naming the exit code;
    (2) the same death WITH the event set (a deliberate stop that reaped
    the child first) must stay silent — the R2 check-then-act regression;
    (3) the watcher must return after the log, not loop. Runs against the
    real _start_web_exit_watch with fake Popen doubles; no node, no ports."""

    import importlib.util
    import io
    import threading

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_watch", str(HELPER)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    class FakeNode:
        def __init__(self, code):
            self._code = code

        def poll(self):
            return self._code

    class FakeSock:
        """Announced-socket double: records closes and shutdowns (#507's
        contract: the watcher must call shutdown(SHUT_RDWR) before close())."""

        def __init__(self):
            self.closed = False
            self.shutdown_called = False

        def close(self):
            self.closed = True

        def shutdown(self, how):
            self.shutdown_called = True

    captured = io.StringIO()
    monkeypatch.setattr(helper, "_log", lambda message: captured.write(message + "\n"))
    sock = FakeSock()

    # (1) Crash without shutdown: one line naming the code, the announced
    # socket is closed (a dead origin must refuse, not relay to a squatter),
    # and the thread returns.
    shutdown = threading.Event()
    thread = helper._start_web_exit_watch(FakeNode(3), shutdown, sock)
    thread.join(timeout=5)
    assert not thread.is_alive(), "the watcher must return after logging"
    assert sock.closed, "the announced socket must close on mid-session death"
    lines = [line for line in captured.getvalue().splitlines() if line]
    assert len(lines) == 1, lines
    assert "exited mid-session" in lines[0] and "code 3" in lines[0]

    # (2) Deliberate stop reaped the child first: the event settles the
    # check-then-act race — no log, watcher returns promptly.
    captured.truncate(0)
    captured.seek(0)
    shutdown = threading.Event()
    shutdown.set()
    live_sock = FakeSock()
    thread = helper._start_web_exit_watch(FakeNode(0), shutdown, live_sock)
    thread.join(timeout=5)
    assert not live_sock.closed, (
        "a stop-side reap must NOT close the announced socket (the helper's "
        "own close path owns it)"
    )
    assert not thread.is_alive()
    assert captured.getvalue() == "", (
        "a deliberate stop must not log a spurious mid-session crash"
    )

    # (3) A live node with the event unset keeps watching (no log, still
    # alive) until the event fires — the poll cadence is the loop, not a
    # one-shot.
    captured.truncate(0)
    captured.seek(0)

    class LiveNode:
        def poll(self):
            return None

    shutdown = threading.Event()
    live_sock2 = FakeSock()
    thread = helper._start_web_exit_watch(LiveNode(), shutdown, live_sock2)
    thread.join(timeout=0.6)
    assert not live_sock2.closed, "a live node must keep the announced port"
    assert thread.is_alive(), "a live node must keep the watcher running"
    assert captured.getvalue() == ""
    shutdown.set()
    thread.join(timeout=5)
    assert not thread.is_alive(), "the event must release a live watcher"


def test_web_spawn_env_additions_are_exact():
    """The helper's caller-side additions on top of _node_child_env, pinned
    at the seam the helper itself uses (_web_spawn_env_additions): PORT
    (node's own ephemeral bind, string form — env values must be str),
    HOSTNAME pinned to loopback (Next reads it as the bind host — an
    inherited HOSTNAME would point the server at a foreign name),
    MANGAFLOW_API_ORIGIN at the fixed relay (rewrites are baked against
    39443), NODE_ENV=production (a dev-mode Next server would recompile
    on the fly). The historical pin built its own dict and asserted it —
    a tautology that stayed green through any real-call-site drift."""

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_envadd", str(HELPER)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    assert helper._web_spawn_env_additions(4321) == {
        "PORT": "4321",
        "HOSTNAME": "127.0.0.1",
        "MANGAFLOW_API_ORIGIN": f"http://127.0.0.1:{helper.WEB_RELAY_PORT}",
        "NODE_ENV": "production",
    }

    # Composed with the strip list: a MANGAFLOW_DESKTOP_* key surviving
    # composition means _node_child_env's strip list missed an exported
    # orchestration name (the additions dict is compile-time-constant and
    # cannot add one), and PORT must be a string (env values are strings;
    # an int PORT makes node's env write raise TypeError at spawn).
    env = helper._node_child_env()
    env.update(helper._web_spawn_env_additions(4321))
    assert not any(name.startswith("MANGAFLOW_DESKTOP_") for name in env), (
        "an exported orchestration name survived _node_child_env's strip list"
    )
    assert isinstance(env["PORT"], str)


def test_spawn_web_server_applies_the_pinned_additions(tmp_path, monkeypatch):
    """The USE pin, replacing the tautology: _spawn_web_server's real
    Popen must receive the pinned additions composed on the stripped
    base. The relay port is redirected to a free port (the fixed 39443
    stays free for a concurrently running helper), and Popen raises a
    sentinel right after capturing — the spawned sockets are function
    locals dropped at unwind, so nothing leaks past the test."""

    import importlib.util
    import socket as socket_module
    import types

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_spawnenv", str(HELPER)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    dist = tmp_path / "web"
    dist.mkdir()
    (dist / "server.js").write_text("module.exports = 1;", encoding="utf-8")

    free_relay = socket_module.socket()
    free_relay.bind(("127.0.0.1", 0))
    relay_port = free_relay.getsockname()[1]
    free_relay.close()
    monkeypatch.setattr(helper, "WEB_RELAY_PORT", relay_port)
    monkeypatch.setattr(helper, "_find_node", lambda web_dist: "/bin/false")

    captured = {}

    class _Captured(RuntimeError):
        pass

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        raise _Captured("captured")

    monkeypatch.setattr(helper.subprocess, "Popen", fake_popen)

    with pytest.raises(_Captured):
        helper._spawn_web_server(
            types.SimpleNamespace(web_dist=str(dist)), api_port=1234
        )

    assert captured["argv"][0] == "/bin/false"
    env = captured["env"]
    assert env["HOSTNAME"] == "127.0.0.1"
    assert env["MANGAFLOW_API_ORIGIN"] == f"http://127.0.0.1:{relay_port}"
    assert env["NODE_ENV"] == "production"
    assert env["PORT"].isdigit(), "node's ephemeral port, in string form"
    assert not any(
        name.startswith("MANGAFLOW_DESKTOP_") for name in env
    ), "handshake identity must not ride into the web child"


def test_web_exit_watch_poll_cadence_is_the_loop_heartbeat(monkeypatch):
    """The watcher's poll cadence (250ms) is its responsiveness contract:
    a death observed in cycle N must be logged by cycle N (the log and the
    detection are in the same iteration), and a live node must see
    poll() called repeatedly — a one-shot watcher would miss a death that
    happens after the first check."""

    import importlib.util
    import io
    import threading

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_cadence", str(HELPER)
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    class FakeSock:
        def __init__(self):
            self.closed = False
            self.shutdowns = 0

        def shutdown(self, how):
            self.shutdowns += 1

        def close(self):
            self.closed = True

    captured = io.StringIO()
    monkeypatch.setattr(helper, "_log", lambda message: captured.write(message + "\n"))

    class SequenceNode:
        """poll answers live for N cycles, then reports an exit code."""

        def __init__(self, live_cycles):
            self.live_cycles = live_cycles
            self.calls = 0

        def poll(self):
            self.calls += 1
            return None if self.calls <= self.live_cycles else 0

    # Death observed on the FIRST live-cycle boundary: exactly one log
    # line, named code, thread returns — no duplicate logging from the
    # outer loop re-entering.
    node = SequenceNode(live_cycles=2)
    shutdown = threading.Event()
    sock = FakeSock()
    thread = helper._start_web_exit_watch(node, shutdown, sock)
    thread.join(timeout=5)
    assert not thread.is_alive()
    lines = [line for line in captured.getvalue().splitlines() if line]
    assert len(lines) == 1 and "code 0" in lines[0], lines
    assert sock.closed, "the announced socket must close on the crash path"

    # A node that stays live sees repeated polls (the heartbeat), until
    # the shutdown event releases the watcher with no log and no close.
    captured.truncate(0)
    captured.seek(0)

    class LiveNode:
        calls = 0

        def poll(self):
            LiveNode.calls += 1
            return None

    live = LiveNode()
    shutdown2 = threading.Event()
    live_sock = FakeSock()
    thread2 = helper._start_web_exit_watch(live, shutdown2, live_sock)
    thread2.join(timeout=1.2)
    assert thread2.is_alive(), "a live node keeps the watcher running"
    assert LiveNode.calls >= 3, "the watcher must poll on its cadence"
    shutdown2.set()
    thread2.join(timeout=5)
    assert not thread2.is_alive()
    assert captured.getvalue() == "" and not live_sock.closed


def test_await_web_server_boot_logs_and_returns_false_on_boot_exit(monkeypatch):
    """The boot-exit leg's log contract: a node that dies during boot must
    (1) return False (the caller downgrades to static export) and (2) log
    the exit code — a silent downgrade leaves operators with no lifecycle
    evidence for the death. The returncode must be the node's OWN code.
    Popen double; no node, no ports; budget shortened for speed."""

    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location(
        "mangaflow_desktop_helper_bootlog", str(HELPER)
    )
    helper_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper_module)

    captured = io.StringIO()
    monkeypatch.setattr(
        helper_module, "_log", lambda message: captured.write(message + "\n")
    )

    class DeadAtBootNode:
        returncode = 7

        def poll(self):
            return 7

    monkeypatch.setattr(helper_module, "WEB_BOOT_TIMEOUT_SECONDS", 0.5)
    result = helper_module._await_web_server_boot(
        DeadAtBootNode(), 0
    )
    assert result is False, "a boot-exit node must downgrade, not serve"
    logged = captured.getvalue()
    assert "exited during boot" in logged and "code 7" in logged, logged
