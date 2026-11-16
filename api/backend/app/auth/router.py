"""Auth API router: register, login, verify, reset, profile, account.

Pure JSON API — no HTML redirects, no CSRF form validation.
"""

import logging
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.dependencies import get_current_user
from app.auth.email import send_api_key_email, send_reset_email, send_verification_email
from app.auth.models import UserInfo
from app.auth.queries import (
    count_user_active_keys,
    create_user,
    delete_user,
    get_account_usage,
    get_default_key_for_user,
    get_user_by_email,
    insert_default_api_key,
    insert_user_api_key,
    key_name_exists,
    list_user_keys,
    revoke_user_key,
    update_api_key_status,
    update_user_password,
    update_user_profile,
    verify_user_email,
)
from app.auth.schemas import (
    CreateKeyRequest,
    ForgotPasswordRequest,
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    QuickKeyRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.auth.service import (
    create_access_token,
    create_reset_token,
    create_verification_token,
    credential_version,
    generate_api_key,
    hash_password,
    session_matches_credential,
    verify_email_token,
    verify_password,
    verify_reset_token,
)
from app.billing.plans import (
    get_max_keys_for_plan,
    get_quota_for_plan,
    is_internal_unlimited_plan,
)
from app.billing.queries import get_active_subscription
from app.common.async_helpers import fire_and_forget
from app.common.exceptions import ErreurApiError
from app.config import settings
from app.database import async_session, get_db
from app.middleware import _get_client_ip
from app.rate_limit import _check_rate, limite_authentification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# C45: the routes that check a password or a token, or that start an account
# or a key, share 10 requests a minute per IP (app.rate_limit). Not on the
# session routes (/me, /logout) nor on the profile, keys and account routes.
_LIMITE_AUTH = [Depends(limite_authentification)]


async def inscription_ouverte() -> None:
    """The account-creation routes do not exist while registration is closed (lot 5)."""
    if not settings.registration_enabled:
        raise ErreurApiError(404, "Not Found")


# Closed registration answers before the rate limit, the body and the database.
_INSCRIPTION = [Depends(inscription_ouverte), *_LIMITE_AUTH]

# Anti-enumeration message (always returned, whether email exists or not)
_REGISTER_MSG = (
    "Si cet email n'est pas deja utilise, un lien de verification a ete envoye."
)
_REGISTER_EMAIL_DISABLED_MSG = (
    "Si cet email n'est pas deja utilise, le compte est cree et peut se connecter."
)
_FORGOT_MSG = (
    "Si un compte existe avec cet email, un lien de reinitialisation a ete envoye."
)

_MIN_PASSWORD_LEN = 8

# Constant-time login guard: a bcrypt hash computed once at import time so the
# login handler can always run verify_password, even on non-existent users, to
# avoid leaking account existence via response timing. The plaintext is never
# used — verify_password is guaranteed to return False.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def _validate_password(password: str) -> None:
    """Raise 400 if password is too short."""
    if len(password) < _MIN_PASSWORD_LEN:
        raise ErreurApiError(
            400,
            "Mot de passe trop court",
            f"Minimum {_MIN_PASSWORD_LEN} caracteres",
        )


def _email_verification_disabled() -> bool:
    """Return True when outbound email is explicitly disabled."""
    return (
        not settings.email_enabled or settings.resend_api_key == "resend-disabled-demo"
    )


def _auto_verify_without_email() -> bool:
    """Allow email-free test accounts only inside the isolated preproduction app."""
    return (
        settings.app_environment == "preproduction" and _email_verification_disabled()
    )


async def _get_self_service_subscription(
    db: AsyncSession,
    user_id: int,
) -> dict | None:
    """Return the active subscription unless the account is ops-managed."""
    subscription = await get_active_subscription(db, user_id)
    if subscription and is_internal_unlimited_plan(subscription["plan_name"]):
        raise ErreurApiError(
            403,
            "Compte interne protégé",
            "Ce compte se gère uniquement via le provisioner sécurisé.",
        )
    return subscription


async def _provision_verified_account(
    db: AsyncSession,
    email: str,
    user_id: int,
) -> None:
    """Verify a freshly created account and attach its default API key."""
    updated = await verify_user_email(db, email)
    if not updated:
        return

    existing = await get_default_key_for_user(db, user_id)
    if existing is None:
        _plain, key_hash, key_prefix = generate_api_key("live")
        await insert_default_api_key(db, key_hash, user_id, key_prefix)


# ---------------------------------------------------------------------------
# Registration + email verification
# ---------------------------------------------------------------------------

# Self-service verification resend: per-email cooldown (in-memory, best-effort)
_VERIF_RESEND_COOLDOWN: dict[str, float] = {}  # {email: timestamp}
_VERIF_RESEND_COOLDOWN_SECONDS = 300  # 5 minutes


def _purge_stale_verif_cooldowns(now: float) -> None:
    stale = [k for k, v in _VERIF_RESEND_COOLDOWN.items() if now - v > 7200]
    for k in stale:
        _VERIF_RESEND_COOLDOWN.pop(k, None)


def _resend_verification_throttled(email: str) -> None:
    """Fire a fresh verification email unless one was sent recently.

    Called when an existing unverified account retries register or login —
    without this, anti-enumeration silently strands users whose original
    email was never delivered.
    """
    now = time.monotonic()
    _purge_stale_verif_cooldowns(now)
    last = _VERIF_RESEND_COOLDOWN.get(email)
    if last is not None and now - last < _VERIF_RESEND_COOLDOWN_SECONDS:
        return
    _VERIF_RESEND_COOLDOWN[email] = now
    token = create_verification_token(email)
    fire_and_forget(
        send_verification_email(email, token),
        task_name="send_verification_email",
    )


@router.post("/register", status_code=202, dependencies=_INSCRIPTION)
async def register(
    body: RegisterRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Register a new account. Always returns 202 (anti-enumeration)."""
    # Per-route IP rate limit (same pattern as /auth/quick-key): this route
    # creates accounts and can trigger outbound email, so cap it well below
    # the global IP ceiling (AB-01: email bombing + Resend costs).
    #
    # Deliberately per-MINUTE and not per-hour, unlike /auth/forgot-password below.
    # An IP is a poor proxy for a person here: a company, a campus or a VPN exit
    # shares one address, and an hourly cap would lock out legitimate signups for
    # everyone behind it. Bombing a third party's inbox is not possible from this
    # route anyway — registering only mails the address being registered.
    client_ip = _get_client_ip(request)
    allowed, _, _ = _check_rate(f"register:ip:{client_ip}", 5, window_seconds=60)
    if not allowed:
        raise ErreurApiError(
            429, "Trop de demandes", "Reessayez dans quelques minutes."
        )

    reg_email = body.email
    reg_password = body.password

    _validate_password(reg_password)

    existing = await get_user_by_email(db, reg_email)
    if existing is None:
        pw_hash = hash_password(reg_password)
        user_id = await create_user(db, reg_email, pw_hash)
        if _auto_verify_without_email():
            await _provision_verified_account(db, reg_email, user_id)
        elif not _email_verification_disabled():
            token = create_verification_token(reg_email)
            fire_and_forget(
                send_verification_email(reg_email, token),
                task_name="send_verification_email",
            )
    elif not existing["is_verified"] and not _email_verification_disabled():
        # Existing unverified account retrying: resend the link.
        # Response stays identical (202) so nothing leaks.
        _resend_verification_throttled(reg_email)

    return JSONResponse(
        content={
            "message": (
                _REGISTER_EMAIL_DISABLED_MSG
                if _auto_verify_without_email()
                else _REGISTER_MSG
            ),
            "requires_verification": not _auto_verify_without_email(),
        },
        status_code=202,
    )


@router.get("/verify", dependencies=_LIMITE_AUTH)
async def verify_email(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Verify email address via signed token link."""
    email = verify_email_token(token)
    if email is None:
        raise ErreurApiError(
            400,
            "Lien invalide ou expire",
            "Demandez un nouveau lien de verification.",
        )

    updated = await verify_user_email(db, email)

    # Auto-create default API key for newly verified users
    if updated:
        user = await get_user_by_email(db, email)
        if user is not None:
            existing = await get_default_key_for_user(db, user["id"])
            if existing is None:
                _plain, key_hash, key_prefix = generate_api_key("live")
                await insert_default_api_key(db, key_hash, user["id"], key_prefix)

    return JSONResponse(
        content={"message": "Email verifie avec succes"},
    )


# Anti-enumeration message: identical whether the account exists, is already
# verified, or the per-email cooldown is active.
_RESEND_VERIFICATION_MSG = (
    "Si un compte non verifie existe avec cet email, "
    "un lien de verification a ete envoye."
)


@router.post("/resend-verification", status_code=202, dependencies=_INSCRIPTION)
async def resend_verification(
    body: ResendVerificationRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Resend the verification email. Always returns 202 (anti-enumeration).

    Self-service path for users who lost the original email and do not want
    to retry register/login. The response body and status code never depend
    on account state — nothing leaks. The global per-IP rate-limit middleware
    also covers this route (it is not in the skip list).
    """
    email = body.email

    # Per-route IP rate limit (same pattern as /auth/quick-key): this route
    # can trigger outbound email, so cap it well below the global IP ceiling.
    client_ip = _get_client_ip(request)
    allowed, _, _ = _check_rate(f"resendverif:ip:{client_ip}", 5)
    if not allowed:
        raise ErreurApiError(
            429, "Trop de demandes", "Reessayez dans quelques minutes."
        )

    user = await get_user_by_email(db, email)
    if (
        user is not None
        and not user["is_verified"]
        # Sentinel hashes ("!...") mark quick-key / locked accounts that must
        # never receive a self-service verification link.
        and not user["password_hash"].startswith("!")
        and not _email_verification_disabled()
    ):
        # Reuse the shared per-email cooldown; inside the window this is a
        # silent no-op so the response stays indistinguishable.
        _resend_verification_throttled(email)

    return JSONResponse(
        content={"message": _RESEND_VERIFICATION_MSG},
        status_code=202,
    )


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------


@router.post("/login", dependencies=_LIMITE_AUTH)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Authenticate with email + password. Returns JWT in HttpOnly cookie."""
    email = body.email
    password = body.password

    user = await get_user_by_email(db, email)
    # Run verify_password unconditionally so response timing cannot distinguish
    # "user does not exist" from "wrong password" — bcrypt dominates the wall-clock.
    candidate_hash = (
        user["password_hash"]
        if user is not None and not user["password_hash"].startswith("!")
        else _DUMMY_PASSWORD_HASH
    )
    password_ok = verify_password(password, candidate_hash)
    if user is None or user["password_hash"].startswith("!") or not password_ok:
        raise ErreurApiError(401, "Email ou mot de passe incorrect")

    if not user["is_verified"]:
        # Correct password proves account ownership: resend the link so the
        # user can self-recover if the original email was never delivered.
        if _email_verification_disabled():
            raise ErreurApiError(403, "Email non verifie")
        _resend_verification_throttled(email)
        raise ErreurApiError(
            403,
            "Email non verifie",
            "Un nouveau lien de verification vient de vous etre envoye par email.",
        )

    # Track last successful login — explicit POST /auth/login only, not /me or refresh
    await db.execute(
        text("UPDATE users SET last_login = NOW() WHERE id = :uid"),
        {"uid": user["id"]},
    )
    await db.commit()

    token = create_access_token(
        {
            "sub": str(user["id"]),
            "credential_version": credential_version(user["password_hash"]),
        }
    )
    max_age = settings.jwt_expire_minutes * 60

    response = JSONResponse(
        content=TokenResponse(access_token=token).model_dump(),
    )
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=f"Bearer {token}",
        httponly=True,
        samesite="lax",
        secure=True,
        max_age=max_age,
        path="/",
        domain=settings.auth_cookie_domain if not settings.debug else None,
    )
    return response


@router.post("/logout")
async def logout() -> JSONResponse:
    """Clear JWT cookie."""
    response = JSONResponse(content={"message": "Deconnecte"})
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        domain=settings.auth_cookie_domain if not settings.debug else None,
    )
    return response


# ---------------------------------------------------------------------------
# Current user profile
# ---------------------------------------------------------------------------


@router.get("/me")
async def me(
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Return authenticated user profile with current subscription plan."""
    sub = await get_active_subscription(db, current_user.id)
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "company": current_user.company,
        "is_verified": current_user.is_verified,
        "is_admin": current_user.is_admin,
        "created_at": current_user.created_at.isoformat(),
        "plan": sub["plan_name"] if sub else "free",
    }


# ---------------------------------------------------------------------------
# Password reset flow
# ---------------------------------------------------------------------------


@router.post("/forgot-password", status_code=202, dependencies=_LIMITE_AUTH)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Request a password reset email (always 202, anti-enumeration)."""
    # Per-route IP rate limit (same pattern as /auth/quick-key): this route
    # can trigger outbound email, so cap it well below the global IP ceiling
    # (AB-01: email bombing + Resend costs).
    #
    # Per-HOUR here, where /auth/register is per-minute. This is the route that can
    # mail a THIRD PARTY on demand, so it is the one an attacker uses to bomb an
    # inbox; 5/min allowed 300 messages an hour against a single victim. Nobody
    # legitimately requests five password resets in an hour, so the tighter window
    # costs no real user anything.
    client_ip = _get_client_ip(request)
    allowed, _, _ = _check_rate(f"forgotpw:ip:{client_ip}", 5, window_seconds=3600)
    if not allowed:
        raise ErreurApiError(
            429, "Trop de demandes", "Reessayez dans quelques minutes."
        )

    fp_email = body.email

    user = await get_user_by_email(db, fp_email)
    if user and user["is_verified"]:
        token = create_reset_token(fp_email, user["password_hash"])
        fire_and_forget(
            send_reset_email(fp_email, token),
            task_name="send_reset_email",
        )

    return JSONResponse(
        content={"message": _FORGOT_MSG},
        status_code=202,
    )


@router.post("/reset-password", dependencies=_LIMITE_AUTH)
async def reset_password(
    body: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Reset password using a signed token from the reset email."""
    rp_token = body.token
    rp_password = body.new_password

    _validate_password(rp_password)

    claims = verify_reset_token(rp_token)
    if claims is None:
        raise ErreurApiError(400, "Lien invalide ou expire")

    user = await get_user_by_email(db, claims["email"])
    if (
        user is None
        or not user["is_verified"]
        or not session_matches_credential(claims, user)
    ):
        raise ErreurApiError(400, "Lien invalide ou expire")

    await _get_self_service_subscription(db, user["id"])
    pw_hash = hash_password(rp_password)
    if not await update_user_password(db, user["id"], pw_hash, user["password_hash"]):
        raise ErreurApiError(400, "Lien invalide ou expire")

    return JSONResponse(
        content={"message": "Mot de passe mis a jour"},
    )


# ---------------------------------------------------------------------------
# Profile management (authenticated)
# ---------------------------------------------------------------------------


@router.put("/profile")
async def update_profile(
    body: ProfileUpdate,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update name and/or company for authenticated user."""
    await _get_self_service_subscription(db, current_user.id)
    await update_user_profile(
        db,
        current_user.id,
        body.name,
        body.company,
    )
    return {"message": "Profil mis a jour"}


@router.put("/password", dependencies=_LIMITE_AUTH)
async def change_password(
    body: PasswordChange,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Change password (requires current password verification)."""
    await _get_self_service_subscription(db, current_user.id)
    _validate_password(body.new_password)

    user = await get_user_by_email(db, current_user.email)
    if user is None or not verify_password(
        body.current_password,
        user["password_hash"],
    ):
        raise ErreurApiError(400, "Mot de passe actuel incorrect")

    pw_hash = hash_password(body.new_password)
    if not await update_user_password(
        db, current_user.id, pw_hash, user["password_hash"]
    ):
        raise ErreurApiError(400, "Mot de passe modifié entre-temps, reconnectez-vous")

    return {"message": "Mot de passe mis a jour"}


# ---------------------------------------------------------------------------
# Quick API key (email-only, no password)
# ---------------------------------------------------------------------------

_QUICKKEY_MSG = "Si cet email est valide, votre cle API a ete envoyee."
_QUICKKEY_COOLDOWN: dict[str, float] = {}  # {email: timestamp}
_QUICKKEY_PENDING: set[str] = set()
_QUICKKEY_COOLDOWN_SECONDS = 3600  # 1 request per email per hour
_QUICKKEY_MAX_KEYS = 3  # max active keys per quick-key user
_QUICKKEY_QUOTA = 500  # Free plan


def _purge_stale_quickkey_cooldowns(now: float) -> None:
    stale = [k for k, v in _QUICKKEY_COOLDOWN.items() if now - v > 7200]
    for k in stale:
        _QUICKKEY_COOLDOWN.pop(k, None)


async def _deliver_quick_key(email: str) -> None:
    """Issue, email, and finalize a quick-key using DB-backed state machine.

    State transitions (migration 064):
      insert(status='pending') -> commit (durable row)
      -> attempt email send
        -> success: UPDATE status='active' + commit
        -> failure: UPDATE status='revoked' + commit (no ghost active key)

    The endpoint always returns 202 — callers never see email send failures.
    In-memory _QUICKKEY_COOLDOWN is only updated on success so a failed email
    does not block a future retry within the cooldown window.
    """
    key_id: int | None = None
    user_id: int | None = None

    try:
        async with async_session() as db:
            user = await get_user_by_email(db, email)

            if user is not None and not user["password_hash"].startswith("!"):
                return

            if user is not None:
                active = await count_user_active_keys(db, user["id"])
                if active >= _QUICKKEY_MAX_KEYS:
                    return
                user_id = user["id"]
            else:
                sentinel = f"!quickkey:{secrets.token_urlsafe(32)}"
                user_id = await create_user(db, email, sentinel)

            plaintext, key_hash, key_prefix = generate_api_key("live")
            # 1. Insert as pending — durable row before email attempt
            key_id = await insert_user_api_key(
                db,
                key_hash,
                "Quick Key",
                _QUICKKEY_QUOTA,
                user_id,
                key_prefix,
                "live",
                status="pending",
            )
            # insert_user_api_key calls db.commit() internally — row is durable

            # 2. Attempt email send
            try:
                await send_api_key_email(email, plaintext)
            except Exception as exc:  # noqa: BLE001
                # 3a. Email failure: mark revoked (no ghost active key)
                await update_api_key_status(db, key_id, "revoked")
                await db.commit()
                logger.warning(
                    "quickkey_email_failed",
                    extra={"key_id": key_id, "error": str(exc)},
                )
                # Do NOT raise — endpoint returns 202 regardless
                return

            # 3b. Email sent successfully: the address is proven, the key goes live.
            # Verified only now, so an account whose mail never left stays
            # unverified and falls to the purge of never-verified accounts.
            await verify_user_email(db, email)
            await update_api_key_status(db, key_id, "active")
            await db.commit()
            logger.info("quickkey_activated", extra={"key_id": key_id})
            _QUICKKEY_COOLDOWN[email] = time.monotonic()

    except Exception:
        logger.exception("Quick-key delivery failed for %s", email)
    finally:
        _QUICKKEY_PENDING.discard(email)
        _purge_stale_quickkey_cooldowns(time.monotonic())


@router.post("/quick-key", status_code=202, dependencies=_INSCRIPTION)
async def quick_key(
    body: QuickKeyRequest,
    request: Request,
) -> JSONResponse:
    """Send an API key by email. No password, no account required."""
    email = body.email.lower()

    # Rate limit: 5 requests per hour per IP
    client_ip = _get_client_ip(request)
    allowed, _, _ = _check_rate(f"quickkey:ip:{client_ip}", 5)
    if not allowed:
        raise ErreurApiError(
            429, "Trop de demandes", "Reessayez dans quelques minutes."
        )

    # Per-email cooldown (in-memory, best-effort)
    now = time.monotonic()
    _purge_stale_quickkey_cooldowns(now)
    # None sentinel: time.monotonic() starts near 0 after a host reboot, so a
    # 0.0 default would silently swallow every request for the first hour
    last = _QUICKKEY_COOLDOWN.get(email)
    if email in _QUICKKEY_PENDING or (
        last is not None and now - last < _QUICKKEY_COOLDOWN_SECONDS
    ):
        return JSONResponse(content={"message": _QUICKKEY_MSG}, status_code=202)

    _QUICKKEY_PENDING.add(email)
    try:
        fire_and_forget(_deliver_quick_key(email), task_name="deliver_quick_key")
    except Exception:
        _QUICKKEY_PENDING.discard(email)
        raise

    return JSONResponse(content={"message": _QUICKKEY_MSG}, status_code=202)


# ---------------------------------------------------------------------------
# API key management (authenticated)
# ---------------------------------------------------------------------------


@router.get("/keys")
async def list_keys(
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """List user-created API keys with account-level quota info."""
    keys = await list_user_keys(db, current_user.id)
    sub = await get_active_subscription(db, current_user.id)
    plan_name = sub["plan_name"] if sub else "free"
    account_quota = get_quota_for_plan(plan_name)
    account_usage = await get_account_usage(db, current_user.id)
    return {
        "keys": keys,
        "account_quota": account_quota,
        "account_usage": account_usage,
    }


@router.post("/keys", status_code=201)
async def create_key(
    body: CreateKeyRequest,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new API key. Returns the plaintext key (shown once only)."""
    sub = await _get_self_service_subscription(db, current_user.id)
    plan_name = sub["plan_name"] if sub else "free"
    max_keys = get_max_keys_for_plan(plan_name)

    active = await count_user_active_keys(db, current_user.id)
    if active >= max_keys:
        raise ErreurApiError(
            400,
            "Limite atteinte",
            f"Maximum {max_keys} cles actives (plan {plan_name}).",
        )

    if await key_name_exists(db, current_user.id, body.name):
        raise ErreurApiError(
            400,
            "Nom deja utilise",
            "Chaque cle doit avoir un nom unique.",
        )

    plan_quota = get_quota_for_plan(plan_name)
    plaintext, key_hash, key_prefix = generate_api_key(body.environment)
    key_id = await insert_user_api_key(
        db,
        key_hash,
        body.name,
        plan_quota,
        current_user.id,
        key_prefix,
        body.environment,
    )

    return {
        "id": key_id,
        "name": body.name,
        "key": plaintext,
        "key_prefix": key_prefix,
        "message": "Conservez cette cle, elle ne sera plus affichee.",
    }


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: int,
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Revoke an API key (soft delete)."""
    await _get_self_service_subscription(db, current_user.id)
    revoked = await revoke_user_key(db, key_id, current_user.id)
    if not revoked:
        raise ErreurApiError(404, "Cle non trouvee ou deja revoquee")
    return {"message": "Cle revoquee"}


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------


@router.delete("/account")
async def delete_account(
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    confirm: Annotated[bool, Query()] = False,
) -> Response:
    """Delete account and all API keys (requires ?confirm=true)."""
    if not confirm:
        raise ErreurApiError(
            400,
            "Confirmation requise",
            "Ajoutez ?confirm=true pour confirmer la suppression.",
        )

    await _get_self_service_subscription(db, current_user.id)
    await delete_user(db, current_user.id)

    response = JSONResponse(
        content={"message": "Compte supprime"},
        status_code=200,
    )
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        domain=settings.auth_cookie_domain if not settings.debug else None,
    )
    return response
