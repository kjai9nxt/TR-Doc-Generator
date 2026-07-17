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


def verify_credential(credential: str) -> dict:
    """Verify a Google ID token and return the allowed user dict, or raise
    AuthError. Network call (fetches/caches Google's signing certs)."""
    if not credential:
        raise AuthError("Missing credential.")
    client_id = config.google_client_id()
    if not client_id:
        raise AuthError("Server is missing GOOGLE_CLIENT_ID — set it in .env.")
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as grequests
    except ImportError as e:
        raise AuthError("google-auth not installed on the server.") from e
    try:
        idinfo = id_token.verify_oauth2_token(credential, grequests.Request(), client_id)
    except ValueError as e:
        # bad signature, wrong audience, or expired token
        raise AuthError("Invalid or expired Google sign-in. Please sign in again.") from e
    return _user_from_claims(idinfo)
