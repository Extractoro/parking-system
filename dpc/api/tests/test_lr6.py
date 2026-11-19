"""DPC lab 6: the generation check on the resource (O6.2) and the check of the history (O6.3).

Against the running stand (docker compose up -d api etcd coordinator-a coordinator-b). Every test makes its own
garage t-xxxx with a tariff: its fence row is 'tariff/t-xxxx', so the registrations of the tests never touch the
generations of mall and center that the live coordinators hold. The tariff, the fence and the history of the test
garage are removed afterwards (the Web catalog lists garages with a tariff; the global check must stay clean).
"""

import uuid

import pytest
from conftest import make_garage


@pytest.fixture
def tgarage(db):
    gid = make_garage(db, 4)
    db.execute("INSERT INTO tariff (garage_id, minor_per_hour) VALUES (%s, 1000)", (gid,))
    yield gid
    db.execute("DELETE FROM tariff_history WHERE garage_id = %s", (gid,))
    db.execute("DELETE FROM fence WHERE resource = %s", (f"tariff/{gid}",))
    db.execute("DELETE FROM tariff WHERE garage_id = %s", (gid,))


def register(api, garage, owner, generation):
    return api.post("/v1/tariff/generations", json={"garage_id": garage, "owner": owner, "generation": generation})


def write(api, garage, owner, generation, minor):
    return api.put(f"/v1/tariff/{garage}", json={"minor_per_hour": minor, "owner": owner, "generation": generation})


def tariff(db, garage):
    return db.execute("SELECT minor_per_hour, owner, generation FROM tariff WHERE garage_id = %s",
                      (garage,)).fetchone()


def history(db, garage):
    return db.execute("SELECT owner, generation, accepted, reason FROM tariff_history WHERE garage_id = %s ORDER BY id",
                      (garage,)).fetchall()


def test_only_a_larger_generation_is_registered(api, db, tgarage):
    assert register(api, tgarage, "coordinator-a", 5).status_code == 200
    again = register(api, tgarage, "coordinator-a", 5)  # a retry of the same owner after a lost answer
    assert again.status_code == 200 and again.json()["repeated"] is True
    same_other = register(api, tgarage, "coordinator-b", 5)
    assert same_other.status_code == 409 and same_other.json()["current_generation"] == "5"
    assert register(api, tgarage, "coordinator-b", 3).status_code == 409
    newer = register(api, tgarage, "coordinator-b", 7)
    assert newer.status_code == 200 and newer.json()["previous_generation"] == "5"
    assert [(h["owner"], h["generation"], h["reason"]) for h in history(db, tgarage)] == [
        ("coordinator-a", 5, "registered"), ("coordinator-b", 7, "registered")
    ]


def test_stale_and_unregistered_generations_are_rejected(api, db, tgarage):
    assert register(api, tgarage, "coordinator-a", 10).status_code == 200
    ok = write(api, tgarage, "coordinator-a", 10, 2500)
    assert ok.status_code == 200 and ok.json()["reason"] == "accepted"
    assert register(api, tgarage, "coordinator-b", 11).status_code == 200

    stale = write(api, tgarage, "coordinator-a", 10, 9900)  # the old owner after a pause
    assert stale.status_code == 409
    assert stale.json()["reason"] == "stale" and stale.json()["current_generation"] == "11"
    future = write(api, tgarage, "coordinator-b", 12, 9800)  # not registered yet
    assert future.status_code == 409 and future.json()["reason"] == "unregistered"

    assert tariff(db, tgarage) == {"minor_per_hour": 2500, "owner": "coordinator-a", "generation": 10}
    assert history(db, tgarage) == [
        {"owner": "coordinator-a", "generation": 10, "accepted": True, "reason": "registered"},
        {"owner": "coordinator-a", "generation": 10, "accepted": True, "reason": "accepted"},
        {"owner": "coordinator-b", "generation": 11, "accepted": True, "reason": "registered"},
        {"owner": "coordinator-a", "generation": 10, "accepted": False, "reason": "stale"},
        {"owner": "coordinator-b", "generation": 12, "accepted": False, "reason": "unregistered"},
    ]
    check = api.get("/v1/history/check", params={"garage_id": tgarage}).json()
    assert check["ok"] is True and check["rejected"] == 2


def test_unknown_garage_and_invalid_body(api):
    assert write(api, f"t-{uuid.uuid4().hex[:10]}", "coordinator-a", 1, 100).status_code == 404
    assert api.put("/v1/tariff/mall", json={"minor_per_hour": 0, "owner": "x", "generation": 1}).status_code == 400
    assert register(api, "mall", "coordinator-a", 0).status_code == 400


def test_check_finds_violations_in_prepared_history(api, db, tgarage):
    def row(owner, generation, accepted, reason, minor=None):
        db.execute("""INSERT INTO tariff_history (garage_id, minor_per_hour, owner, generation, accepted, reason)
                      VALUES (%s, %s, %s, %s, %s, %s)""", (tgarage, minor, owner, generation, accepted, reason))

    row("coordinator-a", 20, True, "registered")
    row("coordinator-a", 20, True, "accepted", 3000)
    row("coordinator-b", 21, True, "registered")
    row("coordinator-b", 21, True, "accepted", 2900)
    db.execute("UPDATE tariff SET minor_per_hour = 2900, owner = 'coordinator-b', generation = 21 WHERE garage_id = %s",
               (tgarage,))
    clean = api.get("/v1/history/check", params={"garage_id": tgarage}).json()
    assert clean["ok"] is True and clean["violations"] == []

    # what a resource without protection lets through: the paused old owner writes after the new registration
    row("coordinator-a", 20, True, "unfenced", 3100)
    db.execute("UPDATE tariff SET minor_per_hour = 3100, owner = 'coordinator-a', generation = 20 WHERE garage_id = %s",
               (tgarage,))
    bad = api.get("/v1/history/check", params={"garage_id": tgarage}).json()
    assert bad["ok"] is False
    assert [(v["kind"], v["owner"], v["generation"], v["registered"]) for v in bad["violations"]] == [
        ("stale-accepted", "coordinator-a", "20", "21")
    ]

    # the tariff no longer equals the last accepted write
    db.execute("UPDATE tariff SET minor_per_hour = 4444 WHERE garage_id = %s", (tgarage,))
    kinds = [v["kind"] for v in api.get("/v1/history/check", params={"garage_id": tgarage}).json()["violations"]]
    assert kinds == ["stale-accepted", "tariff-mismatch"]


def test_coordinator_reports_the_owner_of_the_role(api):
    body = api.get("/v1/coordinator").json()
    if body["owner"] is None:
        pytest.skip("no coordinator holds the role (docker compose up -d coordinator-a coordinator-b)")
    assert body["owner"] in ("coordinator-a", "coordinator-b") and body["lease_ttl_s"] <= 5
    registered = {r["garage_id"]: r for r in body["registered"]}
    # the owner registers its generation (the create_revision of the key) for the garages it manages
    assert registered["mall"]["generation"] == body["generation"] and registered["mall"]["owner"] == body["owner"]


def test_routes_require_the_token(api):
    for path in ("/v1/coordinator", "/v1/history", "/v1/history/check", "/v1/tariff"):
        assert api.get(path, headers={"Authorization": ""}).status_code == 401
