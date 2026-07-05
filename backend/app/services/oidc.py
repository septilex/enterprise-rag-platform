"""OIDC / SSO token verification layer (SEC-01).

A clean integration seam: the app verifies a bearer JWT and maps its claims to a
User. Two verifiers ship:

- ``JWKSVerifier``  — production: RS256 verified against the IdP's JWKS
  (Auth0/Okta/Entra/Keycloak), checking issuer + audience + expiry.
- ``DevJWTVerifier`` — local/staging: HS256 signed with a shared secret, so the
  SSO flow can be exercised without a real IdP.

No provider SDK is required; only PyJWT. Selection is by config.
"""

from __future__ import annotations

import json
import time
import urllib.request
from abc import ABC, abstractmethod

import jwt

from app.core.config import settings


class TokenError(Exception):
    """Raised when a bearer token is missing/invalid/expired."""


class TokenVerifier(ABC):
    @abstractmethod
    def verify(self, token: str) -> dict:
        """Return verified claims or raise TokenError."""
        ...


class DevJWTVerifier(TokenVerifier):
    """HS256 verifier for local/staging SSO testing (shared secret)."""

    def __init__(self, secret: str, audience: str | None = None):
        self._secret = secret
        self._audience = audience

    def verify(self, token: str) -> dict:
        try:
            return jwt.decode(
                token, self._secret, algorithms=["HS256"],
                audience=self._audience or None,
                options={"verify_aud": bool(self._audience)},
            )
        except jwt.PyJWTError as exc:
            raise TokenError(str(exc)) from exc


class JWKSVerifier(TokenVerifier):
    """RS256 verifier against a provider JWKS endpoint (production)."""

    def __init__(self, jwks_url: str, issuer: str, audience: str, cache_ttl: int = 3600):
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._cache_ttl = cache_ttl
        self._keys: dict = {}
        self._fetched = 0.0

    def _jwks(self) -> dict:
        if time.time() - self._fetched > self._cache_ttl or not self._keys:
            with urllib.request.urlopen(self._jwks_url, timeout=5) as resp:  # noqa: S310
                data = json.loads(resp.read())
            self._keys = {k["kid"]: k for k in data.get("keys", [])}
            self._fetched = time.time()
        return self._keys

    def verify(self, token: str) -> dict:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key = self._jwks().get(kid)
            if key is None:
                raise TokenError("signing key not found in JWKS")
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
            return jwt.decode(
                token, public_key, algorithms=["RS256"],
                audience=self._audience, issuer=self._issuer,
            )
        except jwt.PyJWTError as exc:
            raise TokenError(str(exc)) from exc


_verifier: TokenVerifier | None = None


def get_verifier() -> TokenVerifier:
    """Build the configured verifier once (JWKS in prod, HS256 for dev)."""
    global _verifier
    if _verifier is not None:
        return _verifier
    if settings.OIDC_JWKS_URL:
        _verifier = JWKSVerifier(
            settings.OIDC_JWKS_URL, settings.OIDC_ISSUER, settings.OIDC_AUDIENCE)
    elif settings.OIDC_DEV_SECRET:
        _verifier = DevJWTVerifier(settings.OIDC_DEV_SECRET, settings.OIDC_AUDIENCE or None)
    else:
        raise TokenError("OIDC not configured (set OIDC_JWKS_URL or OIDC_DEV_SECRET)")
    return _verifier


def reset_verifier() -> None:  # test helper
    global _verifier
    _verifier = None


def claims_to_identity(claims: dict) -> tuple[str, str]:
    """Extract (email, display_name) from standard OIDC claims."""
    email = claims.get("email") or claims.get("preferred_username") or claims.get("sub")
    if not email:
        raise TokenError("token has no email/sub claim")
    return email, claims.get("name") or email
