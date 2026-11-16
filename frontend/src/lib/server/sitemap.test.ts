// Pure rules of the served sitemap (04.7-10, decision 5): code shapes, de-duplication, XML escaping,
// the two documents (urlset, sitemapindex), the 50 000 URL limit and the one-hour cache. The routes
// that call them (`/sitemap.xml`, `/sitemaps/*`) come with the API contract; nothing here reads the
// network or the clock by itself.
import { describe, expect, it } from 'vitest';
import {
	createSitemapCache,
	dedupeEntries,
	escapeXml,
	isDepartementCode,
	isInseeCode,
	MAX_URLS_PER_FILE,
	renderSitemapIndex,
	renderUrlset,
	SITEMAP_TTL_MS,
	SitemapError,
	toLastmod
} from './sitemap';

const SITE = 'https://datacommune.fr';

describe('codes', () => {
	it('accepts a five-digit INSEE code and the Corsican 2A/2B ones', () => {
		for (const code of ['44109', '75056', '01001', '2A004', '2B033', '97411']) expect(isInseeCode(code), code).toBe(true);
	});

	it('refuses everything else: short, long, lower case, other letters, padding, not a string', () => {
		for (const code of ['4410', '441099', '2a004', '2C004', '2A04', 'abcde', '', ' 44109', '44109 ', '44109\n', '../etc', '44109/../1']) {
			expect(isInseeCode(code), JSON.stringify(code)).toBe(false);
		}
		for (const code of [null, undefined, 44109, {}, ['44109']]) expect(isInseeCode(code), String(code)).toBe(false);
	});

	it('accepts a two-digit department, 2A, 2B and the three-digit overseas ones starting with 97', () => {
		for (const code of ['01', '44', '85', '75', '2A', '2B', '971', '974', '976']) expect(isDepartementCode(code), code).toBe(true);
	});

	it('refuses a department written otherwise: one digit, three digits not 97x, lower case, padding', () => {
		for (const code of ['4', '444', '2a', '2C', '9', '', ' 44', '44 ', '44\n', '4A', '2A0', '98A', '../']) {
			expect(isDepartementCode(code), JSON.stringify(code)).toBe(false);
		}
		for (const code of [null, undefined, 44]) expect(isDepartementCode(code), String(code)).toBe(false);
	});
});

describe('toLastmod', () => {
	it('keeps a real calendar date written AAAA-MM-JJ', () => {
		for (const date of ['2026-11-01', '2024-02-29', '1999-12-31']) expect(toLastmod(date), date).toBe(date);
	});

	it('returns null for anything else: unknown, impossible, other shape, with a time', () => {
		for (const date of ['2026-02-30', '2025-02-29', '2026-13-01', '2026-00-10', '2026-1-1', '2026-11-01T10:00:00Z', '01/11/2026', '', ' 2026-11-01', '0000-01-01']) {
			expect(toLastmod(date), JSON.stringify(date)).toBeNull();
		}
		for (const date of [null, undefined, 20261101, new Date()]) expect(toLastmod(date), String(date)).toBeNull();
	});
});

describe('escapeXml', () => {
	it('escapes the five characters of XML, the ampersand first', () => {
		expect(escapeXml(`a&b<c>d"e'f`)).toBe('a&amp;b&lt;c&gt;d&quot;e&apos;f');
		expect(escapeXml('&amp;')).toBe('&amp;amp;');
		expect(escapeXml('plain')).toBe('plain');
	});
});

describe('dedupeEntries', () => {
	it('keeps the first of each URL, in the order served', () => {
		const entrees = [
			{ loc: `${SITE}/commune/44109`, lastmod: '2026-10-30' },
			{ loc: `${SITE}/commune/85191` },
			{ loc: `${SITE}/commune/44109`, lastmod: '2026-01-01' },
			{ loc: `${SITE}/commune/44020` },
			{ loc: `${SITE}/commune/85191`, lastmod: '2026-05-05' }
		];
		expect(dedupeEntries(entrees)).toEqual([
			{ loc: `${SITE}/commune/44109`, lastmod: '2026-10-30' },
			{ loc: `${SITE}/commune/85191` },
			{ loc: `${SITE}/commune/44020` }
		]);
	});

	it('leaves a list without duplicate alone and does not change its input', () => {
		const entrees = [{ loc: `${SITE}/a` }, { loc: `${SITE}/b` }];
		const copie = structuredClone(entrees);
		expect(dedupeEntries(entrees)).toEqual(copie);
		expect(entrees).toEqual(copie);
		expect(dedupeEntries([])).toEqual([]);
	});
});

