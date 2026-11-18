"""Own columns of C09 «Паркінги» for generate_history.py (--extend columns.c09.py).

One source of truth: GARAGES builds the reference table `garages` (custom columns garage_id, name,
city, tariff_minor_per_hour) AND gives the tariff to amount_minor of the facts, so the reference
column and the amounts of the facts never disagree. The first row is the hot key `mall` (the spec
sets hot_key "mall"; the generator replaces key 0 with it, so the order here must start with mall).
Tariffs of mall and center are those of the demo data (migration 903): 3000 and 4000 kopecks per hour.

Every function gets the row number `n`, the supplied generator `rnd` (the only source of
randomness: the set must be reproducible) and the columns built so far `row`.
"""

import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

# garage_id, name, city, tariff in kopecks per started hour
GARAGES: list[tuple[str, str, str, int]] = [
    ("mall", "ТЦ Південний", "Київ", 3000),
    ("center", "Центр", "Київ", 4000),
    ("station", "Вокзал", "Київ", 3500),
    ("airport", "Аеропорт Бориспіль", "Бориспіль", 6000),
    ("podil", "Поділ", "Київ", 3500),
    ("obolon", "Оболонь", "Київ", 2500),
    ("lviv-rynok", "Площа Ринок", "Львів", 3000),
    ("lviv-forum", "Форум Львів", "Львів", 2500),
    ("kharkiv-center", "Харків Центр", "Харків", 2500),
    ("odesa-port", "Одеса Порт", "Одеса", 3000),
]
TARIFF: dict[str, int] = {garage_id: tariff for garage_id, _name, _city, tariff in GARAGES}

PERIOD_START = datetime(2026, 1, 1, tzinfo=UTC)
PERIOD_DAYS = 300
# hour of the day (UTC) when cars enter: mostly 6-18 UTC (8-20 Kyiv time)
HOUR_WEIGHTS = [1, 1, 1, 1, 2, 4, 8, 10, 10, 9, 9, 9, 9, 9, 9, 8, 7, 5, 3, 2, 1, 1, 1, 1]
HOURS = list(range(24))
MINUTES_MIN, MINUTES_MAX = 10, 600
LETTERS = "ABCEHIKMOPTX"  # letters of Ukrainian plates that look the same in Latin and Cyrillic


def garage_id(n: int, _rnd: random.Random, _row: dict[str, Any]) -> str:
    return GARAGES[n][0]


def garage_name(n: int, _rnd: random.Random, _row: dict[str, Any]) -> str:
    return GARAGES[n][1]


def garage_city(n: int, _rnd: random.Random, _row: dict[str, Any]) -> str:
    return GARAGES[n][2]


def garage_tariff(n: int, _rnd: random.Random, _row: dict[str, Any]) -> int:
    return GARAGES[n][3]


def plate(_n: int, rnd: random.Random, _row: dict[str, Any]) -> str:
    """Ukrainian-style plate: two letters, four digits, two letters (AA1234BC)."""
    return (
        rnd.choice(LETTERS) + rnd.choice(LETTERS) + f"{rnd.randrange(10000):04d}" + rnd.choice(LETTERS) + rnd.choice(LETTERS)
    )


def entered(_n: int, rnd: random.Random, _row: dict[str, Any]) -> datetime:
    """Entry moment in UTC within PERIOD_DAYS days, daytime hours more likely."""
    day = rnd.randrange(PERIOD_DAYS)
    hour = rnd.choices(HOURS, HOUR_WEIGHTS)[0]
    return PERIOD_START + timedelta(days=day, hours=hour, seconds=rnd.randrange(3600))


def minutes(_n: int, rnd: random.Random, _row: dict[str, Any]) -> int:
    """Parking time: log-normal, median 90 min, clipped to 10-600 min."""
    value = round(rnd.lognormvariate(math.log(90), 0.8))
    return min(MINUTES_MAX, max(MINUTES_MIN, value))


def amount_minor(_n: int, _rnd: random.Random, row: dict[str, Any]) -> int:
    """Amount in kopecks: every started hour is a full hour (ceil(minutes / 60) x tariff)."""
    return -(-row["minutes"] // 60) * TARIFF[row["garage_id"]]
