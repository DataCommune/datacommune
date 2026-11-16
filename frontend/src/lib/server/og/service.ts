// Answers of the link-preview routes (`/og/commune/{code}.png`, `/og/departement/{code}.png`).
// Three outcomes, kept apart:
//   - a card: the PNG of the fiche's served figures, cached a day by clients and an hour here;
//   - the generic image (static/og-image.png): the fiche serves no figure, is out of coverage, or its
//     source failed (then cached five minutes only, so that the card comes back with the source);
//   - an unknown or malformed code: a plain 404, never an image.
// One rendering per key at a time: concurrent requests for the same fiche share it (as R-07 for reads).
import { createHash } from 'node:crypto';
import generique from '../../../../static/og-image.png?hex';
import type { CarteOg } from '$lib/seo/og-carte';
import { rendreCarte } from './rendu';

/** What a route reads from its fiche: a card, no card (generic image), an outage, or an unknown code. */
export type LectureOg = { kind: 'carte'; carte: CarteOg } | { kind: 'sans-chiffre' } | { kind: 'panne' } | { kind: 'inconnu'; message: string };

interface Reponse {
	status: number;
	corps: Uint8Array | string;
	type: string;
	cache: string;
	etag: string | null;
	/** Milliseconds of rasterisation when this answer was rendered; null for the generic image or a 404. */
	rendu: number | null;
}

const CACHE_CARTE = 'public, max-age=86400, stale-while-revalidate=604800';
const CACHE_PANNE = 'public, max-age=300';
const CACHE_INCONNU = 'public, max-age=3600';

/** Lifetime in this process: a card an hour (as the JSON export), an outage a minute, a 404 ten. */
const TTL_MS = { carte: 3_600_000, panne: 60_000, inconnu: 600_000 } as const;
/** About 40 kB a card: 400 cards are some 16 MB. */
export const ENTREES_MAX = 400;

const GENERIQUE = Buffer.from(generique, 'hex');

function etagDe(octets: Uint8Array): string {
	return `"${createHash('sha256').update(octets).digest('base64url').slice(0, 22)}"`;
}

const ETAG_GENERIQUE = etagDe(GENERIQUE);

const cache = new Map<string, { reponse: Reponse; expire: number }>();
const enCours = new Map<string, Promise<Reponse>>();

/** Empties the cache of this process (tests). */
export function viderCacheOg(): void {
	cache.clear();
	enCours.clear();
}

function retenir(cle: string, reponse: Reponse, ttl: number): void {
	cache.delete(cle);
	cache.set(cle, { reponse, expire: Date.now() + ttl });
	// A Map iterates in insertion order: the first key is the least recently used.
	while (cache.size > ENTREES_MAX) cache.delete(cache.keys().next().value!);
}

function imageGenerique(cacheHttp: string): Reponse {
	return { status: 200, corps: GENERIQUE, type: 'image/png', cache: cacheHttp, etag: ETAG_GENERIQUE, rendu: null };
}

async function produire(lire: () => Promise<LectureOg>): Promise<{ reponse: Reponse; ttl: number }> {
	let lecture: LectureOg;
	try {
		lecture = await lire();
	} catch (erreur) {
		console.error('og: reading failed', erreur);
		return { reponse: imageGenerique(CACHE_PANNE), ttl: TTL_MS.panne };
	}
	if (lecture.kind === 'inconnu') {
		return {
			reponse: { status: 404, corps: lecture.message, type: 'text/plain; charset=utf-8', cache: CACHE_INCONNU, etag: null, rendu: null },
			ttl: TTL_MS.inconnu
		};
	}
	if (lecture.kind === 'panne') return { reponse: imageGenerique(CACHE_PANNE), ttl: TTL_MS.panne };
	if (lecture.kind === 'sans-chiffre') return { reponse: imageGenerique(CACHE_CARTE), ttl: TTL_MS.carte };
	try {
		const { png, ms } = await rendreCarte(lecture.carte);
		return { reponse: { status: 200, corps: png, type: 'image/png', cache: CACHE_CARTE, etag: etagDe(png), rendu: ms }, ttl: TTL_MS.carte };
	} catch (erreur) {
		// A drawing that fails is a defect of this code, not of the fiche: the generic image, briefly.
		console.error('og: rendering failed', erreur);
		return { reponse: imageGenerique(CACHE_PANNE), ttl: TTL_MS.panne };
	}
}

/**
 * The answer for `cle`: from the cache of this process when fresh, else read and drawn once however
 * many requests ask at the same time. `si` is the `If-None-Match` of the request.
 */
export async function servirOg(cle: string, lire: () => Promise<LectureOg>, si: string | null = null): Promise<Response> {
	const retenue = cache.get(cle);
	let reponse: Reponse;
	let source: 'cache' | 'rendu';
	if (retenue && retenue.expire > Date.now()) {
		retenir(cle, retenue.reponse, retenue.expire - Date.now()); // most recently used again
		reponse = retenue.reponse;
		source = 'cache';
	} else {
		let promesse = enCours.get(cle);
		if (!promesse) {
			promesse = produire(lire)
				.then(({ reponse: produite, ttl }) => {
					retenir(cle, produite, ttl);
					return produite;
				})
				.finally(() => enCours.delete(cle));
			enCours.set(cle, promesse);
		}
		reponse = await promesse;
		source = 'rendu';
	}

	const entetes: Record<string, string> = { 'content-type': reponse.type, 'cache-control': reponse.cache };
	if (reponse.etag) entetes.etag = reponse.etag;
	// The cost of the drawing, readable in the browser tools and by a probe (`curl -I`).
	entetes['server-timing'] = source === 'cache' || reponse.rendu === null ? `og;desc="${source}"` : `og;desc="rendu";dur=${reponse.rendu.toFixed(1)}`;
	if (reponse.etag && si === reponse.etag) return new Response(null, { status: 304, headers: entetes });
	return new Response(reponse.corps as BodyInit, { status: reponse.status, headers: entetes });
}
