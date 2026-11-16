import { PUBLIC_DC_API_KEY } from '$env/static/public';

// Centralized configuration for all external URLs
// Used across components, layouts, and pages

export const DEPLOYMENT_ENV = (import.meta.env.VITE_DEPLOYMENT_ENV || 'production').trim();
export const DEPLOYMENT_SHA = (import.meta.env.VITE_DEPLOYMENT_SHA || 'unknown').trim();
export const IS_PREPRODUCTION = DEPLOYMENT_ENV === 'preproduction';
export const SITE_URL = IS_PREPRODUCTION ? 'https://dev.datacommune.fr' : 'https://datacommune.fr';
export const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
export const OPENAPI_URL = `${API_URL}/openapi.json`;
// Build-time switch of the account space (login, registration, « Mon espace », billing, admin).
// ON by default so existing builds and tests are unchanged; the public 44/85 opening builds with
// VITE_ACCOUNTS_ENABLED=false: the links disappear (SiteHeader, SiteFooter, /developpeurs) and
// every route of the (auth) and (app) groups answers 404 (src/hooks.ts, `reroute`).
export const ACCOUNTS_ENABLED = (import.meta.env.VITE_ACCOUNTS_ENABLED ?? '').trim().toLowerCase() !== 'false';
// Paid plans (/billing, « Facturation »), only inside the account space. Accounts with an API key and
// no paid plan (Ianis, 2026-11-12, D-133 amended): VITE_BILLING_ENABLED=false.
export const BILLING_ENABLED =
  ACCOUNTS_ENABLED && (import.meta.env.VITE_BILLING_ENABLED ?? '').trim().toLowerCase() !== 'false';
export const OG_IMAGE_URL = `${SITE_URL}/og-image.png`;

// SECURITY NOTE (SEC-02): Public app key is intentionally exposed in the JS bundle.
// It must come from build-time env so rotation does not require a source change.
// Key must use dc_anon_* prefix. This is enforced fail-closed at build/deploy
// time by scripts/validate-public-api-key.mjs and scripts/scan-public-bundle.mjs
// (wired into `npm run deploy` and both CD workflows) — a dc_live_ key can never
// reach the bundle, so no runtime warning is needed here.
// Resilient to a missing build-time env (e.g. fresh clone / CI without .env):
// avoids a hard crash on `.trim()` of undefined; an empty key simply sends no X-API-Key header.
export const PUBLIC_API_KEY = (PUBLIC_DC_API_KEY ?? '').trim();

export function getPublicApiHeaders(): Record<string, string> {
  return PUBLIC_API_KEY ? { 'X-API-Key': PUBLIC_API_KEY } : {};
}

// SEO / audience measurement. Both are build-time and both are OPTIONAL: an unset
// value renders no tag at all, so a fresh clone or a dev build stays inert.
// Read through `import.meta.env` (like VITE_API_URL above), NOT `$env/static/public`:
// the latter fails compilation outright on a variable that is absent from the
// environment, which would make every build depend on a .env nobody needs locally.
// MESURE_AUDIENCE: counts the pages shown with the self-hosted GoatCounter of the public stack
// (04.7-14b, `$lib/mesure-audience`). Only the public build sets VITE_MESURE_AUDIENCE=true.
export const MESURE_AUDIENCE = (import.meta.env.VITE_MESURE_AUDIENCE ?? '').trim().toLowerCase() === 'true';
// GOOGLE_SITE_VERIFICATION: only the token, not the full meta tag. Prefer the DNS
// TXT method when possible; this exists for the HTML-tag fallback.
export const GOOGLE_SITE_VERIFICATION = (
  import.meta.env.VITE_GOOGLE_SITE_VERIFICATION || ''
).trim();
