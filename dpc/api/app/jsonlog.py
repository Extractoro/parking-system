import json
import os
import sys
from datetime import UTC, datetime

from . import config


def log(event: str, **fields: object) -> None:
    line = {
        "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "instance": config.INSTANCE,
        "event": event,
    }
    line.update(fields)
    text = json.dumps(line, ensure_ascii=False, default=str) + "\n"
    sys.stdout.write(text)
    sys.stdout.flush()
    if config.INSTANCE.startswith("ray") and os.getpid() != 1:
        _to_container_log(text)


def _to_container_log(text: str) -> None:
    """A Ray worker process writes into the log files of Ray; the same line also goes to the output of
    PID 1 of the container (`ray start --block`), so `docker compose logs ray-worker` shows it."""
    try:
        with open("/proc/1/fd/1", "a", encoding="utf-8") as out:
            out.write(text)
    except OSError:
        pass
