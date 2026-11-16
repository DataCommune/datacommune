#!/usr/bin/env node
// SEC-02 (Phase 46-04) — fail-closed pre-build gate for the public frontend key.
//
// D-10: the non-empty frontend prefix MUST be dc_anon_ in build, CI and CD.
//       A fail-closed validator replaces the previous console.warn.
// D-11: the anon key is public by design but is NEVER echoed. This gate reports
//       only a normalized reason code, never the credential value.
//
// Modes:
//   deploy (default): PUBLIC_DC_API_KEY must be a non-empty value starting with dc_anon_.
//                     Empty, dc_live_ and any other prefix FAIL (non-zero exit).
//   --allow-empty:    local/dev convenience — an empty key is accepted (the app then
//                     simply sends no X-API-Key header). A present key is still validated.
//
// No new dependency. Pure node: stdlib.

export const ALLOWED_PREFIX = 'dc_anon_';
const FORBIDDEN_SERVER_PREFIX = 'dc_live_';

/**
 * Validate a public API key value without ever exposing it.
 * @param {string|undefined|null} key raw key value (usually process.env.PUBLIC_DC_API_KEY)
 * @param {{ allowEmpty?: boolean }} opts
 * @returns {{ ok: boolean, reason: string }} reason is a redacted code, never the value
 */
export function validatePublicApiKey(key, { allowEmpty = false } = {}) {
	const value = (key ?? '').trim();

	if (value === '') {
		return allowEmpty
			? { ok: true, reason: 'empty-allowed-local' }
			: { ok: false, reason: 'empty-forbidden-in-deploy' };
	}

	if (value.startsWith(ALLOWED_PREFIX)) {
		return { ok: true, reason: 'valid-anon-prefix' };
	}

	if (value.startsWith(FORBIDDEN_SERVER_PREFIX)) {
		// Never include `value`: dc_live_ is a server credential.
		return { ok: false, reason: 'forbidden-server-key-prefix' };
	}

	return { ok: false, reason: 'invalid-prefix' };
}

function isMain() {
	if (!process.argv[1]) return false;
	try {
		return import.meta.url === new URL(`file://${process.argv[1]}`).href ||
			import.meta.url.endsWith(process.argv[1].replace(/\\/g, '/'));
	} catch {
		return false;
	}
}

if (isMain()) {
	const allowEmpty = process.argv.includes('--allow-empty');
	const result = validatePublicApiKey(process.env.PUBLIC_DC_API_KEY, { allowEmpty });
	const mode = allowEmpty ? 'local(--allow-empty)' : 'deploy(strict)';

	if (result.ok) {
		// Redacted: never print the key. Only the outcome and reason code.
		console.log(`[validate-public-api-key] OK mode=${mode} reason=${result.reason}`);
		process.exit(0);
	}

	console.error(`[validate-public-api-key] FAIL mode=${mode} reason=${result.reason}`);
	console.error('[validate-public-api-key] PUBLIC_DC_API_KEY must be a non-empty dc_anon_ value in deploy mode.');
	process.exit(1);
}
