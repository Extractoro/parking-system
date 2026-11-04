"""Opaque keyset cursor of the list (O1.5): base64url of {"t": created_at, "id": id} of the last row.

The next page is `(created_at, id) < (t, id)` in the order `created_at DESC, id DESC`: rows with the
same created_at are neither lost nor repeated, and no page numbers or OFFSET are involved.
"""

import base64
import binascii
import json
from datetime import datetime

from .problems import Problem


def encode(created_at: datetime, row_id: str) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "id": row_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(cursor: str) -> tuple[datetime, str]:
    """Any damage (bad base64, bad JSON, missing fields, naive time) -> 400."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        moment = datetime.fromisoformat(data["t"])
        row_id = data["id"]
        if moment.tzinfo is None or not isinstance(row_id, str) or not row_id:
            raise ValueError("incomplete cursor")
        return moment, row_id
    except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise Problem(400, "invalid-cursor", "Invalid cursor", "Use next_cursor of the previous page as is") from None
