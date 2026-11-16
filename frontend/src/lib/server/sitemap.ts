// Pure rules of the sitemap the site serves itself (04.7-10, decision 5): the shape of the codes it
// accepts, de-duplication in served order, XML escaping, the two documents of the protocol (urlset,
// sitemapindex) and a small one-hour memory cache. The routes `/sitemap.xml` and `/sitemaps/*` use
// them; nothing here reads the network or the clock by itself (the cache takes its clock and its
// loader), so every rule is exercised by `sitemap.test.ts` alone.
//
// Principle: a sitemap is complete or it is an error. Beyond 50 000 URLs, with no URL at all or
// with an address that is not one, a document is refused, never truncated or served empty; and the
// cache never keeps a failure, so a crawler that met a 503 meets a fresh attempt on its next visit.

/** Limit of the protocol (sitemaps.org): URLs per sitemap, and sitemaps per index. */
export const MAX_URLS_PER_FILE = 50_000;
/** Limit of the protocol for one `<loc>`, in characters. */
const MAX_LOC_LENGTH = 2048;
/** Life of a cached document: the API is read once an hour at most. */
export const SITEMAP_TTL_MS = 3_600_000;
/** Distinct documents kept at once; the keys come from validated codes, this is only a ceiling. */
const DEFAULT_MAX_ENTRIES = 256;

const INSEE_CODE = /^(\d{5}|2[AB]\d{3})$/;
const DEPARTEMENT_CODE = /^(\d{2}|2[AB]|97\d)$/;
const CALENDAR_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const ABSOLUTE_HTTP_URL = /^https?:\/\/[^\s\x00-\x1f\x7f]+$/;

/** INSEE code of a commune: five digits, or 2A/2B and three digits for Corsica. */
export function isInseeCode(value: unknown): value is string {
	return typeof value === 'string' && INSEE_CODE.test(value);
}

/** Code of a department: two digits, 2A, 2B, or three digits starting with 97. */
export function isDepartementCode(value: unknown): value is string {
	return typeof value === 'string' && DEPARTEMENT_CODE.test(value);
}

/**
 * The date as the protocol writes `lastmod` (AAAA-MM-JJ) when *value* is a real calendar day, else
 * null. A date with a time, another shape or an impossible day is not repaired: the page then has no
 * `lastmod`, which is better than a wrong one.
 */
export function toLastmod(value: unknown): string | null {
	if (typeof value !== 'string') return null;
	const match = CALENDAR_DATE.exec(value);
	if (!match) return null;
	const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
	const date = new Date(Date.UTC(year, month - 1, day));
	// Date.UTC rolls an impossible day over (30 February -> 2 March) and maps years 0 to 99 to 19xx.
	const real = date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
	return real ? value : null;
}

/** An address of a sitemap and the day it was last modified, when known. */
export interface SitemapEntry {
	/** Absolute http(s) URL. */
	loc: string;
	/** AAAA-MM-JJ; anything else is left out of the document. */
	lastmod?: string | null;
}

/** Why a document was refused: too many URLs, none, or an address that is not an absolute URL. */
export class SitemapError extends Error {
	readonly code: 'too-many-urls' | 'empty' | 'invalid-loc';
	readonly count?: number;
	readonly limit?: number;

	constructor(code: SitemapError['code'], message: string, details: { count?: number; limit?: number } = {}) {
		super(message);
		this.name = 'SitemapError';
		this.code = code;
		this.count = details.count;
		this.limit = details.limit;
	}
}

/** *value* written as XML text (the ampersand first, so that the other entities are not escaped twice). */
export function escapeXml(value: string): string {
	return value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&apos;');
}

/** The entries without a repeated `loc`: the first one wins, the order served is kept. */
export function dedupeEntries<T extends { loc: string }>(entries: readonly T[]): T[] {
	const seen = new Set<string>();
	const kept: T[] = [];
	for (const entry of entries) {
		if (seen.has(entry.loc)) continue;
		seen.add(entry.loc);
		kept.push(entry);
	}
	return kept;
}

