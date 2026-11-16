// The sitemap the site serves itself (04.7-10, decision 5), at the request and not at the build: it
// follows the database served, so it covers the national switch and `app.scope set` with no
// regeneration and no 35 000 addresses in the repository. Four documents:
//   - `/sitemap.xml`                 the index: the pages, the departements, one file per indexable
//                                    departement;
//   - `/sitemaps/pages.xml`          the static pages that are indexable, no date;
//   - `/sitemaps/departements.xml`   the indexable departements, each with the day its data changed;
//   - `/sitemaps/communes-XX.xml`    the fiches of an indexable departement, with the day its fiches'
//                                    data changed.
// The territory, the indexing rule and the dates are the API's (`GET /v1/departements` and
// `/fiches`, plan 04.7-10 decisions 3 and 4): nothing is written here, and a date the API does not
// give is left out rather than guessed. Cost: one call for the index and `departements.xml`, two for
// a communes file (the list, shared, then its fiches), each read once an hour at most and one at a
// time per key (`createSitemapCache`). A failure is never kept in memory and never served as a
// document: the API's 429 or 503 is a 503 with `Retry-After` and `no-store`.
import { env } from '$env/dynamic/private';
import { ApiError } from '$lib/api/client';
import {
	getDepartementFiches,
	listDepartements,
	type DepartementRow,
	type FichesDepartement
} from '$lib/api/communes';
import { CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS } from '$lib/data/ce-que-nous-ne-montrons-pas';
import { DEFAULT_RETRY_AFTER_SECONDS, MAX_RETRY_AFTER_SECONDS } from '$lib/utils/etat-page';
import {
	createSitemapCache,
	dedupeEntries,
	isDepartementCode,
	isInseeCode,
	renderSitemapIndex,
	renderUrlset,
	SITEMAP_TTL_MS,
	SitemapError,
	toLastmod,
	type SitemapEntry
} from './sitemap';

/**
 * The static pages of the site that are indexable (none is `noindex`; the directory only is for a
 * search query, `sitemap-source.test.ts`). Not the directory filtered by departement, which stays
 * indexable and linked from the departement page but is not listed here.
 */
export const PAGES_STATIQUES: readonly string[] = [
	'/',
	'/communes',
	'/datasets',
	'/couverture',
	'/departements',
	'/developpeurs',
	CHEMIN_CE_QUE_NOUS_NE_MONTRONS_PAS,
	'/cgu',
	'/confidentialite',
	'/mentions-legales'
];

/** Life of a cached read in milliseconds: one hour, unless `SITEMAP_CACHE_TTL_MS` says otherwise (0: none, for the e2e). */
export function ttlDepuisEnv(valeur: string | undefined): number {
	return valeur !== undefined && /^\d+$/.test(valeur) ? Number(valeur) : SITEMAP_TTL_MS;
}

const ttlMs = ttlDepuisEnv(env.SITEMAP_CACHE_TTL_MS);
const cacheDepartements = createSitemapCache<DepartementRow[]>({ ttlMs });
// The communes files are cached rendered: whatever the rendering refuses (past 50 000 URLs) is
// refused inside the loader, hence never stored (Codex 3). Keyed by site and code.
const cacheCommunes = createSitemapCache<string>({ ttlMs });

/** Forgets every cached read (tests). */
export function viderCachesSitemap(): void {
	cacheDepartements.clear();
	cacheCommunes.clear();
}

export interface DependancesSitemap {
	/** Origin of the site, without a trailing slash. */
	site: string;
	/** The `fetch` of the request: SvelteKit's, so that the API is reached as the pages reach it. */
	fetch: typeof fetch;
}

/** The departement or the file asked for does not exist in the sitemap. */
export class SitemapIntrouvable extends Error {
	constructor() {
		super('Not in the sitemap');
		this.name = 'SitemapIntrouvable';
	}
}

/** The API answered, but with something a sitemap must not be built from: a defect of the source, not of the request. */
export class SitemapSourceInvalide extends Error {
	constructor(detail: string) {
		super(`Invalid answer of the API: ${detail}`);
		this.name = 'SitemapSourceInvalide';
	}
}