describe('renderUrlset', () => {
	it('writes the urlset of the protocol, one url per entry, lastmod only when it is a valid date', () => {
		const xml = renderUrlset([
			{ loc: `${SITE}/`, lastmod: null },
			{ loc: `${SITE}/commune/44109`, lastmod: '2026-10-30' },
			{ loc: `${SITE}/commune/85191`, lastmod: '2026-02-30' },
			{ loc: `${SITE}/commune/2A004` }
		]);
		expect(xml).toBe(
			[
				'<?xml version="1.0" encoding="UTF-8"?>',
				'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
				'  <url><loc>https://datacommune.fr/</loc></url>',
				'  <url><loc>https://datacommune.fr/commune/44109</loc><lastmod>2026-10-30</lastmod></url>',
				'  <url><loc>https://datacommune.fr/commune/85191</loc></url>',
				'  <url><loc>https://datacommune.fr/commune/2A004</loc></url>',
				'</urlset>',
				''
			].join('\n')
		);
	});

	it('escapes the address, and the document stays well-formed XML that reads back the same address', () => {
		const loc = `${SITE}/communes?q=a&departement=44&x=<b>"'`;
		const xml = renderUrlset([{ loc, lastmod: '2026-11-01' }]);
		expect(xml).toContain('<loc>https://datacommune.fr/communes?q=a&amp;departement=44&amp;x=&lt;b&gt;&quot;&apos;</loc>');
		const document = new DOMParser().parseFromString(xml, 'application/xml');
		expect(document.querySelector('parsererror')).toBeNull();
		expect(document.getElementsByTagName('loc')[0].textContent).toBe(loc);
		expect(document.getElementsByTagName('lastmod')[0].textContent).toBe('2026-11-01');
	});

	it('accepts exactly 50 000 URL', () => {
		const entrees = Array.from({ length: MAX_URLS_PER_FILE }, (_, i) => ({ loc: `${SITE}/commune/${i}` }));
		const xml = renderUrlset(entrees);
		expect(xml.match(/<url>/g)).toHaveLength(50_000);
	});

	it('refuses 50 001 URL with an error, never a truncated file', () => {
		const entrees = Array.from({ length: MAX_URLS_PER_FILE + 1 }, (_, i) => ({ loc: `${SITE}/commune/${i}` }));
		let erreur: unknown;
		try {
			renderUrlset(entrees);
		} catch (e) {
			erreur = e;
		}
		expect(erreur).toBeInstanceOf(SitemapError);
		expect(erreur).toMatchObject({ code: 'too-many-urls', count: 50_001, limit: 50_000 });
	});

	it('refuses an empty list: an empty urlset is never served', () => {
		expect(() => renderUrlset([])).toThrowError(expect.objectContaining({ code: 'empty' }));
	});

	it('refuses an address that is not an absolute http(s) URL, holds a space or a control character, or is too long', () => {
		const mauvaises = [
			'',
			'/commune/44109',
			'javascript:alert(1)',
			'ftp://datacommune.fr/x',
			'https://datacommune.fr/a b',
			'https://datacommune.fr/a\nb',
			'https://datacommune.fr/a\tb',
			`${SITE}/${'a'.repeat(2048)}`
		];
		for (const loc of mauvaises) {
			expect(() => renderUrlset([{ loc }]), JSON.stringify(loc.slice(0, 40))).toThrowError(expect.objectContaining({ code: 'invalid-loc' }));
		}
		expect(() => renderUrlset([{ loc: `${SITE}/${'a'.repeat(2048 - SITE.length - 1)}` }])).not.toThrow();
	});
});

