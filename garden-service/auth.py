"""Validate access tokens locally; never trust browser-supplied owner IDs."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt


bearer = HTTPBearer(auto_error=False)


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    unauthorized = HTTPException(
        401, "Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    config = getattr(request.app.state, "auth_settings", None)
    if config is None:
        raise HTTPException(503, "Authentication is not initialized")
    try:
        claims = jwt.decode(
            credentials.credentials, config.jwt_public_key, algorithms=["RS256"],
            issuer=config.jwt_issuer, audience=config.jwt_audience,
            options={"require": ["sub", "iss", "aud", "iat", "exp"]},
        )
        if not isinstance(claims["sub"], str) or not claims["sub"].strip() or len(claims["sub"]) > 100:
            raise unauthorized
        return claims["sub"]
    except jwt.InvalidTokenError:
        raise unauthorized


CurrentUser = Annotated[str, Depends(current_user)]
