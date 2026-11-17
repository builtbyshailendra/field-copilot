"""Sign-in, tokens and roles.

Users and roles come from the customer's identity provider in a real deployment (OIDC);
for the pilot and local demos there are seeded users per tenant with scrypt-hashed
passwords. Tokens are short-lived HS256 JWTs carrying user, tenant and roles. The web
app keeps the token in an httpOnly cookie; cookie requests must also send
X-Requested-With (a CSRF check browsers won't add cross-site).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

import jwt

from fieldcopilot.agent import User

DEV_USERS: dict[str, tuple[str, frozenset[str]]] = {
    "asha": ("corvane-freight", frozenset({"engineer"})),
    "ravi": ("corvane-freight", frozenset({"engineer", "oncall-lead"})),
    "j.patel": ("tallowmere-health", frozenset({"review-nurse"})),
    "nina": ("tallowmere-health", frozenset({"viewer"})),
}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"{salt.hex()}:{digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    salt, digest = stored.split(":")
    candidate = hash_password(password, bytes.fromhex(salt)).split(":")[1]
    return hmac.compare_digest(candidate, digest)


@dataclass
class AuthConfig:
    secret: str
    ttl_seconds: int = 8 * 3600


class UserDirectory:
    def __init__(self, demo_password: str):
        self._users = {name: (tenant, roles, hash_password(demo_password)) for name, (tenant, roles) in DEV_USERS.items()}

    def sign_in(self, username: str, password: str) -> User | None:
        entry = self._users.get(username)
        if not entry or not check_password(password, entry[2]):
            return None
        return User(username, entry[0], entry[1])


def issue(cfg: AuthConfig, user: User) -> str:
    now = int(time.time())
    return jwt.encode({"sub": user.name, "tenant": user.tenant, "roles": sorted(user.roles), "iat": now,
                       "exp": now + cfg.ttl_seconds}, cfg.secret, algorithm="HS256")


def verify(cfg: AuthConfig, token: str) -> User | None:
    try:
        claims = jwt.decode(token, cfg.secret, algorithms=["HS256"], options={"require": ["exp", "sub", "tenant"]})
    except jwt.PyJWTError:
        return None
    return User(claims["sub"], claims["tenant"], frozenset(claims.get("roles", [])))


def secret_from_env() -> str:
    secret = os.environ.get("FIELD_JWT_SECRET", "")
    if os.environ.get("APP_ENV", "dev") != "dev" and len(secret) < 32:
        raise RuntimeError("FIELD_JWT_SECRET must be set (32+ characters) outside dev")
    return secret or "dev-only-secret-change-me-0123456789abcdef"
