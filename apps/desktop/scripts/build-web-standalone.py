"""Build the Next standalone web bundle for the desktop shell (W-15 plan B).

Runs the standard production build (next.config.ts opts into
output:"standalone") and then performs the documented post-build steps:
- copy `.next/static` into the standalone tree (the standalone server does
  not serve the compile tree's static assets by itself);
- verify the rewrites destination is the helper's fixed relay port
  (127.0.0.1:39443) — a build made with a different MANGAFLOW_API_ORIGIN
  would silently proxy to the wrong target, so the manifest is checked here
  and the build fails loudly instead;
- MOVE the verified bundle out of `.next` into
  `apps/desktop/dist/web-standalone/` — every plain `next build`
  regenerates `.next` from scratch (re-baking :8000 and dropping the static
  copy), so the desktop bundle must live outside its reach.

Run this script again after any `npm run build`; the sidecar e2e asserts
the relocated bundle's manifest.

The destructive tail (rmtree + move + provenance stamp of
dist/web-standalone) runs under the shared dist/ build lock (#350): the
same lock build-frontend-static.sh takes around its dist/frontend swap, so
concurrent invocations (two shells, a CI matrix, an e2e runner that saw a
missing server.js) serialize instead of interleaving their delete/move
windows.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "apps" / "web"
DESKTOP_DIST = REPO / "apps" / "desktop" / "dist" / "web-standalone"
RELAY_ORIGIN = "http://127.0.0.1:39443"

# Stable lock path shared by every writer of apps/desktop/dist/ (#350).
DIST_LOCK_PATH = REPO / "apps" / "desktop" / "dist" / ".build.lock"
DIST_LOCK_TIMEOUT_SECONDS = 600.0

# npm's CLI shim is npm.cmd on Windows and npm everywhere else; this script
# runs from both the Windows packaging flow and run-sidecar-e2e.sh (Linux).
NPM = "npm.cmd" if os.name == "nt" else "npm"


@contextlib.contextmanager
def _dist_build_lock(
    path: Path = DIST_LOCK_PATH, timeout: float = DIST_LOCK_TIMEOUT_SECONDS
):
    """Exclusive cross-process lock around the destructive dist/ writes.

    flock where the platform has it (POSIX/CI — and the same mechanism
    dist-build-lock.sh uses when its bash comes from the same environment
    source, so bash and python writers interlock); otherwise an
    O_CREAT|O_EXCL lock file with retry + timeout. #383: the interlock
    assumes bash and python share one environment origin; this repo's
    main path (git bash + Windows-native python) has neither flock nor
    fcntl, so both sides take the lock-file branch — but a mixed install
    (MSYS2 bash WITH flock driving a Windows-native python WITHOUT
    fcntl) puts the two languages on different mechanisms that cannot
    see each other, so mutual exclusion silently fails and such hosts
    are unsupported. A fallback holder that crashes leaves the file
    behind; later writers then fail loudly after the timeout with the
    remedy in the message (dist/ is disposable build output).
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]

    if fcntl is not None:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise SystemExit(
                            f"dist build lock: timed out after {timeout}s "
                            f"waiting for {path}"
                        ) from error
                    time.sleep(0.25)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return

    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise SystemExit(
                    f"dist build lock: timed out after {timeout}s waiting for "
                    f"{path}; if no build is running, delete the stale lock "
                    "file (dist/ is disposable build output)"
                )
            time.sleep(0.25)
    try:
        os.write(fd, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(path)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> int:
    subprocess.run(
        [NPM, "run", "build", "--workspace", "@mangaflow/web"],
        cwd=REPO,
        check=True,
        # The desktop bundle bakes the helper's fixed relay port into its
        # rewrites at build time (see the comment in next.config.ts); the plain
        # default (8000) stays for every non-desktop form.
        env=dict(os.environ, MANGAFLOW_API_ORIGIN="http://127.0.0.1:39443"),
    )

    standalone = WEB / ".next" / "standalone" / "apps" / "web"
    server_js = standalone / "server.js"
    if not server_js.is_file():
        raise SystemExit(f"standalone build did not produce {server_js}")

    manifest = standalone / ".next" / "routes-manifest.json"
    destinations = json.loads(manifest.read_text(encoding="utf-8"))
    rewrites = destinations.get("rewrites", {})
    flat = json.dumps(rewrites)
    if RELAY_ORIGIN not in flat:
        raise SystemExit(
            "standalone rewrites do not target the helper relay "
            f"{RELAY_ORIGIN}; rebuild without MANGAFLOW_API_ORIGIN set "
            "(the relay origin is the documented build-time constant)"
        )

    static_dst = standalone / ".next" / "static"
    if static_dst.exists():
        shutil.rmtree(static_dst)
    shutil.copytree(WEB / ".next" / "static", static_dst)

    # Destructive section (#350): the rmtree+move replaces dist/web-standalone
    # while other writers touch sibling dist/ trees (build-frontend-static.sh)
    # and readers (the e2e's freshness gate) check it. The provenance stamp is
    # written inside the lock too, so a reader that sees the moved bundle can
    # never see it without its build-info.json.
    with _dist_build_lock():
        if DESKTOP_DIST.exists():
            shutil.rmtree(DESKTOP_DIST)
        DESKTOP_DIST.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(standalone), str(DESKTOP_DIST))

        # Build provenance: the e2e asserts the bundle was built from THIS
        # source tree, so a stale relocated bundle (dist/ is gitignored and
        # survives for days) can never silently test outdated UI code.
        build_info = {
            "source_commit": _git("rev-parse", "HEAD"),
            "apps_web_tree": _git("rev-parse", "HEAD:apps/web"),
            "relay_origin": RELAY_ORIGIN,
        }
        (DESKTOP_DIST / "build-info.json").write_text(
            json.dumps(build_info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    print("WEB_STANDALONE_READY", DESKTOP_DIST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
