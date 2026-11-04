"""Own columns for generate_history.py (--extend columns.example.py).

A function gets the row number `n`, the supplied random generator `rnd` (use ONLY it, never
`random.random()`: the data must stay reproducible) and the columns built so far `row`.
Every function takes all three; prefix the ones it does not use with `_`.
"""

import math
import random
from typing import Any


def loan_days(_n: int, rnd: random.Random, row: dict[str, Any]) -> int:
    """Loan length: mostly 14-21 days, a long tail for bestsellers."""
    base = rnd.choice([14, 14, 21, 21, 28])
    tail = int(-math.log(1 - rnd.random()) * (10 if row["book_id"] == "b-hot" else 4))
    return base + tail


def fine_minor(_n: int, _rnd: random.Random, row: dict[str, Any]) -> int:
    """Fine in minor currency units (integers: the report and the reference must agree exactly)."""
    overdue = max(0, row["days"] - 21)
    return overdue * 500
