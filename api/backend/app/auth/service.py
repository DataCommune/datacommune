"""Auth service: API key generation, password hashing, JWT, email tokens."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from app.config import settings

# ---------------------------------------------------------------------------
# Password hashing (bcrypt via pwdlib)
# ---------------------------------------------------------------------------
_password_hash = PasswordHash((BcryptHasher(),))


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return _password_hash.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return _password_hash.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT tokens (PyJWT / HS256)
# ---------------------------------------------------------------------------


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT access token.

    Args:
        data: Payload claims (typically {"sub": user_email}).
        expires_delta: Custom expiry. Defaults to settings.jwt_expire_minutes.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + (
        expires_delta
        if expires_delta
        else timedelta(minutes=settings.jwt_expire_minutes)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    """Decode and validate a JWT access token.

    Returns:
        Decoded payload dict, or None if token is invalid/expired.
    """
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


# ---------------------------------------------------------------------------
# Email verification / password reset tokens (itsdangerous)
# ---------------------------------------------------------------------------
_email_serializer = URLSafeTimedSerializer(
    settings.email_token_secret or (settings.jwt_secret_key + ":email")
)
_reset_serializer = URLSafeTimedSerializer(
    settings.reset_token_secret or (settings.jwt_secret_key + ":reset")
)


def create_verification_token(email: str) -> str:
    """Create a URL-safe signed token for email verification."""
    return _email_serializer.dumps(email, salt="email-verify")


def verify_email_token(token: str, max_age: int = 3600) -> str | None:
    """Verify an email verification token (default 1h expiry).

    Returns:
        The email address, or None if token is invalid/expired.
    """
    try:
        return _email_serializer.loads(token, salt="email-verify", max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def credential_version(password_hash: str) -> str:
    """Opaque credential generation; never expose a password hash in a token."""
    return hmac.new(
        settings.jwt_secret_key.encode(),
        ("credential-version:" + password_hash).encode(),
        hashlib.sha256,
    ).hexdigest()


def session_matches_credential(payload: dict, user: dict) -> bool:
    """Legacy sessions require login; resets revoke every older generation."""
    version = payload.get("credential_version")
    return isinstance(version, str) and hmac.compare_digest(
        version, credential_version(user["password_hash"])
    )


def create_reset_token(email: str, password_hash: str) -> str:
    """Bind a random reset nonce to the current credential generation."""
    return _reset_serializer.dumps(
        {
            "email": email,
            "credential_version": credential_version(password_hash),
            "nonce": secrets.token_urlsafe(32),
        },
        salt="password-reset",
    )


def verify_reset_token(token: str, max_age: int = 1800) -> dict | None:
    """Verify a password reset token (default 30min expiry).

    Returns:
        Validated claims, or None for expired, malformed or legacy tokens.
    """
    try:
        claims = _reset_serializer.loads(token, salt="password-reset", max_age=max_age)
        if not isinstance(claims, dict) or not all(
            isinstance(claims.get(key), str) and claims[key]
            for key in ("email", "credential_version", "nonce")
        ):
            return None
        return claims
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# API key generation and hashing
# ---------------------------------------------------------------------------


def generate_api_key(
    environment: str = "live",
) -> tuple[str, str, str]:
    """Generate a new API key with Stripe-style prefix.

    Args:
        environment: "live" or "test".

    Returns:
        Tuple of (plaintext_key, key_hash, key_prefix).
        The plaintext must be displayed once and never stored.
    """
    token = secrets.token_urlsafe(32)
    plaintext = f"dc_{environment}_{token}"
    key_hash = hash_api_key(plaintext)
    # Visible prefix: dc_live_ + first 8 chars of token + ...
    key_prefix = f"dc_{environment}_{token[:8]}..."
    return plaintext, key_hash, key_prefix


def hash_api_key(key: str) -> str:
    """Hash an API key using HMAC-SHA256 keyed with the server secret.

    Using HMAC instead of plain SHA-256 means a database leak alone
    is not enough to recover keys — the attacker also needs the server
    secret.

    Args:
        key: The plaintext API key.

    Returns:
        Hex-encoded HMAC-SHA256 digest (64 characters).
    """
    import hmac

    # SEC-03: Use dedicated HMAC secret so JWT rotation doesn't invalidate API keys.
    # Falls back to jwt_secret_key for backward compatibility when not yet configured.
    secret = settings.api_key_hmac_secret or settings.jwt_secret_key
    return hmac.new(
        secret.encode(),
        key.encode(),
        hashlib.sha256,
    ).hexdigest()


def hash_api_key_legacy(key: str) -> str:
    """Legacy SHA-256 hash (no HMAC). Used for migration fallback only."""
    return hashlib.sha256(key.encode()).hexdigest()
