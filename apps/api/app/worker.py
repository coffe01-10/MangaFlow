"""Launch RQ with the same dotenv settings as the API, including Redis AUTH."""

import os
import sys
from pathlib import Path

from rq.cli import cli as rq_cli

from app.config import get_settings


def main() -> None:
    api_root = Path(__file__).resolve().parents[1]
    os.chdir(api_root.parents[1])
    settings = get_settings()
    # Do not put credentials in command-line arguments or logs.
    os.environ["REDIS_URL"] = settings.redis_url
    args = ["worker", "--with-scheduler", "--path", str(api_root)]
    if sys.platform == "win32":
        args.extend(["--worker-class", "rq.worker.SpawnWorker"])
    args.append(settings.queue_name)
    rq_cli.main(args=args, prog_name="mangaflow-worker")