const estObjet = (valeur: unknown): valeur is Record<string, unknown> =>
	typeof valeur === 'object' && valeur !== null && !Array.isArray(valeur);

/**
 * The list of departements, or a refusal. A sitemap is complete or it is an error: one row whose code is
 * not a departement code, or without a boolean `indexable`, refuses the whole list. Nothing is dropped
 * in silence, so that no document is ever built from less than what the API said (Codex 1, Opus P3-1).
 * A date that cannot be written is not a refusal: it is left out of the document.
 */
export function validerDepartements(lignes: unknown): DepartementRow[] {
	if (!Array.isArray(lignes)) throw new SitemapSourceInvalide('the list of departements is not a list');
	lignes.forEach((ligne, rang) => {
		if (!estObjet(ligne) || !isDepartementCode(ligne.code) || typeof ligne.indexable !== 'boolean') {
			throw new SitemapSourceInvalide(`row ${rang} of the list of departements is not a departement code with a boolean indexable`);
		}
	});
	return lignes as DepartementRow[];
}

const TYPES_ZONE: ReadonlySet<unknown> = new Set(['commune', 'arrondissement']);

/**
 * The fiches of departement *code*, or a refusal: the answer is for that departement, every fiche is an
 * INSEE code of a commune or an arrondissement, and there is at least one (an empty file is never
 * served). One invalid fiche refuses the whole list.
 */
export function validerFiches(fiches: unknown, code: string): FichesDepartement {
	if (!estObjet(fiches) || fiches.code !== code || !Array.isArray(fiches.fiches)) {
		throw new SitemapSourceInvalide(`the fiches answer is not a list of fiches of departement ${code}`);
	}
	fiches.fiches.forEach((fiche, rang) => {
		if (!estObjet(fiche) || !isInseeCode(fiche.code_insee) || !TYPES_ZONE.has(fiche.type_zone)) {
			throw new SitemapSourceInvalide(`fiche ${rang} of departement ${code} is not an INSEE code of a commune or an arrondissement`);
		}
	});
	if (fiches.fiches.length === 0) throw new SitemapError('empty', `Departement ${code} has no fiche: an empty urlset is never served.`);
	return fiches as unknown as FichesDepartement;
}

// The read is validated by the loader of the cache, before it is stored: an invalid answer is never
// kept, and the next request asks the API again (Codex 2). The list feeds the index and
// departements.xml, whose rendering cannot refuse it (about a hundred rows, the pages always listed).
const lireDepartements = ({ fetch }: DependancesSitemap) =>
	cacheDepartements.get('departements', async () => validerDepartements(await listDepartements(fetch)));

// --- entries --------------------------------------------------------------------------------

export function entreesPages(site: string): SitemapEntry[] {
	return PAGES_STATIQUES.map((chemin) => ({ loc: chemin === '/' ? `${site}/` : `${site}${chemin}` }));
}

const indexables = (lignes: readonly DepartementRow[]) =>
	dedupeEntries(lignes.filter((l) => l.indexable).map((l) => ({ ...l, loc: l.code })));

/** The indexable departements, each with the day of its last import when the API gives a valid one. */
export function entreesDepartements(site: string, lignes: readonly DepartementRow[]): SitemapEntry[] {
	return indexables(lignes).map((l) => ({ loc: `${site}/departement/${l.code}`, lastmod: toLastmod(l.derniere_importation) }));
}

/** The index: pages, departements, then one file per indexable departement (none if none is). */
export function entreesIndex(site: string, lignes: readonly DepartementRow[]): SitemapEntry[] {
	const entrees: SitemapEntry[] = [{ loc: `${site}/sitemaps/pages.xml` }];
	const retenues = indexables(lignes);
	if (retenues.length === 0) return entrees;
	// The departements file is as recent as its most recent departement; the others carry no date
	// here (the day of their fiches is read with the fiches, which the index does not fetch).
	const dates = retenues.map((l) => toLastmod(l.derniere_importation)).filter((d): d is string => d !== null);
	const plusRecente = dates.length ? dates.reduce((a, b) => (a >= b ? a : b)) : null;
	entrees.push({ loc: `${site}/sitemaps/departements.xml`, lastmod: plusRecente });
	for (const ligne of retenues) entrees.push({ loc: `${site}/sitemaps/communes-${ligne.code}.xml` });
	return entrees;
}

