"""Request/response schemas for auth endpoints."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, EmailStr, Field

# State machine values for api_keys.status column (migration 064).
# Used in insert_user_api_key(status=...) and update_api_key_status().
ApiKeyStatus = Literal["pending", "active", "revoked"]

# Normalize emails to lowercase at the schema boundary — emails are
# case-insensitive in practice and both auth lookups and billing webhooks
# assume a canonical lowercased form.
NormalizedEmail = Annotated[EmailStr, AfterValidator(lambda v: v.lower())]


class RegisterRequest(BaseModel):
    """New developer account registration."""

    model_config = {
        "json_schema_extra": {
            "examples": [{"email": "dev@example.com", "password": "monmotdepasse"}]
        }
    }

    email: NormalizedEmail
    password: str = Field(max_length=72)  # bcrypt truncates at 72 bytes


class LoginRequest(BaseModel):
    """Email + password login."""

    email: NormalizedEmail
    password: str = Field(max_length=72)


class TokenResponse(BaseModel):
    """JWT access token response."""

    access_token: str
    token_type: str = "bearer"


class ProfileUpdate(BaseModel):
    """Optional profile fields update."""

    name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)


class PasswordChange(BaseModel):
    """Password change (requires current password)."""

    current_password: str = Field(max_length=72)
    new_password: str = Field(max_length=72)


class ForgotPasswordRequest(BaseModel):
    """Request a password reset email."""

    email: NormalizedEmail


class ResetPasswordRequest(BaseModel):
    """Reset password with a signed token."""

    token: str
    new_password: str = Field(max_length=72)


class ResendVerificationRequest(BaseModel):
    """Request a fresh verification email (anti-enumeration endpoint)."""

    email: NormalizedEmail


class QuickKeyRequest(BaseModel):
    """Request an API key by email (no password, no account)."""

    email: NormalizedEmail


class CreateKeyRequest(BaseModel):
    """Create a new API key."""

    name: str = Field(min_length=3, max_length=100)
    environment: Literal["live", "test"] = "live"
