"""Plan definitions: quotas, price IDs, rate limits."""

from app.config import settings

INTERNAL_PLAN_NAME = "internal"
UNLIMITED_QUOTA = -1
INTERNAL_MAX_KEYS = 0

# Plan name -> {quota_monthly, price_id_monthly, price_id_annual, display_name, ...}
# price_id fields are empty for free (no Stripe product)
PLANS: dict[str, dict] = {
    "free": {
        "quota_monthly": 500,
        "price_id_monthly": "",
        "price_id_annual": "",
        "display_name": "Découverte",
        "price_eur_monthly": 0,
        "price_eur_annual": 0,
        "rate_limit": 10,  # req/min
        "max_keys": 2,
    },
    "pro": {
        "quota_monthly": 5_000,
        "price_id_monthly": "",  # set from settings at runtime
        "price_id_annual": "",  # set from settings at runtime
        "display_name": "Pro",
        "price_eur_monthly": 39,
        "price_eur_annual": 374,  # ~31€/month, -20%
        "rate_limit": 60,  # req/min
        "max_keys": 5,
    },
    "business": {
        "quota_monthly": 25_000,
        "price_id_monthly": "",  # set from settings at runtime
        "price_id_annual": "",  # set from settings at runtime
        "display_name": "Business",
        "price_eur_monthly": 99,
        "price_eur_annual": 950,  # ~79€/month, -20%
        "rate_limit": 200,  # req/min
        "max_keys": 10,
    },
    "enterprise": {
        "quota_monthly": 100_000,
        "price_id_monthly": "",  # set from settings at runtime
        "price_id_annual": "",  # set from settings at runtime
        "display_name": "Enterprise",
        "price_eur_monthly": 249,
        "price_eur_annual": 2_390,  # ~199€/month, -20%
        "rate_limit": 500,  # req/min
        "max_keys": 20,
    },
}


def get_plans() -> dict[str, dict]:
    """Return plans with price_ids resolved from settings."""
    plans = {k: {**v} for k, v in PLANS.items()}
    plans["pro"]["price_id_monthly"] = settings.stripe_price_pro_monthly
    plans["pro"]["price_id_annual"] = settings.stripe_price_pro_annual
    plans["business"]["price_id_monthly"] = settings.stripe_price_business_monthly
    plans["business"]["price_id_annual"] = settings.stripe_price_business_annual
    plans["enterprise"]["price_id_monthly"] = settings.stripe_price_enterprise_monthly
    plans["enterprise"]["price_id_annual"] = settings.stripe_price_enterprise_annual
    return plans


def get_plan_by_price_id(price_id: str) -> tuple[str, dict] | None:
    """Find a plan by its Stripe price_id. Returns (name, plan) or None."""
    for name, plan in get_plans().items():
        for key in ("price_id_monthly", "price_id_annual"):
            if plan[key] and plan[key] == price_id:
                return name, plan
    return None


def get_quota_for_plan(plan_name: str) -> int:
    """Get monthly quota for a plan name. Defaults to free quota."""
    if plan_name == INTERNAL_PLAN_NAME:
        return UNLIMITED_QUOTA
    return PLANS.get(plan_name, PLANS["free"])["quota_monthly"]


def get_rate_limit_for_plan(plan_name: str) -> int:
    """Get rate limit (req/min) for a plan name. Defaults to free rate limit."""
    return PLANS.get(plan_name, PLANS["free"])["rate_limit"]


def get_rate_limit_for_quota(quota_monthly: int) -> int:
    """Derive rate limit from quota_monthly. Matches the highest plan that fits."""
    # Sorted descending so we match the highest applicable plan
    for plan in sorted(PLANS.values(), key=lambda p: p["quota_monthly"], reverse=True):
        if quota_monthly >= plan["quota_monthly"]:
            return plan["rate_limit"]
    return PLANS["free"]["rate_limit"]


def get_max_keys_for_plan(plan_name: str) -> int:
    """Get max active API keys allowed for a plan name. Defaults to free."""
    if plan_name == INTERNAL_PLAN_NAME:
        return INTERNAL_MAX_KEYS
    return PLANS.get(plan_name, PLANS["free"])["max_keys"]


def is_internal_unlimited_plan(plan_name: str | None) -> bool:
    """Return whether a plan is the non-public internal service plan."""
    return plan_name == INTERNAL_PLAN_NAME
