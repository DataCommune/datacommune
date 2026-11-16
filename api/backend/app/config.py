import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


def _promote_file_secrets(names: frozenset[str]) -> None:
    """Docker-style `*_FILE` convention: read secrets from mounted files.

    For an env var named `FOO_FILE` pointing to a readable file, load the
    file's stripped contents into `FOO` unless `FOO` is already set. This
    keeps secrets out of `docker inspect` output while staying compatible
    with plain env-var configuration in dev.

    Only `FOO` among *names* (the settings of this application): any other
    `*_FILE` (`SSL_CERT_FILE`, `GIT_SSL_CAINFO`... a CA bundle of the host)
    is a path for another tool, never read into the environment, where a
    variable over 128 KiB makes every subprocess launch fail (E2BIG).
    """
    for key, value in list(os.environ.items()):
        if not key.endswith("_FILE") or not value:
            continue
        target = key[: -len("_FILE")]
        if target not in names or os.environ.get(target):
            continue
        path = Path(value)
        if not path.is_file():
            continue
        try:
            os.environ[target] = path.read_text(encoding="utf-8").strip()
        except OSError:
            # Silently skip unreadable files so a missing optional secret
            # does not crash the whole app at import time.
            continue


class Settings(BaseSettings):
    # Deployment identity is deliberately non-secret and exposed by /v1/health.
    app_environment: str = "production"
    deployment_sha: str = "unknown"
    database_url: str = (
        "postgresql+asyncpg://datacommune:devpassword@localhost:5432/datacommune"
    )
    debug: bool = False
    default_quota: int = 500  # matches free plan
    # Explicit rollout gate. The policy is an independently reviewed JSON
    # document; an empty/invalid value fails closed only when this is enabled.
    commune_indicators_enabled: bool = False
    commune_indicator_policy: str = ""

    # -- ETL settings ----------------------------------------------------------
    etl_temp_dir: str = str(tempfile.gettempdir())
    etl_statement_timeout_s: int = 7200  # 2h max for INSERT SELECT + indexes
    # NULL scans of a published table (required pass, all columns at once, and
    # expected pass): 8.9 s measured on the 16.7 M national DPE documents
    # (2026-10-30); the expected scan of the 94.8 M national parcelles did not
    # fit in the former fixed 8 s (cadastre run 122, 2026-10-31).
    etl_validation_scan_timeout_s: int = 900
    etl_max_retries: int = 3
    etl_download_concurrency: int = 10  # max parallel HTTP requests
    # DPE (plan 04.2-03): (source, departement) pairs downloaded at once. 5 is
    # the fixed value that ran in production before this setting (44/85).
    dpe_download_concurrency: int = Field(default=5, ge=1, le=8)
    # DPE (plan 04.2-03): complete (source, departement) pairs of an
    # unpublished previous import are reused while younger than this; 0 = never.
    dpe_download_reuse_max_age_hours: float = Field(default=72.0, ge=0)
    etl_min_disk_gb: int = 10  # abort pipeline if free disk < this
    # Source archive (plan 02-14, D-29/D-30 amended): root holding the sentinel
    # .datacommune-source-archive. Empty = no archive step (default, local
    # tests). On the work base: the USB disk of the hypervisor, never the SSD.
    source_archive_dir: str = ""
    # Free bytes the archive must leave on its disk after writing a run; the
    # value is measured on the target disk (docker-compose.workbase.yml).
    source_archive_min_free_bytes: int = 0
    etl_min_success_ratio: float = 0.9  # minimum dept success ratio before swap
    etl_file_max_retries: int = 3  # file-level retry attempts (download+verify)
    etl_max_row_drop_pct: float = 0.20  # abort swap if rows drop by >20%
    # P8-01/B-02/LC-01/A-01: size caps against disk exhaustion (the ETL disk
    # also hosts the PostgreSQL data directory). Largest legit archives are
    # ~1-2 GB compressed (BD TOPO dept .7z, cadastre .gz) and up to ~4 GB
    # decompressed (GPKG/GeoJSON) — caps keep 2-3x headroom above that.
    etl_max_download_bytes: int = 5 * 1024**3  # 5 GiB per downloaded file
    # Per ZIP member AND cumulative uncompressed budget per archive.
    etl_max_decompressed_bytes: int = 10 * 1024**3  # 10 GiB
    # Anti-zip-bomb: reject when declared uncompressed size exceeds this
    # multiple of the compressed size. Legit geo data compresses ~10-30x;
    # bombs reach 1000x+.
    etl_max_compression_ratio: int = 1000
    etl_schedule_grace_days: int = 7  # days after missed schedule before stale
    # SWAP-02: 1h rollback window is sufficient for overnight ETL.
    # The scheduler runs cleanup_old_tables hourly, so effective retention is 1-2h.
    etl_old_table_ttl_hours: int = 1
    # GATE-B': fallback GATE-01 floor applied when a pipeline defines no
    # min_staging_rows of its own (BasePipeline default). Scaled by
    # scoped_min_rows() to the departements of the hosted scope, so scoped
    # runs are not blocked. Closes the "quasi-empty staging swap" hole:
    # the 50%-safety and regression checks only fire when production already
    # has rows.
    etl_min_staging_rows: int = 1000

    # -- Validation settings ---------------------------------------------------
    validation_null_rate_threshold: float = 0.80  # flag columns with >80% nulls

    # -- Schema drift gate (ADR-0003 D2) ---------------------------------------
    # Boot fails fast if the DB is behind the code's Alembic head. Set true ONLY
    # for exceptional ops (rollback, maintenance); never in a compose file.
    allow_schema_drift: bool = False

    # -- Database pool ----------------------------------------------------------
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 3600  # seconds — recycle stale connections

    # -- Alerting ---------------------------------------------------------------
    alert_urls: list[str] = []  # apprise URLs (e.g. tgram://bottoken/chatid)
    alert_cooldown_minutes: int = 60  # dedup alerts for same dataset within window

    # -- Jobs -------------------------------------------------------------------
    log_format: str = "text"  # "text" (dev) or "json" (production)
    disk_alert_threshold_pct: float = 10.0  # disk free % below which to alert
    dq_out_dir: str = "/tmp/audit-dq"  # override via DQ_OUT_DIR env (container-safe)

    # -- Proxy client settings --------------------------------------------------
    proxy_read_timeout_s: float = 15.0
    proxy_connect_timeout_s: float = 5.0
    proxy_urbanisme_read_timeout_s: float = 30.0  # API Carto is slow
    proxy_cache_ttl_s: int = 86400  # 24h TTL for upstream proxy cache
    # Read at start the upstream files the address pages need whole (the
    # communal SSMSI file). Off in the tests: no download at boot.
    prechauffage_amonts: bool = True

    # -- Public access (CORS + rate limiting) -----------------------------------
    # Restrict to DataCommune domains. Local dev MUST set CORS_ORIGINS via env var.
    # SEC-06: localhost removed from defaults — explicit opt-in required in dev.
    cors_origins: list[str] = [
        "https://datacommune.fr",
        "https://www.datacommune.fr",
        "https://api.datacommune.fr",
    ]
    trust_proxy_headers: bool = False
    # Shared req/min ceiling, including ordinary API keys. 120 = C36 (Ianis,
    # 2026-10-10): 30 cut off a visitor browsing two addresses. Measured by
    # 261010-kcq: a first load of an address page costs 5 reads (6 since the
    # declared sales of C37), but every device behind one relay shares the IP.
    rate_limit_per_ip: int = 120
    # Requests per minute and per IP on the exposed authentication routes,
    # one bucket shared by all of them and kept apart from the ceiling above:
    # 10 = C45 (Ianis, 2026-10-10, « Oui, 10/min sur la connexion »). Each
    # login attempt costs a bcrypt; 120 a minute of navigation must not mean
    # 120 password guesses (WR-01 of 04-REVIEW-D15-RECETTE-LOTS.md).
    rate_limit_auth_per_ip: int = 10
    # Map proxy of the address report (04.7-17, ``app/carte``): the IGN WMS the
    # server queries on behalf of the visitor, so the visitor's address never
    # reaches it. Per-minute bucket of one IP on the route, and one global
    # bucket on the calls actually made to the IGN (cache misses only).
    carte_wms_url: str = "https://data.geopf.fr/wms-r/wms"
    carte_rate_limit_per_ip: int = 60
    carte_rate_limit_amont: int = 600
    # Global bucket on the address resolutions (database reads) the route makes
    # on a cache miss; apart from the upstream bucket, which protects the IGN.
    carte_rate_limit_resolution: int = 600
    # 404 of an unknown key or a street key, kept briefly (entries, not bytes).
    carte_cache_negatif_ttl_secondes: int = 600
    carte_cache_negatif_entrees: int = 10_000
    # In-memory image cache, bounded in bytes (64 MiB), 21 days like the
    # Cache-Control the IGN sends; one accepted image is at most 2 MiB.
    carte_cache_octets: int = 64 * 1024 * 1024
    carte_cache_ttl_secondes: int = 1_814_400
    carte_image_max_octets: int = 2 * 1024 * 1024
    # Interactive map (04.10, ``app/carte/parcelles.py``): the JSON routes of
    # the parcels, outside the quota of the address page (Q7). One view is
    # several tiles and a move asks for more, so their per-minute bucket of one
    # visitor is apart from, and wider than, the bucket of the two static
    # images: 600 is the figure of the plan, to be measured at plan 04.10-12.
    carte_parcelles_rate_limit_per_ip: int = 600
    # A tile whose states are all stable is the same for every visitor: public
    # for one hour, so a cache in front of the API answers the next visitors
    # while a new import shows within the hour. A degraded tile is no-store.
    carte_parcelles_cache_secondes: int = 3600
    # Half side of the square the map of an address may be moved within (C6),
    # in pixels of the map projection at zoom 16, so that it holds the widest
    # view of the dialog (912 x 520 px, UI-SPEC 2.2) at every latitude: never
    # below 456. 640 is about 2 km of side in mainland France, more near the
    # equator; figure of the plan, submitted to Ianis at plan 04.10-11.
    carte_emprise_demi_cote_px_z16: int = 640
    # Base map of the interactive map (04.10-04, ``app/carte/tuiles.py``): the
    # Plan IGN in 256 px tiles, asked by the server to the WMTS of the
    # Geoplateforme (URL_SERVICE of 04.10-AMONT-IGN.md) and served on our origin.
    carte_wmts_url: str = "https://data.geopf.fr/wmts"
    # Per-minute bucket of one visitor on the tile route, apart from the buckets
    # of the static images and of the parcels: a view is a dozen tiles and each
    # move asks for more. 600 is the figure of the plan, not a measure.
    carte_fond_rate_limit_per_ip: int = 600
    # Global per-minute bucket on the calls actually made to the WMTS (cache
    # misses only), apart from the one of the WMS. The IGN publishes no limit
    # for the WMTS (CGU, annex 3, article 3.2: « Non soumis à limite d'usage »)
    # but may block an abusive use, and every call leaves from our one address:
    # 1 200 a minute (20 a second) is the fallback of the plan, not a measure.
    carte_fond_rate_limit_amont: int = 1200
    # Tile cache, bounded in bytes and apart from the image cache, so that
    # scrolling a map never evicts the static images; kept as long as they are
    # (``carte_cache_ttl_secondes``). 64 MiB is the figure of the plan: about
    # 2 700 tiles of the weight of the one probed (24 272 bytes), not a measure.
    carte_fond_cache_octets: int = 64 * 1024 * 1024
    # Largest tile accepted, read as a stream and abandoned beyond: above an
    # uncompressed 256 x 256 RGBA image (262 144 bytes), twenty times the tile
    # probed. A choice of this plan, not a figure of the IGN.
    carte_fond_tuile_max_octets: int = 512 * 1024
    # A failed tile is remembered this long, so that a view retried during an
    # outage does not call the IGN again for each tile; short, so that one
    # timeout does not blind a tile for the ten minutes of the 404 memory.
    # Bounded in entries by ``carte_cache_negatif_entrees``. A choice of this plan.
    carte_fond_echec_ttl_secondes: int = 30
    # A tile is served when its envelope, widened by this margin in degrees,
    # touches a departement hosting the BAN: about 2 km north to south and
    # 1.5 km east to west in mainland France, for the addresses of a seashore
    # whose view reaches past the drawn boundary. Figure of the plan.
    carte_fond_marge_deg: float = 0.02
    # Sales of one parcel of the interactive map (04.10-05,
    # ``app/carte/ventes.py``), outside the quota of the address page (Q7).
    # Per-minute bucket of one visitor, apart from the others and ten times
    # tighter than the one of the tiles: a view is several tiles, a look at
    # sales is one call per parcel, and walking the parcels of a map must not
    # become an export of DVF. 60 is the figure of the plan, measured at 04.10-12.
    carte_ventes_rate_limit_per_ip: int = 60
    # Sales per page in the panel of the map (UI-SPEC 2.5); the address page
    # keeps its ten (``app.adresse.ventes.PAR_PAGE``), read by the same reader.
    carte_ventes_par_page: int = 5
    # Most parcels drawn for one sale that took several: beyond, the group
    # serves its counts and its total surface without any shape, so that a
    # sale of thousands of parcels is not thousands of polygons in an answer
    # (T-04.10-14). Counted among those inside the bounds of the address.
    # 1 000 is an assumption of the plan, not a measure. It caps the shapes of
    # one answer of the sales route, never the parcels of a tile, which is
    # served whole (no setting caps it, ``tests/test_carte_parcelles.py``).
    carte_groupe_geometries_max: int = 1000
    # Public anonymous key (dc_anon_ prefix, no owner) shared by every visitor
    # of the site: its allowance is counted per visitor IP, never per key, so
    # one visitor cannot exhaust the site for everyone. Per minute: 120 keeps
    # C36 (a visitor of the public key may make 120 requests a minute). Per
    # day: a human browsing session (~6 reads per address page) stays far
    # below 2000; a scraper on one IP does not.
    anon_rate_limit_per_ip_minute: int = 120
    anon_rate_limit_per_ip_day: int = 2000
    # Requests with neither key nor session (Ianis, 2026-11-12): off by default (401);
    # when on, they share the per-IP buckets of the public key above.
    keyless_access_enabled: bool = False
    # MCP server for agents (D-143, Ianis 2026-11-15): POST /mcp answers 404 when
    # off. When on, it reads /v1 without a key, on the per-IP buckets above,
    # whatever keyless_access_enabled says. An Origin header, when sent, must be
    # one of cors_origins or of mcp_origines (DNS rebinding guard of the spec).
    mcp_enabled: bool = False
    mcp_origines: list[str] = []
    # Account creation (05-07 lot 5): /auth/register, /auth/quick-key and
    # /auth/resend-verification answer 404 when off. On for the working stacks;
    # the public stack sets it off (docker-compose.public.yml), on top of Caddy.
    registration_enabled: bool = True
    # Error reports of the site (``app/signalement``, framing of 2026-11-10).
    # Off by default: the route answers 404. Per visitor: 3 in ten minutes and
    # 10 a day; whole site: 200 a day, counted in the database. Retention in
    # days of a stored report.
    signalement_enabled: bool = False
    signalement_par_ip_10_min: int = 3
    signalement_par_ip_jour: int = 10
    signalement_plafond_jour: int = 200
    signalement_conservation_jours: int = 180

    # -- Admin dashboard -------------------------------------------------------
    admin_username: str = "admin"
    admin_password: str = "changeme"  # MUST override via ADMIN_PASSWORD env var
    smoke_test_api_key: str = ""  # API key for smoke tests (auto-created if set)

    # -- Auth & Dashboard ------------------------------------------------
    # MUST override via JWT_SECRET_KEY env
    jwt_secret_key: str = "change-me-in-production"
    jwt_expire_minutes: int = 480  # 8h — covers a full workday without refresh token
    auth_cookie_name: str = "access_token"
    auth_cookie_domain: str | None = ".datacommune.fr"

    @field_validator("auth_cookie_domain", mode="before")
    @classmethod
    def empty_cookie_domain_is_host_only(cls, value: object) -> object:
        return None if value == "" else value

    api_key_hmac_secret: str = ""  # Separate HMAC secret for API key hashing (SEC-03)
    # HMAC digest of the one internal key allowed to bypass the per-IP ceiling.
    # The database still enforces active key + internal plan + verified admin.
    internal_unlimited_key_hash: str = ""
    # Separate secrets for email/reset tokens (defaults derive from jwt_secret_key)
    email_token_secret: str = ""
    reset_token_secret: str = ""
    resend_api_key: str = ""  # MUST set via RESEND_API_KEY env
    resend_domain: str = "mail.datacommune.fr"
    email_enabled: bool = True
    # Outbound transport (Ianis, 2026-11-12): "smtp" sends through the mailbox host
    # of the domain (Migadu), so no extra processor is declared in /confidentialite.
    # Port 465 is implicit TLS, any other port uses STARTTLS.
    email_transport: Literal["resend", "smtp"] = "resend"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""  # MUST set via SMTP_PASSWORD env when transport is smtp
    # Sender address; empty keeps noreply@<resend_domain>.
    email_sender: str = ""
    dashboard_url: str = Field(
        default="https://datacommune.fr",
        validation_alias=AliasChoices("SITE_URL", "DASHBOARD_URL"),
    )
    api_base_url: str = "http://localhost:8000"

    # -- Stripe Billing --------------------------------------------------------
    stripe_secret_key: str = ""  # MUST set via STRIPE_SECRET_KEY env
    stripe_webhook_secret: str = ""  # MUST set via STRIPE_WEBHOOK_SECRET env
    stripe_enabled: bool = True
    stripe_price_pro_monthly: str = ""  # Stripe Price ID for Pro monthly
    stripe_price_pro_annual: str = ""  # Stripe Price ID for Pro annual
    stripe_price_business_monthly: str = ""  # Stripe Price ID for Business monthly
    stripe_price_business_annual: str = ""  # Stripe Price ID for Business annual
    stripe_price_enterprise_monthly: str = ""  # Stripe Price ID for Enterprise monthly
    stripe_price_enterprise_annual: str = ""  # Stripe Price ID for Enterprise annual

    # -- Scoring export --------------------------------------------------------
    scoring_export_max_rows: int = 5000  # max rows per CSV export

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


