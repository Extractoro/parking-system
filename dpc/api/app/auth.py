"""The API token: every /v1 route requires `Authorization: Bearer <API_TOKEN>`.

Use it as a router dependency: `APIRouter(prefix="/v1", dependencies=[Depends(require_token)])`.
"""

import hmac

from fastapi import Request

from . import config
from .problems import Problem


async def require_token(request: Request) -> None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), config.API_TOKEN.encode()):
        raise Problem(401, "unauthorized", "API token required", headers={"WWW-Authenticate": "Bearer"})