describe('renderSitemapIndex', () => {
	it('writes the sitemapindex of the protocol, one sitemap per entry, lastmod only when valid', () => {
		const xml = renderSitemapIndex([
			{ loc: `${SITE}/sitemaps/pages.xml` },
			{ loc: `${SITE}/sitemaps/departements.xml`, lastmod: '2026-10-30' },
			{ loc: `${SITE}/sitemaps/communes-44.xml`, lastmod: 'hier' }
		]);
		expect(xml).toBe(
			[
				'<?xml version="1.0" encoding="UTF-8"?>',
				'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
				'  <sitemap><loc>https://datacommune.fr/sitemaps/pages.xml</loc></sitemap>',
				'  <sitemap><loc>https://datacommune.fr/sitemaps/departements.xml</loc><lastmod>2026-10-30</lastmod></sitemap>',
				'  <sitemap><loc>https://datacommune.fr/sitemaps/communes-44.xml</loc></sitemap>',
				'</sitemapindex>',
				''
			].join('\n')
		);
	});

	it('is well-formed, escapes, and refuses an empty list, a bad address or more than 50 000 sitemaps', () => {
		const xml = renderSitemapIndex([{ loc: `${SITE}/sitemaps/a.xml?x=1&y=2` }]);
		expect(new DOMParser().parseFromString(xml, 'application/xml').querySelector('parsererror')).toBeNull();
		expect(xml).toContain('x=1&amp;y=2');
		expect(() => renderSitemapIndex([])).toThrowError(expect.objectContaining({ code: 'empty' }));
		expect(() => renderSitemapIndex([{ loc: 'sitemaps/a.xml' }])).toThrowError(expect.objectContaining({ code: 'invalid-loc' }));
		const trop = Array.from({ length: MAX_URLS_PER_FILE + 1 }, (_, i) => ({ loc: `${SITE}/sitemaps/${i}.xml` }));
		expect(() => renderSitemapIndex(trop)).toThrowError(expect.objectContaining({ code: 'too-many-urls' }));
	});
});