_promote_file_secrets(frozenset(name.upper() for name in Settings.model_fields))
settings = Settings()

# Fail-fast: reject insecure defaults in production
if not settings.debug:
    _insecure = []
    if settings.jwt_secret_key == "change-me-in-production":
        _insecure.append("JWT_SECRET_KEY")
    if settings.admin_password == "changeme":
        _insecure.append("ADMIN_PASSWORD")
    if "devpassword" in settings.database_url:
        _insecure.append("DATABASE_URL")
    if settings.email_enabled and settings.email_transport == "resend":
        if not settings.resend_api_key:
            _insecure.append("RESEND_API_KEY")
    elif settings.email_enabled:
        _insecure.extend(
            name
            for name, value in (
                ("SMTP_HOST", settings.smtp_host),
                ("SMTP_USERNAME", settings.smtp_username),
                ("SMTP_PASSWORD", settings.smtp_password),
                ("EMAIL_SENDER", settings.email_sender),
            )
            if not value
        )
    if settings.stripe_enabled and not settings.stripe_webhook_secret:
        _insecure.append("STRIPE_WEBHOOK_SECRET")
    if not settings.api_key_hmac_secret:
        _insecure.append("API_KEY_HMAC_SECRET")
    if settings.api_base_url.startswith(("http://localhost", "http://127.0.0.1")):
        _insecure.append("API_BASE_URL")
    if _insecure:
        raise RuntimeError(
            f"Insecure default(s) detected in production: {', '.join(_insecure)}. "
            "Set them via environment variables or .env file."
        )
