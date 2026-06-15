"""
Authentication for the Blooms Agent.

The Blooms OS frontend signs users in against the Blooms OS Supabase project and
attaches their access token as `Authorization: Bearer <jwt>`. Until now the
server ignored that header entirely, so anyone on the internet could talk to the
agent — and the agent can read financials. This module closes that hole.

We validate the token by asking Supabase Auth who it belongs to (this verifies
the signature and expiry server-side, and works regardless of whether the
project uses legacy HS256 or the newer asymmetric JWTs). We then check the
caller's email against an allowlist.

This agent is Bileysi's *personal* assistant — it can read business financials —
so only she and Manny are allowed. A staff-facing SOP assistant should be a
separate endpoint with no financial tools and a broader allowlist.
"""
import logging

import httpx

import config

log = logging.getLogger("blooms.auth")


class AuthError(Exception):
    """Raised when a request is not authenticated/authorized. Carries an HTTP status."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


async def authenticate(authorization_header: str | None) -> dict:
    """Validate a Bearer token and authorize the caller.

    Returns a dict with the user's id + email on success.
    Raises AuthError(401/403/503) otherwise.
    """
    if not authorization_header or not authorization_header.lower().startswith("bearer "):
        raise AuthError(401, "Sign-in required.")

    token = authorization_header.split(" ", 1)[1].strip()
    if not token:
        raise AuthError(401, "Sign-in required.")

    # Ask Supabase Auth to validate the token and tell us who it is.
    url = f"{config.BLOOMS_OS_URL.rstrip('/')}/auth/v1/user"
    headers = {
        "apikey": config.BLOOMS_OS_ANON_KEY,
        "Authorization": f"Bearer {token}",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:  # network / timeout
        log.error(f"Auth service unreachable: {e}")
        raise AuthError(503, "Could not verify your session. Try again.")

    if resp.status_code != 200:
        raise AuthError(401, "Your session is invalid or expired. Sign in again.")

    user = resp.json()
    email = (user.get("email") or "").lower().strip()
    if not email:
        raise AuthError(401, "Your session is invalid or expired. Sign in again.")

    # Authorization: this assistant is personal to Bileysi + Manny.
    if config.ALLOWED_EMAILS and email not in config.ALLOWED_EMAILS:
        log.warning(f"Denied non-allowlisted user: {email}")
        raise AuthError(403, "This assistant isn't available for your account.")

    return {"id": user.get("id"), "email": email}
