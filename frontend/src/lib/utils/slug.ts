/**
 * Address URL token primitives (D-04 fallback): `/v1/resolve` exposes no stable BAN id
 * for an address (Priority Question 1 of the RESEARCH), so a public Fiche Bien route
 * carries the address as a slugified segment instead and re-issues it as `q=` on the
 * server-side loader (`resolveBienParAdresse`, resolve.ts).
 *
 * Pure, no I/O, no network — never installs a slugify library (RESEARCH Don't Hand-Roll:
 * the address case here is narrow enough to not warrant one). The slug is only ever
 * consumed via `URLSearchParams` (never interpolated into HTML/JS) — see threat T-47-03.
 */

/**
 * Turn an address label ("12 Rue de la Paix, 44000 Nantes") into a stable, lowercase,
 * accent-free URL token ("12-rue-de-la-paix-44000-nantes"): NFD-normalize to strip
 * diacritics, lowercase, replace every non [a-z0-9] run with a single "-", then trim
 * leading/trailing "-".
 */
export function slugifyAdresse(label: string): string {
	return label
		.normalize('NFD')
		.replace(/[̀-ͯ]/g, '') // strip combining diacritical marks
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-+|-+$/g, '');
}

/**
 * Reconstruct a re-typeable text from a slug ("12-rue-de-la-paix-44000-nantes" →
 * "12 rue de la paix 44000 nantes"), for re-use as `q=` on the fuzzy geocoder.
 * Strict equality to the original label is NOT guaranteed (RESEARCH Pitfall 3) —
 * only non-vacuity and the absence of residual "-" is. A slug with no letters/digits
 * (e.g. "" or "---") yields "".
 */
export function deslugifyAdresse(slug: string): string {
	return slug.replace(/-+/g, ' ').trim();
}
