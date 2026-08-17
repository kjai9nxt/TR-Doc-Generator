"""Google Sign-In (OAuth) verification, restricted to the org domain.

The frontend obtains a Google ID token (JWT) via Google Identity Services and
sends it as `Authorization: Bearer <token>`. We verify that token server-side
(signature, audience, expiry) with google-auth, then enforce:
  - the email is verified by Google, and
  - it belongs to the allowed domain (hd claim or @domain), and
  - admin status for the global dashboard.

Verifying the ID token per request is stateless and simple; Google ID tokens are
short-lived (~1h) and the frontend refreshes them via GIS.
"""
from __future__ import annotations

from . import config


class AuthError(Exception):
    """Raised when a credential is missing, invalid, or not allowed."""


def is_admin(email: str) -> bool:
    admins = {e.lower() for e in config.auth().get("admin_emails", [])}
    return (email or "").lower() in admins


def _user_from_claims(idinfo: dict) -> dict:
    """Pure claim-check (no network) — enforce verified email + allowed domain.
    Factored out so it can be unit-tested without a real Google token."""
    if not idinfo.get("email_verified"):
        raise AuthError("Your Google email is not verified.")
    email = (idinfo.get("email") or "").lower()
    domain = (config.auth().get("allowed_domain") or "").lower()
    hd = (idinfo.get("hd") or "").lower()
    if not domain:
        raise AuthError("Server auth is misconfigured (no allowed_domain).")
    if not (email.endswith("@" + domain) or hd == domain):
        raise AuthError(f"Access is restricted to {domain} accounts.")
    return {
        "email": email,
        "name": idinfo.get("name") or email.split("@")[0],
        "picture": idinfo.get("picture"),
        "is_admin": is_admin(email),
    }


class AuthUnavailable(AuthError):
    """Google could not be REACHED to check the token.

    Distinct from AuthError on purpose: one means "we could not ask", the other means
    "we asked and the answer was no". Telling a user their sign-in was rejected when
    the server simply could not open a socket sends them to re-authenticate over and
    over against a problem that has nothing to do with their account.
    """


# Verified tokens, keyed by a hash of the credential: {digest: (expires_at, user)}.
#
# WHY THIS EXISTS. verify_oauth2_token() fetches Google's public signing certificates
# over the network EVERY time it is called, and current_user() calls it on every single
# API request — so each page load made a handful of round-trips to googleapis.com just
# to re-verify the same token, and one flaky moment on that connection locked the whole
# app out. A Google ID token is signed and carries its own expiry, so once verified it
# stays valid until it expires; re-asking Google seconds later learns nothing.
#
# The window is capped well below the token's own lifetime so a revoked session cannot
# stay usable for long.
_VERIFIED: dict = {}
_CACHE_SECONDS = 300


def _cache_get(digest: str):
    import time
    hit = _VERIFIED.get(digest)
    if not hit:
        return None
    expires, user = hit
    if expires <= time.time():
        _VERIFIED.pop(digest, None)
        return None
    return user


def verify_credential(credential: str) -> dict:
    """Verify a Google ID token and return the allowed user dict, or raise AuthError.

    Hits the network only on a cache miss, and distinguishes "Google is unreachable"
    from "this token is not acceptable".
    """
    if not credential:
        raise AuthError("Missing credential.")
    client_id = config.google_client_id()
    if not client_id:
        raise AuthError("Server is missing GOOGLE_CLIENT_ID — set it in .env.")

    import hashlib
    import time
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    cached = _cache_get(digest)
    if cached is not None:
        return cached

    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as grequests
        from google.auth import exceptions as gexc
    except ImportError as e:
        raise AuthError("google-auth not installed on the server.") from e

    last = None
    for attempt in range(2):          # a TLS/network blip is usually gone by the retry
        try:
            idinfo = id_token.verify_oauth2_token(
                credential, grequests.Request(), client_id)
            user = _user_from_claims(idinfo)
            # Never outlive the token itself.
            exp = float(idinfo.get("exp") or 0)
            ttl = min(_CACHE_SECONDS, max(0.0, exp - time.time()))
            if ttl > 0:
                _VERIFIED[digest] = (time.time() + ttl, user)
            return user
        except ValueError as e:
            # Bad signature, wrong audience, or expired token — asking again will not
            # change the answer.
            raise AuthError("Invalid or expired Google sign-in. Please sign in again.") from e
        except gexc.TransportError as e:
            last = e
            if attempt == 0:
                time.sleep(0.6)
                continue
    raise AuthUnavailable(
        "Could not reach Google to verify the sign-in, so the server cannot confirm who "
        "you are. This is a network problem on the server, not a problem with your "
        "account — a proxy or firewall between it and www.googleapis.com is the usual "
        f"cause. Try again in a moment. ({type(last).__name__}: {last})")