describe('createSitemapCache', () => {
	/** A clock the test moves, and a loader that counts its calls. */
	function banc(options: { maxEntries?: number } = {}) {
		let maintenant = 1_000_000;
		const cache = createSitemapCache<string>({ now: () => maintenant, ...options });
		const avance = (ms: number) => {
			maintenant += ms;
		};
		const chargeur = (valeur: string) => {
			const appels = { n: 0 };
			return { appels, load: async () => (appels.n++, valeur) };
		};
		return { cache, avance, chargeur };
	}

	it('keeps a value for one hour: one load, then the stored value', async () => {
		expect(SITEMAP_TTL_MS).toBe(3_600_000);
		const { cache, avance, chargeur } = banc();
		const a = chargeur('A');
		expect(await cache.get('index', a.load)).toBe('A');
		avance(SITEMAP_TTL_MS - 1);
		const autre = chargeur('B');
		expect(await cache.get('index', autre.load)).toBe('A');
		expect(a.appels.n).toBe(1);
		expect(autre.appels.n).toBe(0);
	});

	it('loads again once the hour is over, at the exact boundary', async () => {
		const { cache, avance, chargeur } = banc();
		await cache.get('index', chargeur('A').load);
		avance(SITEMAP_TTL_MS);
		const b = chargeur('B');
		expect(await cache.get('index', b.load)).toBe('B');
		expect(b.appels.n).toBe(1);
	});

	it('counts the hour from the end of the load, not from its start', async () => {
		const { cache, avance } = banc();
		await cache.get('lent', async () => {
			avance(600_000);
			return 'A';
		});
		avance(SITEMAP_TTL_MS - 1);
		const b = { n: 0 };
		expect(
			await cache.get('lent', async () => {
				b.n++;
				return 'B';
			})
		).toBe('A');
		expect(b.n).toBe(0);
	});

	it('keeps a falsy value: an empty string is a hit, not a miss', async () => {
		const { cache, chargeur } = banc();
		await cache.get('vide', chargeur('').load);
		const b = chargeur('B');
		expect(await cache.get('vide', b.load)).toBe('');
		expect(b.appels.n).toBe(0);
	});

	it('keeps the keys apart', async () => {
		const { cache, chargeur } = banc();
		expect(await cache.get('communes-44', chargeur('44').load)).toBe('44');
		expect(await cache.get('communes-85', chargeur('85').load)).toBe('85');
		expect(await cache.get('communes-44', chargeur('x').load)).toBe('44');
	});

	it('runs one call at a time per key: simultaneous readers share the one load', async () => {
		const { cache } = banc();
		let appels = 0;
		let fin!: (v: string) => void;
		const attente = new Promise<string>((resolve) => (fin = resolve));
		const load = () => {
			appels++;
			return attente;
		};
		const lectures = [cache.get('index', load), cache.get('index', load), cache.get('index', load)];
		fin('A');
		expect(await Promise.all(lectures)).toEqual(['A', 'A', 'A']);
		expect(appels).toBe(1);
	});

	it('does not make two keys wait for each other', async () => {
		const { cache, chargeur } = banc();
		const a = chargeur('A');
		const b = chargeur('B');
		await Promise.all([cache.get('a', a.load), cache.get('b', b.load)]);
		expect([a.appels.n, b.appels.n]).toEqual([1, 1]);
	});

	it('never keeps an error: the readers waiting on it all fail, the next call loads again', async () => {
		const { cache, chargeur } = banc();
		let appels = 0;
		let echec!: (e: Error) => void;
		const attente = new Promise<string>((_, reject) => (echec = reject));
		const load = () => {
			appels++;
			return attente;
		};
		const lectures = [cache.get('index', load), cache.get('index', load)];
		const panne = new Error('API 503');
		echec(panne);
		const resultats = await Promise.allSettled(lectures);
		expect(resultats.map((r) => r.status)).toEqual(['rejected', 'rejected']);
		expect(resultats.every((r) => r.status === 'rejected' && r.reason === panne)).toBe(true);
		expect(appels).toBe(1);

		const reprise = chargeur('A');
		expect(await cache.get('index', reprise.load)).toBe('A');
		expect(reprise.appels.n).toBe(1);
	});

	it('treats a loader that throws at once like a rejected one, and keeps nothing', async () => {
		const { cache, chargeur } = banc();
		await expect(
			cache.get('index', () => {
				throw new Error('boum');
			})
		).rejects.toThrow('boum');
		const reprise = chargeur('A');
		expect(await cache.get('index', reprise.load)).toBe('A');
	});

	it('does not serve the old value when its reload fails, and does not store the failure', async () => {
		const { cache, avance, chargeur } = banc();
		await cache.get('index', chargeur('A').load);
		avance(SITEMAP_TTL_MS);
		await expect(cache.get('index', async () => Promise.reject(new Error('API 429')))).rejects.toThrow('API 429');
		const reprise = chargeur('B');
		expect(await cache.get('index', reprise.load)).toBe('B');
		expect(reprise.appels.n).toBe(1);
	});

	it('stays small: past its limit the oldest key goes first', async () => {
		const { cache, chargeur } = banc({ maxEntries: 2 });
		await cache.get('a', chargeur('A').load);
		await cache.get('b', chargeur('B').load);
		await cache.get('c', chargeur('C').load);
		const a = chargeur('A2');
		const c = chargeur('C2');
		expect(await cache.get('a', a.load)).toBe('A2');
		expect(a.appels.n).toBe(1);
		expect(await cache.get('c', c.load)).toBe('C');
		expect(c.appels.n).toBe(0);
	});

	it('forgets everything on clear, even a load still running', async () => {
		const { cache, chargeur } = banc();
		await cache.get('a', chargeur('A').load);
		let fin!: (v: string) => void;
		const enCours = cache.get('b', () => new Promise<string>((resolve) => (fin = resolve)));
		cache.clear();
		fin('B');
		expect(await enCours).toBe('B');
		const a = chargeur('A2');
		const b = chargeur('B2');
		expect(await cache.get('a', a.load)).toBe('A2');
		expect(await cache.get('b', b.load)).toBe('B2');
	});
});
