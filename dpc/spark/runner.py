"""spark-runner: starts your PySpark jobs on request of the api (standard library only).

  POST /jobs          {"script": "report.py", "args": ["--partitions", "16"], "py_files": ["common.py"]}
                      -> 202 {"run": ..., "state": "running", ...}; 409 if the same script is running
  GET  /jobs          all jobs of this process, newest first
  GET  /jobs/<run>    one job: state (running | done | failed), exit code, seconds, log tail
  GET  /health
Scripts are files of /jobs (spark/jobs/ of the project, copied into the image). In `args`
the text {run} is replaced with the run id, so a job can write its result to a folder of
its own and publish it atomically. Jobs run with spark-submit in client mode: the driver
lives in this container (its UI on port 4040), the executors on the Spark workers.
What a job computes, how its result is published and how the api reads it are lab 4 and 5.
"""

import json
import os
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MASTER = os.environ.get("SPARK_MASTER", "spark://spark-master:7077")
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "/jobs"))
EXTRA_CONF = os.environ.get("SPARK_SUBMIT_CONF", "spark.ui.port=4040,spark.executor.memory=1g")
LOG_LINES = 200
_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.py$")

type Job = dict[str, Any]


class BadRequest(Exception):
    pass


def parse_request(raw: bytes) -> tuple[str, list[str], list[str]]:
    """Body of POST /jobs -> (script, args, py_files); any other shape is a BadRequest (400)."""
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise BadRequest(f"body is not JSON: {exc}") from None

    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    script = body.get("script")
    if not isinstance(script, str) or not script:
        raise BadRequest("script must be a non-empty string")

    lists = {name: body.get(name, []) for name in ("args", "py_files")}
    for name, value in lists.items():
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise BadRequest(f"{name} must be a list of strings")

    return script, lists["args"], lists["py_files"]


class JobStore:
    """Jobs of this process; a thread follows each spark-submit and keeps the tail of its log."""

    def __init__(self, submit: list[str] | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._submit = submit or ["spark-submit"]

    def start(self, script: str, args: list[str], py_files: list[str]) -> Job:
        for name in [script, *py_files]:
            if not _NAME.match(name) or not (JOBS_DIR / name).is_file():
                raise BadRequest(f"no such job file in {JOBS_DIR}: {name}")
        if not all(isinstance(arg, str) for arg in args):
            raise BadRequest("args must be strings")

        run = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        conf = [part for item in EXTRA_CONF.split(",") if item for part in ("--conf", item)]
        shipped = ["--py-files", ",".join(str(JOBS_DIR / name) for name in py_files)] if py_files else []
        command = [
            *self._submit,
            "--master",
            MASTER,
            *conf,
            *shipped,
            str(JOBS_DIR / script),
            *(arg.replace("{run}", run) for arg in args),
        ]

        job: Job = {
            "run": run,
            "script": script,
            "args": args,
            "state": "running",
            "started": time.time(),
            "log": [],
        }
        with self._lock:
            if any(other["script"] == script and other["state"] == "running" for other in self._jobs.values()):
                raise RuntimeError(f"{script} is already running")
            self._jobs[run] = job

        threading.Thread(target=self._follow, args=(job, command), daemon=True).start()
        return job

    def _follow(self, job: Job, command: list[str]) -> None:
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
                assert process.stdout is not None
                for line in process.stdout:
                    job["log"] = [*job["log"][-(LOG_LINES - 1) :], line.rstrip()]
            job["exit_code"] = process.returncode
            job["state"] = "done" if process.returncode == 0 else "failed"
        except OSError as exc:
            job["log"].append(f"cannot start: {exc}")
            job["exit_code"] = None
            job["state"] = "failed"

        job["seconds"] = round(time.time() - job["started"], 1)

    def all(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: -job["started"])

    def get(self, run: str) -> Job | None:
        with self._lock:
            return self._jobs.get(run)


def public(job: Job) -> Job:
    return {**job, "log": job["log"][-30:]}


store = JobStore()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: Any) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/jobs":
            self._send(200, {"jobs": [public(job) for job in store.all()]})
        elif self.path.startswith("/jobs/"):
            job = store.get(self.path.removeprefix("/jobs/"))
            self._send(200, public(job)) if job else self._send(404, {"error": "no such run"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/jobs":
            self._send(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "bad Content-Length"})
            return

        try:
            job = store.start(*parse_request(self.rfile.read(length)))
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._send(409, {"error": str(exc)})
        else:
            self._send(202, public(job))

    def log_message(self, *_: Any) -> None:  # quiet: the jobs log for themselves
        pass


if __name__ == "__main__":
    Path("/data/spark-events").mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
