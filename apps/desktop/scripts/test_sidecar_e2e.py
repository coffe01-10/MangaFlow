"""Desktop sidecar end-to-end (V02-54): real API sidecar + fake model channel.

Drives the exact startup protocol the Tauri shell uses, entirely over HTTP
against the real FastAPI app (Alembic-migrated SQLite, jobs executed by the
in-process local executor — the shipped default without Redis), with the fake
model channel proving the 生成→候选 loop makes zero provider calls.

Run via scripts/run-sidecar-e2e.sh (needs .venv-desktop with apps/api deps).
"""

from __future__ import annotations

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

    def __init__(self, user_data: Path, web_dist: Path | None = None) -> None:
        self.token = os.urandom(16).hex()
        self.user_data = user_data
        self.web_dist = web_dist
        self.runtime = user_data / "runtime" / f"mangaflow-desktop-{self.token}"
        self.runtime.mkdir(parents=True)
        self.journal = self.runtime / "owner.json"
        self.stderr_log = (self.runtime / "helper.stderr.log").open("wb")
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
        # server and announces its loopback origin in READY and the journal.
        if self.web_dist is not None:
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

    def stop(self) -> int:
        if os.name == "nt":
            # The production Windows stop channel: closing the helper's stdin
            # makes its EOF watcher self-terminate (uvicorn graceful exit);
            # escalate to a hard kill of the direct child if it refuses.
            try:
                if self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.close()
                code = self.process.wait(timeout=20)
                self.stderr_log.close()
                return code
            except subprocess.TimeoutExpired:
                self.process.kill()
                code = self.process.wait(timeout=5)
                self.stderr_log.close()
                return code
        # SIGTERM reaches the whole session (uvicorn installs graceful
        # shutdown handlers); escalate to SIGKILL if it refuses.
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            code = self.process.wait(timeout=15)
            self.stderr_log.close()
            return code
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            code = self.process.wait(timeout=5)
            self.stderr_log.close()
            return code


@pytest.fixture()
def desktop(tmp_path: Path):
    user_data = tmp_path / "user-data"
    (user_data / "data").mkdir(parents=True)
    shell = DesktopShell(user_data)
    try:
        record = shell.handshake()
        shell.wait_health()
        yield shell, user_data, record
    finally:
        exit_code = shell.stop()
        assert exit_code == 0, f"helper exited with {exit_code}"


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


def _web_dist_dir() -> Path:
    """The Next standalone bundle (plan B, W-15): produced and relocated to
    apps/desktop/dist/web-standalone by scripts/build-web-standalone.py
    (build + relay-manifest verify + static copy; the relocation keeps it
    out of reach of plain `next build`, which regenerates `.next` with the
    :8000 destination and no static copy)."""

    dist = REPO_ROOT / "apps/desktop/dist/web-standalone"
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
        (HELPER.parent / "node" / ("node.exe" if os.name == "nt" else "bin/node"))
    ).exists():
        pytest.skip("no node runtime available for the standalone server")
    shell = DesktopShell(tmp_path / "user-data", web_dist=_web_dist_dir())
    (shell.user_data / "data").mkdir(parents=True, exist_ok=True)
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
        # Journal identity-only: web fields are identity too, no commands/env.
        assert set(record).issubset(
            {"version", "token", "role", "state", "pid", "pid_starttime", "port",
             "api_origin", "web_origin", "web_port", "started_at", "grandchild_pid"}
        ), record
    finally:
        exit_code = shell.stop()
        assert exit_code == 0, f"helper exited with {exit_code}"
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
