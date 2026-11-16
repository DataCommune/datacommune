"""Pydantic schemas for API key and user authentication."""

from datetime import datetime

from pydantic import BaseModel


class ApiKeyInfo(BaseModel):
    """Information about an API key (excludes the hash)."""

    id: int
    name: str
    quota_monthly: int
    revoked: bool
    created_at: datetime
    user_id: int | None = None  # Nullable for CLI-created keys (migration 022)
    # State machine column (migration 064): pending | active | revoked
    status: str = "active"
    # Visible prefix stored at creation (migration 022), e.g. "dc_anon_LbOh...".
    key_prefix: str | None = None


class UserInfo(BaseModel):
    """Developer account information."""

    id: int
    email: str
    name: str | None
    company: str | None
    is_verified: bool
    is_admin: bool = False
    created_at: datetime
    updated_at: datetime
