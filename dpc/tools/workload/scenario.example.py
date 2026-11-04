"""Example for "Міська бібліотека": a book drop returns a copy, then the state is polled to the end.
Copy it as tools/workload/my-scenario.py and change the route and fields to your card.
From the project root:

    uv run --no-project --python 3.14 python tools/workload/run.py tools/workload/scenario.example.py \\
        --base api=http://localhost:8000 --env token=API_TOKEN --set branch_id=central \\
        --duration 30 --rate 10 --concurrency 20 --out runs/returns-01
"""

from urllib.parse import quote
from uuid import uuid4


def validate(bases, settings):
    if "api" not in bases or not settings.get("branch_id") or not settings.get("token"):
        raise ValueError("supply --base api=..., --env token=..., --set branch_id=...")


def scenario(ctx):
    headers = {"Authorization": f"Bearer {ctx.settings['token']}"}
    tag = uuid4().hex[:12]

    accepted = ctx.request(
        "POST",
        "/v1/returns",
        body={
            "return_id": f"r_{tag}",
            "copy_id": f"CEN-{tag}",
            "loan_id": f"l_{tag}",
            "branch_id": ctx.settings["branch_id"],
            "station_id": "st-1",
        },
        headers={**headers, "Idempotency-Key": tag},
        expected=(202, 429),
        label="submit",
    )
    if accepted.status == 429:
        return

    final = ctx.poll(
        f"/v1/returns/{quote(str(accepted.json()['id']), safe='')}",
        lambda response: response.json()["state"] in {"done", "rejected", "failed"},
        headers=headers,
        label="state",
    )

    ctx.check(final.json()["state"] != "failed")
