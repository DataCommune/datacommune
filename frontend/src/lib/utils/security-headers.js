// Canonical security response headers for the frontend (audit S4).
//
// TWO delivery paths read this list:
//   - `src/hooks.server.ts`, for everything SvelteKit renders on demand
//     (/commune/[code], /bien/[token], the app SPA);
//   - `server.js`, the production entry, which sets it on every response before
//     adapter-node's handler runs, so the static assets and prerendered pages
//     sirv serves without entering the hook carry it too (KI-P3, closed).
//
// `npm run test:served` proves both on the built artifact over HTTP;
// `npm run security:gates` keeps the dead Cloudflare `_headers` file from coming
// back as a silent third source of truth.
export const SECURITY_HEADERS = {
	// One year (05-07 lot 5, after one day verified live since 2026-10). Never
	// `preload`, and never `includeSubDomains`: api.datacommune.fr serves its own
	// HSTS from Caddy and must not inherit this one.
	'Strict-Transport-Security': 'max-age=31536000',
	// Safe as DENY: the frontend embeds no iframe of itself.
	'X-Frame-Options': 'DENY',
	'X-Content-Type-Options': 'nosniff',
	'Referrer-Policy': 'strict-origin-when-cross-origin',
	'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
	// Fallback CSP for the answers SvelteKit does not render (prerendered pages and assets served by
	// sirv, endpoints): the page CSP of svelte.config.js wins wherever SvelteKit sets it (the hook and
	// server.js only fill a missing header). No script-src or default-src here: on a prerendered
	// page it adds to the <meta> CSP, and would block the hashes that one allows.
	'Content-Security-Policy': "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
};