/** The fiches of one departement (validated by `validerFiches`), each once, with the day of their data. */
export function entreesCommunes(site: string, fiches: FichesDepartement): SitemapEntry[] {
	const lastmod = toLastmod(fiches.derniere_importation);
	return dedupeEntries(fiches.fiches.map((f) => ({ loc: `${site}/commune/${f.code_insee}`, lastmod })));
}

// --- documents ------------------------------------------------------------------------------

export function documentPages(site: string): string {
	return renderUrlset(entreesPages(site));
}

export async function documentIndex(deps: DependancesSitemap): Promise<string> {
	return renderSitemapIndex(entreesIndex(deps.site, await lireDepartements(deps)));
}

/** The indexable departements; `SitemapIntrouvable` when none is, the index then listing no such file (Opus P3-2). */
export async function documentDepartements(deps: DependancesSitemap): Promise<string> {
	const entrees = entreesDepartements(deps.site, await lireDepartements(deps));
	if (entrees.length === 0) throw new SitemapIntrouvable();
	return renderUrlset(entrees);
}

/** The fiches of departement *code*; `SitemapIntrouvable` unless it is an indexable departement. */
export async function documentCommunes(deps: DependancesSitemap, code: string): Promise<string> {
	if (!isDepartementCode(code)) throw new SitemapIntrouvable();
	const ligne = (await lireDepartements(deps)).find((l) => l.code === code);
	if (!ligne || !ligne.indexable) throw new SitemapIntrouvable();
	try {
		return await cacheCommunes.get(`${deps.site} ${code}`, async () => {
			const fiches = validerFiches(await getDepartementFiches(code, deps.fetch), code);
			return renderUrlset(entreesCommunes(deps.site, fiches));
		});
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) throw new SitemapIntrouvable();
		throw err;
	}
}

// --- answers --------------------------------------------------------------------------------

const TEXTE = 'text/plain; charset=utf-8';

function texte(status: number, corps: string, entetes: Record<string, string> = {}): Response {
	return new Response(corps, { status, headers: { 'content-type': TEXTE, 'cache-control': 'no-store', ...entetes } });
}

/** `Retry-After` of a failed read: the delay the API served, within a day, else a minute. */
function delaiReessai(err: unknown): number {
	const servi = err instanceof ApiError ? err.retryAfter : null;
	return servi !== null && servi >= 1 ? Math.min(servi, MAX_RETRY_AFTER_SECONDS) : DEFAULT_RETRY_AFTER_SECONDS;
}

/**
 * The answer for a document being built: the XML, public for an hour, or an error that is never a
 * document. An unknown file is 404; a document the rules refuse (empty, past 50 000 URLs) is 500, our
 * defect; every other failure (API 429 or 503, network, an answer the API gave that a sitemap must not
 * be built from) is a 503 with `Retry-After`, and none of them is cached by the browser, the CDN or the
 * server.
 */
export async function reponseSitemap(document: Promise<string>): Promise<Response> {
	try {
		const xml = await document;
		return new Response(xml, {
			headers: { 'content-type': 'application/xml; charset=utf-8', 'cache-control': 'public, max-age=3600' }
		});
	} catch (err) {
		if (err instanceof SitemapIntrouvable) return texte(404, 'Sitemap introuvable.');
		console.error('[sitemap]', err);
		if (err instanceof SitemapError) return texte(500, 'Sitemap non produit.');
		return texte(503, 'Sitemap momentanément indisponible. Réessayez plus tard.', {
			'retry-after': String(delaiReessai(err))
		});
	}
}
