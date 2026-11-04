"""Task metrics of the stages of a Spark application (lab 4): where the skew is.

Reads the monitoring REST API of a running job (driver UI, port 4040) or of the history
server and prints, for every complete stage, the number of successful tasks, max and median
task duration, max and median records read (shuffle + input) and the ratio max / median.
A long task is a reason to look closer, not by itself a proof of data skew.
Standard library only.

Run from the project root:

    python tools/spark_stages.py http://localhost:4040
    python tools/spark_stages.py http://localhost:4040 --stage 7 --tasks

Finding the stage and naming the cause and the remedy is the task of O4.4; the tool only
collects the numbers that the Spark UI shows on several pages.
"""

import argparse
import json
import statistics
import urllib.request
from typing import Any

PAGE = 1000
HEADER = (
    f"{'stage':>5}  {'attempt':>7}  {'tasks':>5}  {'max ms':>7}  {'med ms':>7}  {'max/med':>7}  "
    f"{'max rec':>9}  {'med rec':>9}  name"
)


def get(base: str, path: str) -> Any:
    with urllib.request.urlopen(f"{base}/api/v1{path}", timeout=10) as response:
        return json.load(response)


def successful_tasks(base: str, app: str, stage: dict) -> list[dict]:
    """All successful tasks of a stage attempt, read page by page."""
    path = f"/applications/{app}/stages/{stage['stageId']}/{stage['attemptId']}/taskList"
    tasks: list[dict] = []
    offset = 0

    while True:
        page = get(base, f"{path}?offset={offset}&length={PAGE}")
        tasks.extend(task for task in page if task.get("status") == "SUCCESS")
        if len(page) < PAGE:
            return tasks
        offset += len(page)


def records_read(task: dict) -> int:
    metrics = task.get("taskMetrics", {})
    shuffle = metrics.get("shuffleReadMetrics", {}).get("recordsRead", 0)
    source = metrics.get("inputMetrics", {}).get("recordsRead", 0)
    return shuffle + source


def ratio(values: list[float]) -> float:
    middle = statistics.median(values)
    return max(values) / middle if middle else float("inf")


def stage_line(stage: dict, tasks: list[dict]) -> str:
    durations = [task["duration"] for task in tasks]
    records = [records_read(task) for task in tasks]
    return (
        f"{stage['stageId']:>5}  {stage['attemptId']:>7}  {len(tasks):>5}  "
        f"{max(durations):>7}  {statistics.median(durations):>7.0f}  {ratio(durations):>7.1f}  "
        f"{max(records):>9}  {statistics.median(records):>9.0f}  {stage['name'][:40]}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ui", help="http://localhost:4040 or the history server")
    parser.add_argument("--app", help="application id (default: the first one)")
    parser.add_argument("--stage", type=int, help="only this stage")
    parser.add_argument("--tasks", action="store_true", help="also print the duration of every task")
    args = parser.parse_args()

    app = args.app
    if app is None:
        applications = get(args.ui, "/applications")
        if not applications:
            raise SystemExit("no applications found at this Spark UI")
        app = applications[0]["id"]

    stages = [stage for stage in get(args.ui, f"/applications/{app}/stages") if stage["status"] == "COMPLETE"]
    if args.stage is not None:
        stages = [stage for stage in stages if stage["stageId"] == args.stage]
    if not stages:
        raise SystemExit("no complete stages match the selection")

    print(f"application {app}")
    print(HEADER)
    for stage in sorted(stages, key=lambda s: (s["stageId"], s["attemptId"])):
        tasks = successful_tasks(args.ui, app, stage)
        if not tasks:
            continue

        print(stage_line(stage, tasks))
        if args.tasks:
            durations = sorted((task["duration"] for task in tasks), reverse=True)
            print("       durations, ms:", " ".join(map(str, durations)))


if __name__ == "__main__":
    main()
