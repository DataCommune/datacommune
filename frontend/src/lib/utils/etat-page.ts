// A page whose data source failed is still rendered (the visitor reads what happened), but it must
// not be answered 200: a robot would index an « unavailable » page. The load posts an internal
// header through the `setHeaders` of its event; `hooks.server.ts` reads it, answers 503 with
// `Retry-After` and removes it, so it never reaches a client (04.7-10, decision 11, threat T-10).
// The commune fiche uses it; the department page calls the same function on its outage state.

/** Internal response header read and removed by `hooks.server.ts`. */
export const PAGE_STATE_HEADER = 'x-dc-etat-page';
/** Value of `PAGE_STATE_HEADER` for a page whose source failed. */
export const PAGE_STATE_OUTAGE = 'panne';
/** Internal companion of the marker: the `Retry-After` the failed API call served, in seconds. */
export const OUTAGE_RETRY_HEADER = 'x-dc-retry-after';
/** `Retry-After` sent when the failure served none: the same minute as the JSON export of a fiche. */
export const DEFAULT_RETRY_AFTER_SECONDS = 60;
/** A served delay is honoured up to a day; beyond, a robot is not asked to wait for longer. */
export const MAX_RETRY_AFTER_SECONDS = 86_400;

/** The `setHeaders` of a SvelteKit load event; absent on the client and in a plain `+server` route. */
export type SetHeaders = (headers: Record<string, string>) => void;

/**
 * Mark the page being loaded as an outage. *retryAfter* is the delay (seconds) served by the failed
 * call; anything that is not a positive whole number is left out and the hook uses its default.
 * Called once per load: SvelteKit refuses to set the same header twice.
 */
export function markOutage(setHeaders: SetHeaders | undefined, retryAfter?: number | null): void {
	if (!setHeaders) return;
	const headers: Record<string, string> = { [PAGE_STATE_HEADER]: PAGE_STATE_OUTAGE };
	if (typeof retryAfter === 'number' && Number.isInteger(retryAfter) && retryAfter > 0) {
		headers[OUTAGE_RETRY_HEADER] = String(retryAfter);
	}
	setHeaders(headers);
}
