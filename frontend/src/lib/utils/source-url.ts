/** Preserve attribution text when an external URL is absent, malformed or not HTTPS. */
export function urlHttpsSeulement(value: string | null | undefined): string | null {
	if (!value) return null;
	try {
		return new URL(value).protocol === 'https:' ? value : null;
	} catch {
		return null;
	}
}