function render(root: 'urlset' | 'sitemapindex', item: 'url' | 'sitemap', entries: readonly SitemapEntry[]): string {
	if (entries.length === 0) {
		throw new SitemapError('empty', `A ${root} with no ${item} is never served.`);
	}
	if (entries.length > MAX_URLS_PER_FILE) {
		throw new SitemapError(
			'too-many-urls',
			`${entries.length} ${item} entries exceed the limit of ${MAX_URLS_PER_FILE} per ${root}; nothing is truncated.`,
			{ count: entries.length, limit: MAX_URLS_PER_FILE }
		);
	}
	const lines = entries.map(({ loc, lastmod }) => {
		if (typeof loc !== 'string' || loc.length > MAX_LOC_LENGTH || !ABSOLUTE_HTTP_URL.test(loc)) {
			throw new SitemapError('invalid-loc', `Not an absolute http(s) address of at most ${MAX_LOC_LENGTH} characters: ${JSON.stringify(String(loc).slice(0, 80))}`);
		}
		const date = toLastmod(lastmod);
		return `  <${item}><loc>${escapeXml(loc)}</loc>${date ? `<lastmod>${date}</lastmod>` : ''}</${item}>`;
	});
	return [
		'<?xml version="1.0" encoding="UTF-8"?>',
		`<${root} xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">`,
		...lines,
		`</${root}>`,
		''
	].join('\n');
}

/** The `urlset` document of *entries* (1 to 50 000), each with its `lastmod` when it is a valid date. */
export function renderUrlset(entries: readonly SitemapEntry[]): string {
	return render('urlset', 'url', entries);
}

/** The `sitemapindex` document of *entries* (1 to 50 000 sitemaps), each with its `lastmod` when valid. */
export function renderSitemapIndex(entries: readonly SitemapEntry[]): string {
	return render('sitemapindex', 'sitemap', entries);
}

export interface SitemapCache<T> {
	/**
	 * The value stored under *key* while it is less than an hour old, else the result of *load*. Readers
	 * who arrive while a load of the same key runs share it. A failure is passed to all of them and kept
	 * by none.
	 */
	get(key: string, load: () => Promise<T> | T): Promise<T>;
	/** Forget every value and every load in flight (a load that ends afterwards stores nothing). */
	clear(): void;
}

export interface SitemapCacheOptions {
	/** Clock in milliseconds; injected by the tests. */
	now?: () => number;
	ttlMs?: number;
	maxEntries?: number;
}

/** A memory cache for the documents of the sitemap: one hour, one load at a time per key. */
export function createSitemapCache<T>(options: SitemapCacheOptions = {}): SitemapCache<T> {
	const now = options.now ?? Date.now;
	const ttlMs = options.ttlMs ?? SITEMAP_TTL_MS;
	const maxEntries = options.maxEntries ?? DEFAULT_MAX_ENTRIES;
	const stored = new Map<string, { value: T; storedAt: number }>();
	const running = new Map<string, Promise<T>>();

	function get(key: string, load: () => Promise<T> | T): Promise<T> {
		const hit = stored.get(key);
		if (hit) {
			if (now() - hit.storedAt < ttlMs) return Promise.resolve(hit.value);
			stored.delete(key);
		}
		const shared = running.get(key);
		if (shared) return shared;

		// `async` so that a loader that throws at once is a rejection like any other.
		const attempt: Promise<T> = (async () => load())().then(
			(value) => {
				// Stored only if this load is still the one of the key (not cleared meanwhile), from its end.
				if (running.get(key) === attempt) {
					running.delete(key);
					stored.set(key, { value, storedAt: now() });
					while (stored.size > maxEntries) stored.delete(stored.keys().next().value as string);
				}
				return value;
			},
			(error: unknown) => {
				if (running.get(key) === attempt) running.delete(key);
				throw error;
			}
		);
		running.set(key, attempt);
		return attempt;
	}

	return {
		get,
		clear() {
			stored.clear();
			running.clear();
		}
	};
}
