/**
 * Return a safe internal redirect path, or `fallback` for anything unsafe.
 *
 * Guards the post-login `?redirect=` param against open-redirect attacks. Only
 * same-origin absolute paths are accepted. A target is rejected when it:
 *  - is empty / null / not a string;
 *  - does not start with a single "/" (absolute schemes, bare words);
 *  - starts with "//" (protocol-relative URL → external host);
 *  - contains ":" (e.g. javascript:, data:, http:);
 *  - contains "\" (backslash — some browsers normalize it to "/");
 *  - contains an encoded "/", "\" or ":" (%2f, %5c, %3a) that would smuggle
 *    one of the above once decoded downstream.
 */
export function safeRedirect(
	target: string | null | undefined,
	fallback = '/mon-espace'
): string {
	if (!target || typeof target !== 'string') return fallback;
	if (!target.startsWith('/')) return fallback;
	if (target.startsWith('//')) return fallback;
	if (target.includes(':')) return fallback;
	if (target.includes('\\')) return fallback;

	const lower = target.toLowerCase();
	if (lower.includes('%2f') || lower.includes('%5c') || lower.includes('%3a')) {
		return fallback;
	}

	return target;
}
