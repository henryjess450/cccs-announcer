"""Sign-in enforcement for the web layer.

Three things happen on every request that changes something:

1. **Session check.** The cookie is looked up server-side, which also slides
   the idle window and rejects sessions belonging to accounts that have since
   been turned off.
2. **CSRF check.** The session's own token must be echoed in a header. A cookie
   alone is not enough, because a browser attaches cookies to requests started
   by other sites. Announcements are exactly the kind of thing you do not want
   a malicious page to be able to trigger from a staff member's browser.
3. **First-run setup.** The administrator account the system creates for
   itself can do nothing except claim itself -- name, username and password.
   Ordinary staff keep whatever password they were given and are never
   stopped by this.

Errors carry a `reason` alongside the human message so the page can react --
sign-in again, show the password screen, show the rate-limit notice -- without
parsing English.
"""

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import HTTPException, Request

from .accounts import User
from .security import tokens_match

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Reachable while the first-run administrator account is still unclaimed.
PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/api/me",
    "/api/password",
    "/api/setup",
    "/api/logout",
    "/api/config",
}


class AppError(HTTPException):
    """An HTTPException that also carries a machine-readable reason."""

    def __init__(self, status_code: int, message: str, reason: str, **extra):
        super().__init__(status_code=status_code, detail=message)
        self.reason = reason
        self.extra = extra


def services_of(request: Request):
    return request.app.state.services


def load_session(request: Request) -> Optional[Tuple[User, dict]]:
    """Look up the session once per request and cache it on the request."""
    cached = getattr(request.state, "auth_result", "unset")
    if cached != "unset":
        return cached
    services = services_of(request)
    token = request.cookies.get(services.config.session_cookie_name)
    result = services.accounts.load_session(token)
    request.state.auth_result = result
    return result


def optional_user(request: Request) -> Optional[User]:
    result = load_session(request)
    return result[0] if result else None


def require_user(request: Request) -> User:
    result = load_session(request)
    if result is None:
        raise AppError(401, "Please sign in again.", "signed_out")
    user, session = result

    if request.method not in SAFE_METHODS:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not tokens_match(supplied, session.get("csrf_token", "")):
            raise AppError(
                403,
                "This page is out of date. Reload it and try again.",
                "bad_csrf",
            )

    # In practice this is only ever the first-run administrator account: staff
    # accounts are created without the flag.
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED_PATHS:
        raise AppError(
            403,
            "Finish setting up this account before making announcements.",
            "password_change_required",
        )
    return user


def require_admin(request: Request) -> User:
    user = require_user(request)
    if not user.is_admin:
        raise AppError(
            403,
            "Only an administrator can do that.",
            "not_admin",
        )
    return user
